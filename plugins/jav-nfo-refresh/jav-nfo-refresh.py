#!/usr/bin/env python3
"""
jav-nfo-refresh.py -- Stash plugin entry point.

Refreshes Japanese-source metadata for every scene carrying the trigger
tag: looks the scene's studio code up on javstash.org, libredmm.com and/or
jav321.com (fixed priority order, first hit wins), writes the Japanese
title/description into the scene's custom fields, optionally translates
them into the scene's title/details, turns the scraped genres into tags,
and mirrors all of it into the .nfo sidecar next to the video file.

Invoked by Stash with a JSON payload on stdin:
  {"server_connection": {...}, "args": {"mode": "scrape"|"translate_only",
                                        "dry_run": bool, "rescrape": bool,
                                        "retranslate": bool}}
The task definitions in jav-nfo-refresh.yml supply those args; everything
else comes from the plugin's settings (Settings -> Plugins).

The actual scraping/translation/NFO code lives in jav_nfo_core.py, lifted
from the standalone jav_nfo_refresh_unified.py CLI.
"""

import copy
import json
import logging
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import stashapi.log as slog
from stashapi.stashapp import StashInterface

import jav_nfo_core as core

PLUGIN_ID = "jav-nfo-refresh"
PLUGIN_VERSION = "1.1.1"

DEFAULT_TRIGGER_TAG = "JAV NFO Refresh"
DEFAULT_ORIGINAL_TITLE_FIELD = "Original Title"
DEFAULT_ORIGINAL_DESCRIPTION_FIELD = "Original Description"
# Custom field holding the ISO timestamp of the last title/details
# translation -- the Stash-side twin of the NFO's <title_translated>.
TRANSLATED_AT_FIELD = "Translated At"
# Used for <stash_ids> when Stash has no javstash stash-box configured.
JAVSTASH_ENDPOINT_FALLBACK = "https://javstash.org/graphql"

TRANSLATION_ENGINES = {
    "deep-translator": ("deep-translator", "nmt"),
    "cloud-nmt": ("cloud", "nmt"),
    "cloud-llm": ("cloud", "translation-llm"),
}

SCENE_FRAGMENT = """
    id title code details date director urls custom_fields
    tags { id name }
    performers { name }
    stash_ids { endpoint stash_id }
    files { path }
"""

log = logging.getLogger(PLUGIN_ID)


# ---------------------------------------------------------------------------
# Configuration: plugin settings + task args
# ---------------------------------------------------------------------------

PLUGIN_DIR = Path(__file__).resolve().parent


def resolve_plugin_path(value: str) -> Optional[str]:
    """A file setting: "~" is expanded and a relative path is taken relative
    to the plugin's own directory (the working directory Stash starts the
    process in is not something to rely on)."""
    if not value:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = PLUGIN_DIR / path
    return str(path)

class Config:
    def __init__(self, settings: Dict[str, Any], args: Dict[str, Any]):
        def text(key: str, default: str = "") -> str:
            value = settings.get(key)
            return (value.strip() if isinstance(value, str) else "") or default

        def flag(key: str) -> bool:
            return bool(settings.get(key))

        def number(key: str) -> Optional[float]:
            value = settings.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None
            return float(value)

        self.raw_settings = dict(settings)
        self.trigger_tag = text("triggerTag", DEFAULT_TRIGGER_TAG)
        self.remove_trigger_tag = flag("removeTriggerTag")
        self.sources = core.parse_source_list(text("sources", "all"))
        self.javstash_api_key = text("javstashApiKey")
        delay = number("requestDelay")
        self.delay = delay if delay is not None else core.DEFAULT_DELAY
        wait = number("libredmmProcessingWait")
        if wait is None:
            wait = (core.PROCESSING_WAIT_SOLE_SOURCE
                    if self.sources[0] == core.SOURCE_LIBREDMM
                    else core.PROCESSING_WAIT_BOTH)
        self.processing_wait = wait
        self.update_release_date = flag("updateReleaseDate")

        self.translate = not flag("disableTranslation")
        self.translation_engine = text("translationEngine", "deep-translator").lower()
        self.cloud_api_key = text("cloudApiKey") or None
        self.cloud_project_id = text("cloudProjectId") or None
        self.cloud_region = text("cloudRegion", "us-central1")
        self.cloud_credentials_file = resolve_plugin_path(text("cloudCredentialsFile"))

        self.update_stash = not flag("disableStashUpdate")
        self.write_nfo = not flag("disableNfoWrite")
        self.nfo_backup = not flag("disableNfoBackup")
        self.plain_title = flag("plainTitle")
        self.mark_failed = not flag("disableFailedTag")
        self.original_title_field = text("originalTitleField", DEFAULT_ORIGINAL_TITLE_FIELD)
        self.original_description_field = text("originalDescriptionField",
                                               DEFAULT_ORIGINAL_DESCRIPTION_FIELD)

        self.mode = (args.get("mode") or "scrape").strip().lower()
        self.dry_run = bool(args.get("dry_run"))
        self.rescrape = bool(args.get("rescrape"))
        self.retranslate = bool(args.get("retranslate"))

        # Consumed by the core's fetchers / NFO helpers.
        self.search_limit = core.DEFAULT_SEARCH_LIMIT
        self.backup_mode = "once" if self.nfo_backup else "skip"
        self.no_backup = not self.nfo_backup

    def validate(self) -> None:
        if self.mode not in ("scrape", "translate_only", "check"):
            raise SystemExit(f"Unknown task mode {self.mode!r}.")
        if not self.update_stash and not self.write_nfo:
            raise SystemExit("Both 'Disable Stash updates' and 'Disable NFO writes' are "
                             "on -- there is nowhere to write results.")
        if self.translation_engine not in TRANSLATION_ENGINES:
            raise SystemExit(f"Unknown translation engine {self.translation_engine!r} -- "
                             f"choose one of {', '.join(TRANSLATION_ENGINES)}.")
        if self.mode == "translate_only" and not self.translate:
            raise SystemExit("The re-translate task needs translation, but 'Disable "
                             "translation' is on.")


# ---------------------------------------------------------------------------
# Stash helpers
# ---------------------------------------------------------------------------

class TagCache:
    """Case-insensitive name -> id map over every tag (and alias) in Stash,
    loaded once per run; creates missing tags on demand."""

    def __init__(self, stash: StashInterface, dry_run: bool):
        self.stash = stash
        self.dry_run = dry_run
        self._ids: Dict[str, str] = {}
        for tag in stash.find_tags(fragment="id name aliases") or []:
            self._ids.setdefault(tag["name"].strip().lower(), tag["id"])
            for alias in tag.get("aliases") or []:
                self._ids.setdefault(alias.strip().lower(), tag["id"])

    def get(self, name: str) -> Optional[str]:
        return self._ids.get(name.strip().lower())

    def ensure(self, name: str) -> Optional[str]:
        tag_id = self.get(name)
        if tag_id or self.dry_run:
            if not tag_id:
                log.info("[dry-run] would create tag %r", name)
            return tag_id
        created = self.stash.create_tag({"name": name.strip()})
        tag_id = created["id"]
        self._ids[name.strip().lower()] = tag_id
        log.info("created tag %r (id %s)", name, tag_id)
        return tag_id


def scene_file_path(scene: Dict[str, Any]) -> Optional[Path]:
    for f in scene.get("files") or []:
        if f.get("path"):
            return Path(f["path"])
    return None


def scene_label(scene: Dict[str, Any]) -> str:
    path = scene_file_path(scene)
    return f"scene {scene['id']} ({path.name if path else 'no file'})"


def javstash_endpoint_and_key(stash: StashInterface, cfg: Config) -> Tuple[str, Optional[str]]:
    """The javstash stash-box as configured in Stash: its endpoint URL is
    what <stash_ids> are keyed by, and its API key is the fallback when the
    plugin setting is blank."""
    endpoint, api_key = JAVSTASH_ENDPOINT_FALLBACK, None
    try:
        for box in stash.get_stashbox_connections() or []:
            if "javstash" in (box.get("endpoint") or "").lower():
                endpoint = box["endpoint"]
                api_key = (box.get("api_key") or "").strip() or None
                break
    except Exception as exc:  # the lookup is a convenience, never fatal
        log.warning("could not read stash-box connections (%s)", exc)
    return endpoint, cfg.javstash_api_key or api_key


def build_clients(cfg: Config, javstash_api_key: Optional[str]) -> Dict[str, Any]:
    clients: Dict[str, Any] = {}
    if core.SOURCE_JAVSTASH in cfg.sources:
        if not javstash_api_key:
            raise SystemExit("javstash is a selected source but no API key is available: "
                             "set the 'javstash API key' plugin setting, or configure "
                             "javstash as a stash-box in Settings -> Metadata Providers.")
        clients[core.SOURCE_JAVSTASH] = core.JavstashClient(
            core.JAVSTASH_DEFAULT_ENDPOINT, javstash_api_key, delay=cfg.delay)
    if core.SOURCE_LIBREDMM in cfg.sources:
        clients[core.SOURCE_LIBREDMM] = core.LibredmmClient(
            core.LIBREDMM_DEFAULT_BASE_URL, delay=cfg.delay,
            processing_wait=cfg.processing_wait)
    if core.SOURCE_JAV321 in cfg.sources:
        clients[core.SOURCE_JAV321] = core.Jav321Client(
            core.JAV321_DEFAULT_BASE_URL, delay=cfg.delay)
    return clients


def build_translator(cfg: Config) -> Optional[core.BaseTranslator]:
    if not cfg.translate:
        return None
    backend, model = TRANSLATION_ENGINES[cfg.translation_engine]
    project_id = cfg.cloud_project_id
    if model == "translation-llm" and not project_id and cfg.cloud_credentials_file:
        # A service account key file names its project; save the user a setting.
        try:
            with open(cfg.cloud_credentials_file, encoding="utf-8") as f:
                project_id = (json.load(f).get("project_id") or "").strip() or None
        except (OSError, ValueError) as exc:
            log.warning("could not read project_id from %s (%s)", cfg.cloud_credentials_file, exc)
    return core.build_translator(
        backend, cfg.cloud_api_key, cloud_model=model,
        cloud_project_id=project_id, cloud_region=cfg.cloud_region,
        cloud_credentials_file=cfg.cloud_credentials_file,
    )


# ---------------------------------------------------------------------------
# One scene = Stash record + optional NFO sidecar
# ---------------------------------------------------------------------------

class NfoWriteError(Exception):
    """The .nfo could not be written; nothing was changed in Stash either."""


class SceneContext:
    """Everything the per-scene logic needs, plus the pending writes."""

    def __init__(self, scene: Dict[str, Any], cfg: Config, run: "Run"):
        self.scene = scene
        self.cfg = cfg
        self.run = run
        self.path = scene_file_path(scene)
        self.custom = dict(scene.get("custom_fields") or {})
        self.tag_names = {(t.get("name") or "").strip().lower() for t in scene.get("tags") or []}
        self.tag_ids: List[str] = [t["id"] for t in scene.get("tags") or []]
        self.update: Dict[str, Any] = {}        # sceneUpdate input, minus id
        self.custom_partial: Dict[str, str] = {}
        self.nfo_path: Optional[Path] = None
        self.tree: Optional[ET.ElementTree] = None
        self.root: Optional[ET.Element] = None
        self.nfo_before: Optional[bytes] = None

    # -- loading ----------------------------------------------------------

    def load_nfo(self) -> Optional[str]:
        """Parse the sidecar when NFO writes are enabled. Returns an error
        status string if the file exists but cannot be parsed."""
        if not self.cfg.write_nfo or self.path is None:
            return None
        self.nfo_path = self.path.with_suffix(".nfo")
        if not self.nfo_path.exists():
            log.info("%s: no .nfo sidecar at %s -- Stash only.", self.label, self.nfo_path.name)
            self.nfo_path = None
            return None
        try:
            self.tree = ET.parse(self.nfo_path)
        except ET.ParseError as exc:
            log.error("%s: %s is not valid XML (%s) -- skipping scene.",
                      self.label, self.nfo_path.name, exc)
            return f"parse_error: {exc}"
        self.root = self.tree.getroot()
        self.nfo_before = self.nfo_snapshot()
        # Probe write access up front (open for update, touch nothing): a
        # read-only share would otherwise only surface after the sources
        # were queried and the translation paid for.
        try:
            with open(self.nfo_path, "r+b"):
                pass
        except OSError as exc:
            log.error("%s: %s is not writable (%s) -- skipping scene. Fix the share "
                      "or turn on 'Disable NFO writes'.", self.label, self.nfo_path.name,
                      exc.strerror or exc)
            return f"nfo_not_writable: {exc.strerror or exc}"
        return None

    def nfo_snapshot(self) -> bytes:
        """The NFO as write_nfo would serialize it. Re-inserted elements lose
        their whitespace tails, so a raw tostring() of the live tree can
        differ from the original without any real change."""
        clone = copy.deepcopy(self.root)
        ET.indent(clone, space="  ")
        return ET.tostring(clone)

    @property
    def label(self) -> str:
        return scene_label(self.scene)

    # -- reads ------------------------------------------------------------

    def nfo_text(self, tag: str) -> Optional[str]:
        return core.existing_text(self.root, tag) if self.root is not None else None

    def existing_original_title(self) -> Optional[str]:
        return (self.custom.get(self.cfg.original_title_field) or "").strip() \
            or self.nfo_text("originaltitle")

    def existing_original_description(self) -> Optional[str]:
        return (self.custom.get(self.cfg.original_description_field) or "").strip() \
            or self.nfo_text("original_plot")

    def code(self) -> Optional[str]:
        """Studio code: Stash's own field first, then the NFO tagline, then
        the filename."""
        code = core.normalize_code(self.scene.get("code") or "")
        if not code and self.root is not None:
            code = core.extract_code_from_nfo(self.root)
        if not code and self.path is not None:
            code = core.extract_code_from_filename(self.path)
        return code

    def refreshed_by(self) -> List[str]:
        """Sources that already refreshed this scene, judged from BOTH
        stores: the marker tag in Stash, the marker genre (or legacy
        <stashid>) in the NFO, or a javstash stash_id on the scene."""
        found = []
        for source in core.SOURCE_PRIORITY:
            marker = core.REFRESHED_GENRES[source].lower()
            hit = marker in self.tag_names
            if not hit and self.root is not None:
                hit = core.source_already_refreshed(self.root, source)
            if not hit and source == core.SOURCE_JAVSTASH:
                hit = any(s.get("endpoint") == self.run.javstash_endpoint
                          for s in self.scene.get("stash_ids") or [])
            if hit:
                found.append(source)
        return found

    def was_translated(self) -> bool:
        """Any sign of a previous translation: the NFO markers, the
        Translated At custom field, or an English title sitting next to a
        Japanese Original Title (scenes imported from already-translated
        NFOs by nfoSceneParser carry no marker of their own)."""
        if self.root is not None and core.was_already_translated(self.root):
            return True
        if (self.custom.get(TRANSLATED_AT_FIELD) or "").strip():
            return True
        original = (self.custom.get(self.cfg.original_title_field) or "").strip()
        title = (self.scene.get("title") or "").strip()
        return bool(original and title and title != original
                    and not core.JAPANESE_RE.search(title))

    def actor_string(self) -> str:
        names = [(p.get("name") or "").strip() for p in self.scene.get("performers") or []]
        names = [n for n in names if n]
        if not names and self.root is not None:
            names = core.get_actor_names(self.root)
        return ", ".join(names)

    # -- writes (buffered; nothing touches Stash or disk until commit) ----

    def set_original_fields(self, title_jp: Optional[str], details_jp: Optional[str]) -> None:
        if title_jp:
            self.set_custom(self.cfg.original_title_field, title_jp)
            if self.root is not None:
                core.set_text_insert_after(self.root, "originaltitle", title_jp, after_tags=["title"])
        if details_jp:
            self.set_custom(self.cfg.original_description_field, details_jp)
            if self.root is not None:
                core.set_text_insert_after(self.root, "original_plot", details_jp,
                                           after_tags=["plot", "originaltitle"])

    def set_custom(self, field: str, value: str) -> None:
        if (self.custom.get(field) or "") != value:
            self.custom_partial[field] = value

    def set_stash_field(self, field: str, value: Optional[str]) -> None:
        if value is not None and (self.scene.get(field) or "") != value:
            self.update[field] = value

    def set_javstash_id(self, stash_id: str) -> None:
        endpoint = self.run.javstash_endpoint
        current = [dict(s) for s in self.scene.get("stash_ids") or []]
        wanted = [s for s in current if s.get("endpoint") != endpoint]
        wanted.append({"endpoint": endpoint, "stash_id": stash_id})
        if wanted != current:
            self.update["stash_ids"] = wanted
        if self.root is not None:
            core.set_attributed_tag_insert_after(
                self.root, "stashid", {"endpoint": core.JAVSTASH_ENDPOINT_NAME}, stash_id,
                after_tags=["tagline", "id", "datasource"])

    def set_urls(self, urls: List[str]) -> None:
        if urls and list(self.scene.get("urls") or []) != urls:
            self.update["urls"] = urls
        if urls and self.root is not None:
            core.set_repeated_tag(self.root, "URL", urls,
                                  after_tags=["stashid", "tagline", "id", "datasource"])

    def set_release_date(self, date: str) -> None:
        self.set_stash_field("date", date)
        if self.root is not None:
            core.set_text_insert_after(self.root, "premiered", date,
                                       after_tags=["original_plot", "plot", "originaltitle"])
            core.set_text_insert_after(self.root, "releasedate", date, after_tags=["premiered"])

    def set_director(self, director: str) -> None:
        self.set_stash_field("director", director)
        if self.root is not None:
            core.set_text_insert_after(self.root, "director", director,
                                       after_tags=["releasedate", "premiered"])

    def set_translated_title(self, title: str) -> None:
        self.set_stash_field("title", title)
        self.set_custom(TRANSLATED_AT_FIELD, self.run.timestamp)
        if self.root is not None:
            title_el = self.root.find("title")
            old = title_el.text if title_el is not None else None
            if self.cfg.nfo_backup and old is not None and self.root.find("title_backup") is None:
                core.set_text_insert_after(self.root, "title_backup", old, after_tags=["title"])
            core.stamp_translated_marker(self.root)
            if title_el is None:
                title_el = ET.Element("title")
                self.root.insert(0, title_el)
            title_el.text = title

    def set_translated_plot(self, plot: str) -> None:
        self.set_stash_field("details", plot)
        self.set_custom(TRANSLATED_AT_FIELD, self.run.timestamp)
        if self.root is not None:
            core.set_text_insert_after(self.root, "plot", plot, after_tags=["title"])
            core.stamp_translated_marker(self.root)

    def add_tags(self, names: List[str]) -> List[str]:
        """Attach tags (creating them if needed) and mirror them as NFO
        genres. Returns the names that were actually new."""
        added = []
        for name in names:
            new = False
            tag_id = self.run.tags.ensure(name)
            if tag_id and tag_id not in self.tag_ids:
                self.tag_ids.append(tag_id)
                new = True
            elif not tag_id and name.lower() not in self.tag_names:
                new = True  # dry run: would be created and attached
            if self.root is not None and core.add_genre(self.root, name, after_tags=core.GENRE_AFTER_TAGS):
                new = True
            if new:
                added.append(name)
        return added

    def remove_tag(self, name: str) -> None:
        tag_id = self.run.tags.get(name)
        if tag_id and tag_id in self.tag_ids:
            self.tag_ids.remove(tag_id)
        if self.root is not None:
            core.remove_genre(self.root, name)

    # -- commit -----------------------------------------------------------

    def nfo_changed(self) -> bool:
        return self.root is not None and self.nfo_snapshot() != self.nfo_before

    def stash_changed(self) -> bool:
        if not self.cfg.update_stash:
            return False
        original_ids = [t["id"] for t in self.scene.get("tags") or []]
        return bool(self.update or self.custom_partial
                    or sorted(self.tag_ids) != sorted(original_ids))

    def data_changed(self) -> bool:
        """A substantive change (anything except marker/trigger tags)."""
        return bool(self.update or self.custom_partial or self.nfo_changed())

    def finish_trigger_tag(self) -> None:
        if self.cfg.remove_trigger_tag:
            tag_id = self.run.trigger_tag_id
            if tag_id in self.tag_ids:
                self.tag_ids.remove(tag_id)

    def commit(self, what: str) -> None:
        stash_changed, nfo_changed = self.stash_changed(), self.nfo_changed()
        if not stash_changed and not nfo_changed:
            return
        summary = []
        if stash_changed:
            fields = sorted(self.update) + [f"custom:{k}" for k in sorted(self.custom_partial)]
            if sorted(self.tag_ids) != sorted(t["id"] for t in self.scene.get("tags") or []):
                fields.append("tags")
            summary.append("Stash[" + ", ".join(fields) + "]")
        if nfo_changed:
            summary.append(f"NFO[{self.nfo_path.name}]")
        if self.cfg.dry_run:
            log.info("%s: [dry-run] would update %s -- %s", self.label, " + ".join(summary), what)
            return
        if nfo_changed:
            try:
                core.backup_nfo_file(self.nfo_path, self.cfg.backup_mode)
                core.write_nfo(self.tree, self.nfo_path)
            except OSError as exc:
                # Leave Stash alone too, so the two stores don't drift apart.
                raise NfoWriteError(f"{self.nfo_path.name}: {exc.strerror or exc}") from exc
        if stash_changed:
            payload = {"id": self.scene["id"], **self.update}
            if self.custom_partial:
                payload["custom_fields"] = {"partial": self.custom_partial}
            if sorted(self.tag_ids) != sorted(t["id"] for t in self.scene.get("tags") or []):
                payload["tag_ids"] = self.tag_ids
            self.run.stash.update_scene(payload)
        log.info("%s: updated %s -- %s", self.label, " + ".join(summary), what)


# ---------------------------------------------------------------------------
# Per-scene processing
# ---------------------------------------------------------------------------

def pick_field(field: str, data: Dict[str, core.SourceData], protected: List[str],
               existing: Callable[[], Optional[str]]) -> Optional[str]:
    """Same rule as the CLI's merge_field: SOURCE_PRIORITY order, the winner
    contributes what it returned, a protected (higher-priority, previously
    refreshed) source contributes what the scene already holds."""
    for source in core.SOURCE_PRIORITY:
        if source in data:
            value = getattr(data[source], field)
            if value:
                return value
        elif source in protected:
            value = existing()
            if value:
                return value
    return None


def translate_genre_names(translator: core.BaseTranslator, genres_jp: List[str],
                          label: str) -> List[str]:
    wanted = [g.strip() for g in genres_jp if g and g.strip() and g.strip() not in core.GENRE_SKIP]
    translations = core.translate_genres(translator, wanted)
    names = []
    for genre_jp in wanted:
        translated = translations.get(genre_jp)
        if translated is None:
            log.warning("%s: genre translation failed for %r -- skipping that genre.", label, genre_jp)
        elif translated not in names:
            names.append(translated)
    return names


def process_scene_scrape(ctx: SceneContext, run: "Run") -> core.ProcessResult:
    cfg = ctx.cfg
    code = ctx.code()
    if not code:
        log.warning("%s: no studio code in Stash, NFO tagline or filename -- skipping.", ctx.label)
        return core.ProcessResult("no_code", detail="no recognizable studio code")

    refreshed_by = ctx.refreshed_by()
    data: Dict[str, core.SourceData] = {}
    outcomes: Dict[str, str] = {}
    miss_details: List[str] = []
    stopped_at: Optional[str] = None
    for source in cfg.sources:
        if not cfg.rescrape and source in refreshed_by:
            stopped_at = source
            break
        try:
            sd, miss_detail = core.FETCHERS[source](run.clients[source], code, cfg)
        except RuntimeError as exc:
            outcomes[source] = "error"
            miss_details.append(f"{source}: API error: {exc}")
            log.error("%s: %s lookup failed (%s).", ctx.label, source, exc)
            continue
        if sd is None:
            outcomes[source] = "miss"
            miss_details.append(f"{source}: {miss_detail}")
            log.warning("%s: no %s match for %s -- %s", ctx.label, source, code, miss_detail)
            continue
        outcomes[source] = "hit"
        data[source] = sd
        break

    if not data:
        if stopped_at is not None:
            log.info("%s: already refreshed from %s -- nothing new (use the Rescrape task "
                     "to re-query).", ctx.label, stopped_at)
            return core.ProcessResult("already_refreshed", code=code, outcomes=outcomes)
        status = "api_error" if "error" in outcomes.values() else "no_match"
        return core.ProcessResult(status, code=code, detail="; ".join(miss_details), outcomes=outcomes)

    winner = next(iter(data.values()))
    protected = [s for s in core.SOURCE_PRIORITY[:core.SOURCE_PRIORITY.index(winner.source)]
                 if s in refreshed_by]

    js = data.get(core.SOURCE_JAVSTASH)
    if js and js.id:
        ctx.set_javstash_id(js.id)

    new_urls = list(dict.fromkeys(winner.urls))
    if new_urls:
        if any(s != winner.source for s in refreshed_by):
            existing_urls = list(ctx.scene.get("urls") or [])
            if not existing_urls and ctx.root is not None:
                existing_urls = [el.text for el in ctx.root.findall("URL") if el.text]
            merged = existing_urls + [u for u in new_urls if u not in existing_urls]
        else:
            merged = new_urls
        ctx.set_urls(merged)

    title_jp = pick_field("title", data, protected, ctx.existing_original_title)
    old_details_jp = ctx.existing_original_description()
    details_jp = pick_field("details", data, protected, ctx.existing_original_description)
    ctx.set_original_fields(title_jp, details_jp)
    plot_is_new = bool(details_jp) and details_jp != old_details_jp

    if cfg.update_release_date:
        release_date = pick_field("release_date", data, protected,
                                  lambda: ctx.scene.get("date") or ctx.nfo_text("premiered"))
        if release_date:
            ctx.set_release_date(release_date)

    director = pick_field("director", data, protected,
                          lambda: ctx.scene.get("director") or ctx.nfo_text("director"))
    if director:
        ctx.set_director(director)

    if run.translator:
        full_retranslate = cfg.retranslate or not ctx.was_translated()
        if full_retranslate and title_jp:
            translate_title(ctx, code, title_jp, run.translator)
        if details_jp and (full_retranslate or plot_is_new):
            if plot_is_new and not full_retranslate:
                log.info("%s: new description from %s -- translating it although the "
                         "scene was already translated.", ctx.label, winner.source)
            translated_plot = run.translator.translate_ja_to_en(details_jp)
            if translated_plot:
                ctx.set_translated_plot(translated_plot)
            else:
                log.warning("%s: description translation failed -- details left untouched.", ctx.label)
        if not full_retranslate:
            log.info("%s: already translated -- leaving the title%s alone; genres still refreshed.",
                     ctx.label, "" if plot_is_new else "/details")
        if winner.genres:
            names = translate_genre_names(run.translator, winner.genres, ctx.label)
            added = ctx.add_tags(names)
            if added:
                log.info("%s: adding genre tag(s) %s", ctx.label, added)

    if not ctx.data_changed():
        if any(sd.has_any_data() for sd in data.values()):
            log.info("%s: %s returned nothing the scene didn't already have.", ctx.label, sorted(data))
            ctx.finish_trigger_tag()
            ctx.commit("trigger tag only")
            return core.ProcessResult("unchanged", code=code, outcomes=outcomes)
        ctx.finish_trigger_tag()
        ctx.commit("trigger tag only")
        return core.ProcessResult("no_changes", code=code, outcomes=outcomes,
                                  detail=f"matched on {sorted(data)} but returned no usable fields")

    ctx.add_tags([core.REFRESHED_GENRES[s] for s in list(data) + refreshed_by])
    ctx.remove_tag(core.FAILED_GENRE)
    ctx.finish_trigger_tag()
    ids = ", ".join(f"{s}={sd.id}" for s, sd in data.items())
    ctx.commit(f"from {sorted(data)} (code={code}, {ids})")
    return core.ProcessResult("would_update" if cfg.dry_run else "updated", code=code, outcomes=outcomes)


def translate_title(ctx: SceneContext, code: str, title_jp: str,
                    translator: core.BaseTranslator) -> bool:
    translated = translator.translate_ja_to_en(title_jp)
    if translated is None:
        log.warning("%s: title translation failed -- title left untouched.", ctx.label)
        return False
    translated = core.to_title_case(translated)
    if ctx.cfg.plain_title:
        new_title = translated
    else:
        old_title = ctx.scene.get("title") or ctx.nfo_text("title")
        group = core.detect_group(ctx.path, existing_title=old_title) if ctx.path else None
        new_title = core.build_new_title(ctx.actor_string(), group, translated, code)
    ctx.set_translated_title(new_title)
    return True


def process_scene_translate_only(ctx: SceneContext, run: "Run") -> core.ProcessResult:
    cfg = ctx.cfg
    if not cfg.retranslate and ctx.was_translated():
        log.info("%s: already translated -- skipping.", ctx.label)
        ctx.finish_trigger_tag()
        ctx.commit("trigger tag only")
        return core.ProcessResult("already_translated")

    title_jp = ctx.existing_original_title()
    details_jp = ctx.existing_original_description()
    if not title_jp and not details_jp:
        log.warning("%s: no Original Title / Original Description to translate -- skipping.", ctx.label)
        return core.ProcessResult("no_source_text", detail="nothing to translate")

    code = ctx.code()
    if not code:
        log.warning("%s: no studio code -- needed for the [Code] slot in the title.", ctx.label)
        return core.ProcessResult("no_code", detail="no recognizable studio code")

    changed = False
    if title_jp and translate_title(ctx, code, title_jp, run.translator):
        changed = True
    if details_jp:
        translated_plot = run.translator.translate_ja_to_en(details_jp)
        if translated_plot:
            ctx.set_translated_plot(translated_plot)
            changed = True
        else:
            log.warning("%s: description translation failed -- details left untouched.", ctx.label)
    if not changed:
        return core.ProcessResult("no_changes", code=code,
                                  detail="translation failed for all available source text")
    ctx.finish_trigger_tag()
    ctx.commit(f"re-translated (code={code})")
    return core.ProcessResult("would_update" if cfg.dry_run else "updated", code=code)


def mark_failed(ctx: SceneContext) -> bool:
    """Tag a scene no source could match so failures are filterable. The
    only thing written to such a scene; cleared by any later success."""
    added = ctx.add_tags([core.FAILED_GENRE])
    if not added:
        return False
    ctx.commit(f"tagged '{core.FAILED_GENRE}'")
    return not ctx.cfg.dry_run


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class Run:
    def __init__(self, stash: StashInterface, cfg: Config):
        self.stash = stash
        self.cfg = cfg
        self.timestamp = datetime.now().isoformat(timespec="seconds")
        self.counts: Dict[str, int] = {}
        self.javstash_endpoint, api_key = javstash_endpoint_and_key(stash, cfg)
        self.tags = TagCache(stash, cfg.dry_run)
        self.trigger_tag_id = self.tags.get(cfg.trigger_tag)
        self.translator = build_translator(cfg)
        self.clients = build_clients(cfg, api_key) if cfg.mode == "scrape" else {}

    def tagged_scenes(self) -> List[Dict[str, Any]]:
        if not self.trigger_tag_id:
            raise SystemExit(f"Trigger tag {self.cfg.trigger_tag!r} does not exist in Stash -- "
                             "create it and tag the scenes to process.")
        scenes = self.stash.find_scenes(
            f={"tags": {"value": [self.trigger_tag_id], "modifier": "INCLUDES", "depth": 0}},
            fragment=SCENE_FRAGMENT,
        ) or []
        scenes.sort(key=lambda s: str(scene_file_path(s) or ""))
        return scenes

    def process(self, scene: Dict[str, Any]) -> core.ProcessResult:
        ctx = SceneContext(scene, self.cfg, self)
        if ctx.path is None:
            log.warning("%s: has no file -- skipping.", ctx.label)
            return core.ProcessResult("no_file")
        error = ctx.load_nfo()
        if error:
            status = error.split(":", 1)[0]
            return core.ProcessResult(status, detail=error)
        if self.cfg.mode == "translate_only":
            return process_scene_translate_only(ctx, self)
        result = process_scene_scrape(ctx, self)
        if self.cfg.mark_failed and result.status in core.NOT_FOUND_STATUSES:
            if mark_failed(ctx):
                self.counts["marked_failed"] = self.counts.get("marked_failed", 0) + 1
        return result

    def main(self) -> None:
        scenes = self.tagged_scenes()
        translate_desc = "off"
        if self.translator:
            translate_desc = f"{self.cfg.translation_engine} via {type(self.translator).__name__}"
            if hasattr(self.translator, "model_path"):
                translate_desc += f" ({self.translator.model_path})"
        log.info("%s v%s: %d scene(s) tagged %r; mode=%s%s; sources=%s; translate=%s; "
                 "stash=%s; nfo=%s", PLUGIN_ID, PLUGIN_VERSION, len(scenes), self.cfg.trigger_tag,
                 self.cfg.mode, " (dry run)" if self.cfg.dry_run else "", self.cfg.sources,
                 translate_desc,
                 "on" if self.cfg.update_stash else "off", "on" if self.cfg.write_nfo else "off")
        for i, scene in enumerate(scenes):
            slog.progress(i / len(scenes))
            try:
                result = self.process(scene)
            except NfoWriteError as exc:
                log.error("%s: NFO write failed: %s -- scene left unchanged in Stash too.",
                          scene_label(scene), exc)
                result = core.ProcessResult("nfo_write_error", detail=str(exc))
            except Exception as exc:  # one bad scene must not abort the batch
                log.error("%s: unexpected error: %s\n%s", scene_label(scene), exc,
                          traceback.format_exc())
                result = core.ProcessResult("error", detail=str(exc))
            self.counts[result.status] = self.counts.get(result.status, 0) + 1
            for source, outcome in (result.outcomes or {}).items():
                key = f"{source}_{outcome}"
                self.counts[key] = self.counts.get(key, 0) + 1
        slog.progress(1.0)
        summary = ", ".join(f"{k}={v}" for k, v in sorted(self.counts.items())) or "nothing to do"
        log.info("done: %s", summary)


# ---------------------------------------------------------------------------
# "Check settings" task
# ---------------------------------------------------------------------------

def check_settings(stash: StashInterface, cfg: Config) -> None:
    """Log every effective setting with where it came from, verify the
    files and keys it points at, build the translator and run a one-word
    test translation through it. Nothing is written to Stash or disk."""
    raw = cfg.raw_settings
    problems: List[str] = []

    def src(key: str) -> str:
        value = raw.get(key)
        return "setting" if value not in (None, "", False) else "default"

    def line(label: str, value: Any, key: Optional[str] = None, note: str = "") -> None:
        origin = f" [{src(key)}]" if key else ""
        log.info("  %-28s %s%s%s", label, value, origin, f"  -- {note}" if note else "")

    log.info("=== %s v%s settings check ===", PLUGIN_ID, PLUGIN_VERSION)
    unknown = sorted(k for k in raw if k not in KNOWN_SETTINGS)
    if unknown:
        problems.append(f"unknown setting key(s) stored for this plugin: {unknown}")

    # Scene selection
    log.info("Scene selection")
    trigger_id = None
    try:
        tags = TagCache(stash, dry_run=True)
        trigger_id = tags.get(cfg.trigger_tag)
    except Exception as exc:
        problems.append(f"could not list tags: {exc}")
    if trigger_id:
        count = len(stash.find_scenes(
            f={"tags": {"value": [trigger_id], "modifier": "INCLUDES", "depth": 0}},
            fragment="id") or [])
        line("Trigger tag", repr(cfg.trigger_tag), "triggerTag", f"exists (id {trigger_id}), {count} scene(s) tagged")
    else:
        line("Trigger tag", repr(cfg.trigger_tag), "triggerTag", "DOES NOT EXIST")
        problems.append(f"trigger tag {cfg.trigger_tag!r} does not exist in Stash")
    line("Remove trigger tag when done", cfg.remove_trigger_tag, "removeTriggerTag")

    # Sources
    log.info("Sources")
    line("Sources", ", ".join(cfg.sources), "sources", "priority order")
    endpoint, api_key = javstash_endpoint_and_key(stash, cfg)
    if core.SOURCE_JAVSTASH in cfg.sources:
        if cfg.javstash_api_key:
            line("javstash API key", f"set ({len(cfg.javstash_api_key)} chars)", "javstashApiKey")
        elif api_key:
            line("javstash API key", f"from stash-box {endpoint}", None)
        else:
            line("javstash API key", "MISSING", "javstashApiKey")
            problems.append("javstash is selected but no API key is set and no javstash stash-box is configured")
    line("javstash stash_ids endpoint", endpoint)
    line("Request delay", f"{cfg.delay:g}s", "requestDelay")
    line("libredmm processing wait", f"{cfg.processing_wait:g}s", "libredmmProcessingWait")
    line("Update release date", cfg.update_release_date, "updateReleaseDate")

    # Writes
    log.info("Writes")
    line("Stash updates", "on" if cfg.update_stash else "OFF", "disableStashUpdate")
    line("NFO writes", "on" if cfg.write_nfo else "OFF", "disableNfoWrite")
    line("NFO backup", "on" if cfg.nfo_backup else "OFF", "disableNfoBackup")
    line("Tag failures", "on" if cfg.mark_failed else "OFF", "disableFailedTag")
    line("Plain translated title", cfg.plain_title, "plainTitle")
    line("Original title field", repr(cfg.original_title_field), "originalTitleField")
    line("Original description field", repr(cfg.original_description_field), "originalDescriptionField")

    # Translation
    log.info("Translation")
    if not cfg.translate:
        line("Translation", "OFF", "disableTranslation", "no title/details translation and no genre tags")
    else:
        line("Engine", cfg.translation_engine, "translationEngine")
        backend, model = TRANSLATION_ENGINES[cfg.translation_engine]
        if cfg.cloud_api_key:
            line("Google Cloud API key", f"set ({len(cfg.cloud_api_key)} chars)", "cloudApiKey",
                 "" if model == "nmt" and backend == "cloud" else "not used by this engine")
        elif cfg.translation_engine == "cloud-nmt":
            line("Google Cloud API key", "MISSING", "cloudApiKey")
            problems.append("cloud-nmt needs the Google Cloud API key setting")
        if cfg.translation_engine == "cloud-llm":
            path = cfg.cloud_credentials_file
            project_id = cfg.cloud_project_id
            if path:
                exists = Path(path).is_file()
                line("Credentials file", path, "cloudCredentialsFile",
                     "found" if exists else "NOT FOUND")
                if not exists:
                    problems.append(f"credentials file not found: {path}")
                elif not project_id:
                    try:
                        with open(path, encoding="utf-8") as f:
                            key = json.load(f)
                        project_id = key.get("project_id")
                        line("Service account", key.get("client_email", "?"), None, "from credentials file")
                    except (OSError, ValueError) as exc:
                        problems.append(f"credentials file is not a readable JSON key: {exc}")
            else:
                line("Credentials file", "blank", "cloudCredentialsFile",
                     "Application Default Credentials will be used")
            line("Project ID", project_id or "MISSING", "cloudProjectId",
                 "" if cfg.cloud_project_id or not project_id else "from credentials file")
            if not project_id:
                problems.append("cloud-llm needs a project ID (setting, or project_id in the credentials file)")
            line("Region", cfg.cloud_region, "cloudRegion")
        else:
            for key, label in (("cloudCredentialsFile", "Credentials file"),
                               ("cloudProjectId", "Project ID"), ("cloudRegion", "Region")):
                if raw.get(key):
                    line(label, raw[key], key, "not used by this engine")

        # Build the real translator and push one word through it.
        try:
            translator = build_translator(cfg)
            line("Backend class", type(translator).__name__)
            if hasattr(translator, "model_path"):
                line("Model", translator.model_path)
            sample = "日本語のテスト"
            result = translator.translate_ja_to_en(sample)
            if result:
                line("Test translation", f"{sample!r} -> {result!r}", None, "engine is working")
            else:
                problems.append("test translation returned nothing (see the warning above)")
        except SystemExit as exc:
            problems.append(f"translator could not be built: {exc.code}")
        except Exception as exc:
            problems.append(f"translator failed: {exc}")

    if problems:
        for p in problems:
            log.error("PROBLEM: %s", p)
        log.info("=== settings check finished with %d problem(s) ===", len(problems))
    else:
        log.info("=== settings check passed: no problems found ===")


KNOWN_SETTINGS = {
    "triggerTag", "removeTriggerTag", "sources", "javstashApiKey", "requestDelay",
    "libredmmProcessingWait", "updateReleaseDate", "disableTranslation",
    "translationEngine", "cloudApiKey", "cloudProjectId", "cloudRegion",
    "cloudCredentialsFile", "plainTitle", "disableStashUpdate", "disableNfoWrite",
    "disableNfoBackup", "disableFailedTag", "originalTitleField",
    "originalDescriptionField",
}


def main() -> None:
    # Only our own logger (shared with jav_nfo_core) goes through the Stash
    # handler; stashapi writes its own lines directly, so a root handler
    # would print those twice.
    handler = slog.StashLogHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False
    payload = json.loads(sys.stdin.read() or "{}")
    stash = StashInterface(payload.get("server_connection") or {})
    settings = (stash.get_configuration() or {}).get("plugins", {}).get(PLUGIN_ID) or {}
    cfg = Config(settings, payload.get("args") or {})
    try:
        cfg.validate()
        if cfg.mode == "check":
            check_settings(stash, cfg)
        else:
            Run(stash, cfg).main()
    except SystemExit as exc:
        if exc.code not in (None, 0):
            log.error("%s", exc.code)
            slog.exit(err=str(exc.code))
        raise
    slog.exit("ok")


if __name__ == "__main__":
    main()
