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
import re
import sys
import time
import traceback
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import stashapi.log as slog
from stashapi.stashapp import StashInterface

import jav_nfo_core as core

PLUGIN_ID = "jav-nfo-refresh"
PLUGIN_VERSION = "1.4.0"

DEFAULT_TRIGGER_TAG = "JAV NFO Refresh"
DEFAULT_ORIGINAL_TITLE_FIELD = "Original Title"
DEFAULT_ORIGINAL_DESCRIPTION_FIELD = "Original Description"
# Custom field holding the ISO timestamp of the last title/details
# translation -- the Stash-side twin of the NFO's <title_translated>.
TRANSLATED_AT_FIELD = "Translated At"
# NFO writability probe: attempts and seconds between them (a NAS drive
# spinning up takes 10-30 s).
WRITE_PROBE_ATTEMPTS = 4
WRITE_PROBE_WAIT = 10
# Used for <stash_ids> when Stash has no javstash stash-box configured.
JAVSTASH_ENDPOINT_FALLBACK = "https://javstash.org/graphql"

# Outcome tags. Every one is a child of PARENT_TAG; each name says what to
# do about it. A tag is added when its condition is seen and removed again
# the next time the condition is checked and found clear.
PARENT_TAG = "Metadata Refresh"
# The provenance tags ("nfo refreshed javstash", ...) and the manual
# override live under a separate parent, so PARENT_TAG + children is
# exactly "scenes that need a look" and never the successes.
SOURCE_PARENT_TAG = "Metadata Refresh Source"
MANUAL_TAG = "Metadata Refreshed Manually"   # pre-existing: "do not touch"
OUTCOME_TAGS = {
    "no_code": "Refresh: No Studio Code",
    "not_found": "Refresh: Not Found",
    "source_error": "Refresh: Source Error",
    "code_mismatch": "Refresh: Code Mismatch",
    "alt_match": "Refresh: Alternate Match",
    "merged": "Refresh: Merged",
    "no_source_text": "Refresh: No Original Text",
    "translation_failed": "Refresh: Translation Failed",
    "genres_incomplete": "Refresh: Genres Incomplete",
    "no_nfo": "Refresh: No NFO",
    "nfo_error": "Refresh: NFO Error",
}
TAG_DESCRIPTIONS = {
    PARENT_TAG: "Outcome tags written by the jav-nfo-refresh plugin: every child means a scene "
                "worth a look. Successful refreshes are under 'Metadata Refresh Source'.",
    SOURCE_PARENT_TAG: "Which source last refreshed a scene (jav-nfo-refresh plugin), plus the "
                       "manual override.",
    "no_code": "No studio code in the Studio Code field, the NFO tagline or the filename. "
               "Enter the Studio Code, then re-tag the scene.",
    "not_found": "Every selected source missed the code. Check the code or variant by hand, "
                 "or tag the scene 'Metadata Refreshed Manually'.",
    "source_error": "A source returned errors (outage, rate limit). Nothing to do: the trigger "
                    "tag is kept and the next run retries.",
    "code_mismatch": "The Studio Code disagrees with the filename; the Studio Code was used. "
                     "Decide which is right, fix it, re-tag.",
    "alt_match": "A source matched a different form of the code (e.g. KTRA-309 for KTRA-309E). "
                 "Spot-check that it is the same release.",
    "merged": "A lower-priority source only filled gaps on a scene a higher one refreshed "
              "earlier. Spot-check the mixed data.",
    "no_source_text": "Re-translate found no Original Title / Original Description. Run Refresh, "
                      "or paste the Japanese text into the custom fields.",
    "translation_failed": "The title or description translation returned nothing; nothing was "
                          "overwritten. The trigger tag is kept so a re-run retries.",
    "genres_incomplete": "Some genres could not be translated and were skipped. The trigger tag "
                         "is kept so a re-run picks them up.",
    "no_nfo": "No .nfo sidecar next to the file, so only Stash was updated.",
    "nfo_error": "The .nfo sidecar is unreadable, invalid XML or not writable. Fix the file or "
                 "the share; the trigger tag is kept.",
}
# Children of the existing "Metadata Refresh Failed" tag, which stays the
# umbrella for "a human has to look at this code".
FAILED_CHILDREN = ("no_code", "not_found")
# These keep the trigger tag on the scene so a plain re-run retries it.
TRANSIENT_FLAGS = {"source_error", "translation_failed", "genres_incomplete", "nfo_error"}
TRANSIENT_STATUSES = {"api_error", "nfo_not_writable", "nfo_write_error", "parse_error", "error"}

TRANSLATION_ENGINES = {
    "deep-translator": ("deep-translator", "nmt"),
    "cloud-nmt": ("cloud", "nmt"),
    "cloud-llm": ("cloud", "translation-llm"),
}

SCENE_FRAGMENT = """
    id title code details date director urls custom_fields
    studio { name }
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
        self.nfo_sync_skip_prefixes = [s.strip().lower() for s in
                                       text("nfoSyncSkipTagPrefixes").split(",") if s.strip()]

        self.mode = (args.get("mode") or "scrape").strip().lower()
        self.dry_run = bool(args.get("dry_run"))
        self.rescrape = bool(args.get("rescrape"))
        self.retranslate = bool(args.get("retranslate"))

        # Consumed by the core's fetchers / NFO helpers.
        self.search_limit = core.DEFAULT_SEARCH_LIMIT
        self.backup_mode = "once" if self.nfo_backup else "skip"
        self.no_backup = not self.nfo_backup

    def validate(self) -> None:
        if self.mode not in ("scrape", "translate_only", "check", "reclassify", "nfo_sync"):
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
        if self.mode == "nfo_sync" and not self.write_nfo:
            raise SystemExit("The NFO sync task writes sidecars, but 'Disable NFO writes' is on.")


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
        self._parents: Dict[str, List[str]] = {}
        for tag in stash.find_tags(fragment="id name aliases parents { id }") or []:
            self._ids.setdefault(tag["name"].strip().lower(), tag["id"])
            self._parents[tag["id"]] = [p["id"] for p in tag.get("parents") or []]
            for alias in tag.get("aliases") or []:
                self._ids.setdefault(alias.strip().lower(), tag["id"])

    def get(self, name: str) -> Optional[str]:
        return self._ids.get(name.strip().lower())

    def ensure(self, name: str, description: str = "",
               parent_id: Optional[str] = None) -> Optional[str]:
        tag_id = self.get(name)
        if tag_id or self.dry_run:
            if not tag_id:
                log.info("[dry-run] would create tag %r", name)
            return tag_id
        tag_in: Dict[str, Any] = {"name": name.strip()}
        if description:
            tag_in["description"] = description
        if parent_id:
            tag_in["parent_ids"] = [parent_id]
        created = self.stash.create_tag(tag_in)
        tag_id = created["id"]
        self._ids[name.strip().lower()] = tag_id
        self._parents[tag_id] = [parent_id] if parent_id else []
        log.info("created tag %r (id %s)", name, tag_id)
        return tag_id

    def add_parent(self, tag_id: Optional[str], parent_id: Optional[str],
                   instead_of: Optional[str] = None) -> None:
        """Put an existing tag under a parent, keeping its other parents
        except `instead_of`, which is dropped (used to move a tag)."""
        if not tag_id or not parent_id or tag_id == parent_id:
            return
        parents = self._parents.get(tag_id, [])
        wanted = [p for p in parents if p != instead_of]
        if parent_id not in wanted:
            wanted.append(parent_id)
        if wanted == parents:
            return
        if self.dry_run:
            log.info("[dry-run] would nest tag %s under %s", tag_id, parent_id)
            return
        self.stash.update_tag({"id": tag_id, "parent_ids": wanted})
        self._parents[tag_id] = wanted


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
        # Outcome bookkeeping for the tags: a condition that was looked at
        # goes into `checked`, one that holds goes into `flags` too.
        self.flags: set = set()
        self.checked: set = set()

    def check(self, key: str, hit: bool) -> None:
        self.checked.add(key)
        if hit:
            self.flags.add(key)

    # -- loading ----------------------------------------------------------

    def load_nfo(self) -> Optional[str]:
        """Parse the sidecar when NFO writes are enabled. Returns an error
        status string if the file exists but cannot be parsed."""
        if not self.cfg.write_nfo or self.path is None:
            return None
        self.nfo_path = self.path.with_suffix(".nfo")
        exists = self.nfo_path.exists()
        self.check("no_nfo", not exists)
        if not exists:
            log.info("%s: no .nfo sidecar at %s -- Stash only.", self.label, self.nfo_path.name)
            self.nfo_path = None
            self.check("nfo_error", False)
            return None
        try:
            self.tree = ET.parse(self.nfo_path)
        except ET.ParseError as exc:
            log.error("%s: %s is not valid XML (%s) -- skipping scene.",
                      self.label, self.nfo_path.name, exc)
            self.check("nfo_error", True)
            return f"parse_error: {exc}"
        self.root = self.tree.getroot()
        self.nfo_before = self.nfo_snapshot()
        # Probe write access up front (open for update, touch nothing): a
        # read-only file would otherwise only surface after the sources
        # were queried and the translation paid for. A pooled NAS share
        # answers "read-only file system" for files on a drive that is
        # still spinning up, so give it a few tries before giving up.
        error = None
        # Once a full set of retries has failed in this run, the drive is
        # evidently not coming back: later scenes get a single attempt.
        attempts = 1 if self.run.drive_wait_exhausted else WRITE_PROBE_ATTEMPTS
        for attempt in range(1, attempts + 1):
            try:
                with open(self.nfo_path, "r+b"):
                    pass
                error = None
                break
            except OSError as exc:
                error = exc.strerror or str(exc)
                if attempt < attempts:
                    log.warning("%s: %s is not writable (%s) -- waiting %ds for the drive "
                                "(attempt %d/%d).", self.label, self.nfo_path.name, error,
                                WRITE_PROBE_WAIT, attempt, WRITE_PROBE_ATTEMPTS)
                    time.sleep(WRITE_PROBE_WAIT)
        self.check("nfo_error", bool(error))
        if error:
            self.run.drive_wait_exhausted = True
            log.error("%s: %s is not writable (%s) -- skipping scene. Fix the share "
                      "or turn on 'Disable NFO writes'.", self.label, self.nfo_path.name, error)
            return f"nfo_not_writable: {error}"
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
        file_code = core.extract_code_from_filename(self.path) if self.path is not None else None
        mismatch = False
        if not code:
            code = file_code
        elif file_code and not self.filename_agrees(code):
            # Stash's Studio Code wins, but a disagreement with the filename
            # usually means one of them is wrong -- say so.
            mismatch = True
            log.warning("%s: Studio Code %s disagrees with the filename (%s) -- using %s. "
                        "Fix the Studio Code (or the tagline) if the file is right.",
                        self.label, code, file_code, code)
        self.check("code_mismatch", mismatch)
        self.check("no_code", not code)
        if not code:
            log.warning("%s: no studio code in Stash, NFO tagline or filename.", self.label)
        return code

    def filename_agrees(self, code: str) -> bool:
        """True when the filename spells the same release as `code`, in any
        of the forms the parser knows (before-underscore part, whole stem,
        alternate forms, or the bare date-number of an uncensored code:
        "Carib-010823-001" and "010823-001-carib" are the same thing)."""
        stem = core.PART_SUFFIX_RE.sub("", self.path.stem)
        bare = core.uncensored_bare_code(code)
        if bare and bare.replace("_", "-") in stem.replace("_", "-"):
            return True
        candidates = {core.extract_code_from_filename(self.path), core.normalize_code(stem)}
        keys = {core.code_match_key(f) for c in candidates if c for f in core.acceptable_code_forms(c)}
        accepted = {core.code_match_key(f) for f in core.acceptable_code_forms(code)}
        return bool(keys & accepted)

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

    def set_nfo_text(self, tag: str, value: Optional[str], after_tags: List[str]) -> None:
        """NFO-only write of a plain text element; empty values never blank
        out what the file already has."""
        if self.root is not None and value and value.strip():
            core.set_text_insert_after(self.root, tag, value.strip(), after_tags=after_tags)

    def add_nfo_actors(self, names: List[str]) -> List[str]:
        """Append <actor> entries for performers the NFO doesn't list yet."""
        if self.root is None:
            return []
        have = {n.lower() for n in core.get_actor_names(self.root)}
        added = []
        for name in names:
            if not name or name.lower() in have:
                continue
            actor = ET.Element("actor")
            ET.SubElement(actor, "name").text = name
            ET.SubElement(actor, "type").text = "Actor"
            existing = self.root.findall("actor")
            idx = list(self.root).index(existing[-1]) + 1 if existing else len(self.root)
            self.root.insert(idx, actor)
            have.add(name.lower())
            added.append(name)
        return added

    def add_tags(self, names: List[str], nfo: bool = True) -> List[str]:
        """Attach tags (creating them if needed) and, unless nfo=False,
        mirror them as NFO genres. Returns the names that were actually new."""
        added = []
        for name in names:
            new = False
            tag_id = self.run.tags.ensure(name)
            if tag_id and tag_id not in self.tag_ids:
                self.tag_ids.append(tag_id)
                new = True
            elif not tag_id and name.lower() not in self.tag_names:
                new = True  # dry run: would be created and attached
            if nfo and self.root is not None \
                    and core.add_genre(self.root, name, after_tags=core.GENRE_AFTER_TAGS):
                new = True
            if new:
                added.append(name)
        return added

    def remove_tag(self, name: str, nfo: bool = True) -> None:
        tag_id = self.run.tags.get(name)
        if tag_id and tag_id in self.tag_ids:
            self.tag_ids.remove(tag_id)
        if nfo and self.root is not None:
            core.remove_genre(self.root, name)

    def has_tag(self, name: str) -> bool:
        tag_id = self.run.tags.get(name)
        return bool(tag_id) and tag_id in self.tag_ids

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

    def finish_trigger_tag(self, keep: bool) -> None:
        if self.cfg.remove_trigger_tag and not keep:
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
                          label: str) -> Tuple[List[str], int]:
    """Translated, de-duplicated genre names plus how many failed."""
    wanted = [g.strip() for g in genres_jp if g and g.strip() and g.strip() not in core.GENRE_SKIP]
    translations = core.translate_genres(translator, wanted)
    names, failed = [], 0
    for genre_jp in wanted:
        translated = translations.get(genre_jp)
        if translated is None:
            failed += 1
            log.warning("%s: genre translation failed for %r -- skipping that genre.", label, genre_jp)
        elif translated not in names:
            names.append(translated)
    return names, failed


def process_scene_scrape(ctx: SceneContext, run: "Run") -> Tuple[core.ProcessResult, str]:
    """Returns (result, description of what changed); nothing is committed here."""
    cfg = ctx.cfg
    code = ctx.code()
    if not code:
        return core.ProcessResult("no_code", detail="no recognizable studio code"), ""

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
            # A refreshed scene is by definition not a code failure: a stale
            # Failed / Not Found left over from an earlier miss is cleared.
            ctx.check("not_found", False)
            return core.ProcessResult("already_refreshed", code=code, outcomes=outcomes), "tags only"
        errored = "error" in outcomes.values()
        ctx.check("source_error", errored)
        ctx.check("not_found", not errored)
        status = "api_error" if errored else "no_match"
        return (core.ProcessResult(status, code=code, detail="; ".join(miss_details),
                                   outcomes=outcomes), "")

    ctx.check("source_error", False)
    ctx.check("not_found", False)
    winner = next(iter(data.values()))
    protected = [s for s in core.SOURCE_PRIORITY[:core.SOURCE_PRIORITY.index(winner.source)]
                 if s in refreshed_by]
    ctx.check("merged", bool(protected))
    matched = core.normalize_scene_code(winner.matched_code or "")
    ctx.check("alt_match", bool(matched) and core.code_match_key(matched) != core.code_match_key(code))
    if "alt_match" in ctx.flags:
        log.info("%s: %s filed this release under %s (asked for %s) -- tagging for a spot-check.",
                 ctx.label, winner.source, matched, code)
    if protected:
        log.info("%s: %s only fills gaps -- %s refreshed this scene earlier.",
                 ctx.label, winner.source, protected)

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
        failed = False
        if full_retranslate and title_jp:
            failed |= not translate_title(ctx, code, title_jp, run.translator)
        if details_jp and (full_retranslate or plot_is_new):
            if plot_is_new and not full_retranslate:
                log.info("%s: new description from %s -- translating it although the "
                         "scene was already translated.", ctx.label, winner.source)
            translated_plot = run.translator.translate_ja_to_en(details_jp)
            if translated_plot:
                ctx.set_translated_plot(translated_plot)
            else:
                failed = True
                log.warning("%s: description translation failed -- details left untouched.", ctx.label)
        if (full_retranslate and title_jp) or (details_jp and (full_retranslate or plot_is_new)):
            ctx.check("translation_failed", failed)
        if not full_retranslate:
            log.info("%s: already translated -- leaving the title%s alone; genres still refreshed.",
                     ctx.label, "" if plot_is_new else "/details")
        if winner.genres:
            names, genre_failures = translate_genre_names(run.translator, winner.genres, ctx.label)
            ctx.check("genres_incomplete", genre_failures > 0)
            added = ctx.add_tags(names)
            if added:
                log.info("%s: adding genre tag(s) %s", ctx.label, added)

    if not ctx.data_changed():
        if any(sd.has_any_data() for sd in data.values()):
            log.info("%s: %s returned nothing the scene didn't already have.", ctx.label, sorted(data))
            return core.ProcessResult("unchanged", code=code, outcomes=outcomes), "tags only"
        return (core.ProcessResult("no_changes", code=code, outcomes=outcomes,
                                   detail=f"matched on {sorted(data)} but returned no usable fields"),
                "tags only")

    ctx.add_tags([core.REFRESHED_GENRES[s] for s in list(data) + refreshed_by])
    ids = ", ".join(f"{s}={sd.id}" for s, sd in data.items())
    return (core.ProcessResult("would_update" if cfg.dry_run else "updated", code=code,
                               outcomes=outcomes),
            f"from {sorted(data)} (code={code}, {ids})")


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


def process_scene_translate_only(ctx: SceneContext, run: "Run") -> Tuple[core.ProcessResult, str]:
    cfg = ctx.cfg
    if not cfg.retranslate and ctx.was_translated():
        log.info("%s: already translated -- skipping.", ctx.label)
        return core.ProcessResult("already_translated"), "tags only"

    title_jp = ctx.existing_original_title()
    details_jp = ctx.existing_original_description()
    ctx.check("no_source_text", not title_jp and not details_jp)
    if not title_jp and not details_jp:
        log.warning("%s: no Original Title / Original Description to translate -- skipping.", ctx.label)
        return core.ProcessResult("no_source_text", detail="nothing to translate"), ""

    code = ctx.code()
    if not code:
        return core.ProcessResult("no_code", detail="no recognizable studio code"), ""

    changed = failed = False
    if title_jp:
        if translate_title(ctx, code, title_jp, run.translator):
            changed = True
        else:
            failed = True
    if details_jp:
        translated_plot = run.translator.translate_ja_to_en(details_jp)
        if translated_plot:
            ctx.set_translated_plot(translated_plot)
            changed = True
        else:
            failed = True
            log.warning("%s: description translation failed -- details left untouched.", ctx.label)
    ctx.check("translation_failed", failed)
    if not changed:
        return (core.ProcessResult("no_changes", code=code,
                                   detail="translation failed for all available source text"), "")
    return (core.ProcessResult("would_update" if cfg.dry_run else "updated", code=code),
            f"re-translated (code={code})")


def sync_stash_to_nfo(ctx: SceneContext, run: "Run") -> Tuple[core.ProcessResult, str]:
    """Stash is the source of truth for a manually curated scene: copy its
    metadata into the sidecar. Adds, never removes (genres, actors, URLs
    are merged); empty Stash fields leave the NFO alone."""
    if ctx.root is None:
        log.warning("%s: no .nfo sidecar to sync into.", ctx.label)
        return core.ProcessResult("no_nfo"), "tags only"
    sc = ctx.scene
    custom = ctx.custom
    scalars = ("title", "plot", "originaltitle", "original_plot", "tagline", "premiered",
               "releasedate", "director", "studio", "stashid")
    before = {tag: ctx.nfo_text(tag) for tag in scalars}
    before_urls = [el.text for el in ctx.root.findall("URL") if el.text]
    ctx.set_nfo_text("title", sc.get("title"), after_tags=[])
    ctx.set_nfo_text("plot", sc.get("details"), after_tags=["title"])
    ctx.set_nfo_text("originaltitle", custom.get(ctx.cfg.original_title_field), after_tags=["title"])
    ctx.set_nfo_text("original_plot", custom.get(ctx.cfg.original_description_field),
                     after_tags=["plot", "originaltitle"])
    ctx.set_nfo_text("tagline", sc.get("code"), after_tags=["runtime", "director", "year", "plot"])
    if sc.get("date"):
        ctx.set_nfo_text("premiered", sc["date"], after_tags=["original_plot", "plot", "originaltitle"])
        ctx.set_nfo_text("releasedate", sc["date"], after_tags=["premiered"])
    ctx.set_nfo_text("director", sc.get("director"), after_tags=["releasedate", "premiered"])
    ctx.set_nfo_text("studio", (sc.get("studio") or {}).get("name"), after_tags=["genre", "URL", "tagline"])
    for sid in sc.get("stash_ids") or []:
        if sid.get("endpoint") == run.javstash_endpoint and sid.get("stash_id"):
            core.set_attributed_tag_insert_after(
                ctx.root, "stashid", {"endpoint": core.JAVSTASH_ENDPOINT_NAME}, sid["stash_id"],
                after_tags=["tagline", "id", "datasource"])
    urls = list(sc.get("urls") or [])
    if urls:
        have = [el.text for el in ctx.root.findall("URL") if el.text]
        merged = have + [u for u in urls if u not in have]
        if merged != have:
            core.set_repeated_tag(ctx.root, "URL", merged,
                                  after_tags=["stashid", "tagline", "id", "datasource"])
    skip = {ctx.cfg.trigger_tag.lower(), PARENT_TAG.lower(), SOURCE_PARENT_TAG.lower(),
            core.FAILED_GENRE.lower()} | {n.lower() for n in OUTCOME_TAGS.values()}
    genres = []
    for tag in sc.get("tags") or []:
        name = (tag.get("name") or "").strip()
        low = name.lower()
        if not name or low in skip or any(low.startswith(p) for p in ctx.cfg.nfo_sync_skip_prefixes):
            continue
        if core.add_genre(ctx.root, name, after_tags=core.GENRE_AFTER_TAGS):
            genres.append(name)
    actors = ctx.add_nfo_actors([(p.get("name") or "").strip() for p in sc.get("performers") or []])
    core.remove_genre(ctx.root, core.FAILED_GENRE)
    changed = [tag for tag in scalars if ctx.nfo_text(tag) != before[tag]]
    if [el.text for el in ctx.root.findall("URL") if el.text] != before_urls:
        changed.append("URL")
    if genres:
        changed.append(f"genre+{len(genres)}")
        log.info("%s: adding NFO genre(s) %s", ctx.label, genres)
    if actors:
        changed.append(f"actor+{len(actors)}")
        log.info("%s: adding NFO actor(s) %s", ctx.label, actors)
    if not changed or not ctx.nfo_changed():
        # `changed` empty but the tree differs = whitespace normalization
        # only; not worth a write.
        log.info("%s: NFO already matches Stash.", ctx.label)
        ctx.root = None   # nothing to write for this scene
        return core.ProcessResult("unchanged"), "tags only"
    return (core.ProcessResult("would_update" if ctx.cfg.dry_run else "updated"),
            "synced Stash -> NFO: " + ", ".join(changed))


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class Run:
    def __init__(self, stash: StashInterface, cfg: Config):
        self.stash = stash
        self.cfg = cfg
        self.timestamp = datetime.now().isoformat(timespec="seconds")
        self.counts: Dict[str, int] = {}
        self.drive_wait_exhausted = False
        self.javstash_endpoint, api_key = javstash_endpoint_and_key(stash, cfg)
        self.tags = TagCache(stash, cfg.dry_run)
        self.trigger_tag_id = self.tags.get(cfg.trigger_tag)
        self.translator = build_translator(cfg) if cfg.mode in ("scrape", "translate_only") else None
        self.clients = build_clients(cfg, api_key) if cfg.mode == "scrape" else {}

    # -- tags -------------------------------------------------------------

    def ensure_tag_tree(self) -> None:
        """Create the outcome tags (with their action text) and nest every
        plugin tag under PARENT_TAG; the two code failures also go under
        the pre-existing 'Metadata Refresh Failed'."""
        parent = self.tags.ensure(PARENT_TAG, TAG_DESCRIPTIONS[PARENT_TAG])
        failed = self.tags.ensure(core.FAILED_GENRE, "", parent)
        self.tags.add_parent(failed, parent)
        source_parent = self.tags.ensure(SOURCE_PARENT_TAG, TAG_DESCRIPTIONS[SOURCE_PARENT_TAG])
        for name in (MANUAL_TAG, *core.REFRESHED_GENRES.values()):
            self.tags.add_parent(self.tags.ensure(name), source_parent, instead_of=parent)
        for key, name in OUTCOME_TAGS.items():
            under = failed if key in FAILED_CHILDREN else parent
            tag_id = self.tags.ensure(name, TAG_DESCRIPTIONS[key], under)
            self.tags.add_parent(tag_id, under)

    def reconcile_tags(self, ctx: SceneContext, result: core.ProcessResult) -> None:
        """Apply the outcome flags: add a tag whose condition held, remove
        one whose condition was checked and found clear, keep the rest."""
        for key, name in OUTCOME_TAGS.items():
            if key in ctx.flags:
                ctx.add_tags([name], nfo=False)
            elif key in ctx.checked:
                ctx.remove_tag(name, nfo=False)
        # The umbrella failure tag (also an NFO genre, as the CLI wrote it).
        code_failed = bool(ctx.flags & set(FAILED_CHILDREN))
        if code_failed and self.cfg.mark_failed:
            ctx.add_tags([core.FAILED_GENRE])
        elif "not_found" in ctx.checked and not code_failed:
            ctx.remove_tag(core.FAILED_GENRE)
        keep = bool(ctx.flags & TRANSIENT_FLAGS) or result.status in TRANSIENT_STATUSES
        ctx.finish_trigger_tag(keep=keep)

    # -- scenes -----------------------------------------------------------

    def tagged_scenes(self, tag_id: Optional[str] = None) -> List[Dict[str, Any]]:
        tag_id = tag_id or self.trigger_tag_id
        if not tag_id:
            raise SystemExit(f"Trigger tag {self.cfg.trigger_tag!r} does not exist in Stash -- "
                             "create it and tag the scenes to process.")
        scenes = self.stash.find_scenes(
            f={"tags": {"value": [tag_id], "modifier": "INCLUDES", "depth": 0}},
            fragment=SCENE_FRAGMENT,
        ) or []
        scenes.sort(key=lambda s: str(scene_file_path(s) or ""))
        return scenes

    def process(self, scene: Dict[str, Any]) -> core.ProcessResult:
        ctx = SceneContext(scene, self.cfg, self)
        if ctx.path is None:
            log.warning("%s: has no file -- skipping.", ctx.label)
            return core.ProcessResult("no_file")
        manual = ctx.has_tag(MANUAL_TAG)
        if manual and not self.cfg.write_nfo:
            log.info("%s: tagged %r -- leaving it alone.", ctx.label, MANUAL_TAG)
            ctx.finish_trigger_tag(keep=False)
            ctx.commit("manual, trigger tag only")
            return core.ProcessResult("manual")
        error = ctx.load_nfo()
        if error:
            result = core.ProcessResult(error.split(":", 1)[0], detail=error)
            what = "tags only"
        elif manual or self.cfg.mode == "nfo_sync":
            if self.cfg.mode != "nfo_sync":
                log.info("%s: tagged %r -- syncing Stash into the NFO instead of scraping.",
                         ctx.label, MANUAL_TAG)
            result, what = sync_stash_to_nfo(ctx, self)
        elif self.cfg.mode == "translate_only":
            result, what = process_scene_translate_only(ctx, self)
        else:
            result, what = process_scene_scrape(ctx, self)
        self.reconcile_tags(ctx, result)
        ctx.commit(what or "tags only")
        return result

    def main(self) -> None:
        if self.cfg.mode == "nfo_sync":
            manual_id = self.tags.get(MANUAL_TAG)
            if not manual_id:
                raise SystemExit(f"Tag {MANUAL_TAG!r} does not exist -- nothing to sync.")
            scenes = self.tagged_scenes(manual_id)
            # The sync never touches the trigger tag: these scenes were
            # selected by the manual tag, not queued.
            self.cfg.remove_trigger_tag = False
        else:
            scenes = self.tagged_scenes()
        translate_desc = "off"
        if self.translator:
            translate_desc = f"{self.cfg.translation_engine} via {type(self.translator).__name__}"
            if hasattr(self.translator, "model_path"):
                translate_desc += f" ({self.translator.model_path})"
        log.info("%s v%s: %d scene(s) tagged %r; mode=%s%s; sources=%s; translate=%s; "
                 "stash=%s; nfo=%s", PLUGIN_ID, PLUGIN_VERSION, len(scenes),
                 MANUAL_TAG if self.cfg.mode == "nfo_sync" else self.cfg.trigger_tag,
                 self.cfg.mode, " (dry run)" if self.cfg.dry_run else "", self.cfg.sources,
                 translate_desc,
                 "on" if self.cfg.update_stash else "off", "on" if self.cfg.write_nfo else "off")
        if scenes and self.cfg.update_stash:
            self.ensure_tag_tree()
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

    # -- "Reclassify failures" task ---------------------------------------

    LOG_LINE_RE = re.compile(r'^time="([^"]+)" level=\w+ msg="\[Plugin / [^\]]+\] scene (\d+) \(([^)]*)\): (.*)"$')

    def read_log_outcomes(self) -> Dict[str, str]:
        """Latest per-scene outcome recoverable from the Stash log: the
        lines of each scene's most recent run, classified."""
        log_file = None
        try:
            log_file = ((self.stash.get_configuration() or {}).get("general") or {}).get("logFile")
        except Exception:
            pass
        if not log_file or not Path(log_file).is_file():
            log.warning("Stash log file not found (%r) -- classifying from local data only.", log_file)
            return {}
        lines: Dict[str, List[Tuple[str, str]]] = {}
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for raw in f:
                m = self.LOG_LINE_RE.match(raw.rstrip("\n"))
                if m:
                    lines.setdefault(m.group(2), []).append((m.group(1), m.group(4)))
        # Only lines a scrape run writes count; a later re-translate run
        # says nothing about whether the sources know the code.
        scrape_patterns = ("-- from [", "already refreshed from", "lookup failed",
                           "no studio code in Stash", " match for ")

        def stamp_of(s: str) -> float:
            try:
                return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").timestamp()
            except ValueError:
                return 0.0

        outcomes: Dict[str, str] = {}
        for scene_id, entries in lines.items():
            scrape_lines = [(stamp_of(s), text) for s, text in entries
                            if any(p in text for p in scrape_patterns)]
            if not scrape_lines:
                continue
            newest = scrape_lines[-1][0]
            block = "\n".join(text for ts, text in scrape_lines if ts >= newest - 600)
            if "-- from [" in block or "already refreshed from" in block:
                outcomes[scene_id] = "ok"
            elif "lookup failed" in block:
                outcomes[scene_id] = "source_error"
            elif "no studio code in Stash" in block:
                outcomes[scene_id] = "no_code"
            else:
                outcomes[scene_id] = "not_found"
        log.info("read %d scene(s) with outcomes from %s", len(outcomes), log_file)
        return outcomes

    def reclassify(self) -> None:
        failed_id = self.tags.get(core.FAILED_GENRE)
        if not failed_id:
            raise SystemExit(f"Tag {core.FAILED_GENRE!r} does not exist -- nothing to reclassify.")
        scenes = self.tagged_scenes(failed_id)
        log.info("%s v%s: reclassifying %d scene(s) tagged %r%s", PLUGIN_ID, PLUGIN_VERSION,
                 len(scenes), core.FAILED_GENRE, " (dry run)" if self.cfg.dry_run else "")
        self.ensure_tag_tree()
        from_log = self.read_log_outcomes()
        for i, scene in enumerate(scenes):
            slog.progress(i / len(scenes))
            ctx = SceneContext(scene, self.cfg, self)
            try:
                error = ctx.load_nfo() if ctx.path else None
                outcome = from_log.get(str(scene["id"]))
                # A scene some source already refreshed is not a failure,
                # whatever an older run said.
                if ctx.refreshed_by():
                    outcome = "ok"
                # Local evidence beats an old log line for the code itself.
                elif ctx.path and ctx.code() is None:
                    outcome = "no_code"
                elif outcome == "no_code":
                    outcome = "not_found"   # has a code now; the sources still missed it
                if outcome == "no_code":
                    ctx.add_tags([OUTCOME_TAGS["no_code"]], nfo=False)
                    ctx.remove_tag(OUTCOME_TAGS["not_found"], nfo=False)
                elif outcome == "not_found":
                    ctx.add_tags([OUTCOME_TAGS["not_found"]], nfo=False)
                elif outcome == "source_error":
                    ctx.add_tags([OUTCOME_TAGS["source_error"]], nfo=False)
                    ctx.remove_tag(core.FAILED_GENRE)
                elif outcome == "ok":
                    # Refreshed by a source: the Failed umbrella and its two
                    # code-failure children are stale.
                    ctx.remove_tag(core.FAILED_GENRE)
                    ctx.remove_tag(OUTCOME_TAGS["no_code"], nfo=False)
                    ctx.remove_tag(OUTCOME_TAGS["not_found"], nfo=False)
                    outcome = "refreshed_already"
                else:
                    outcome = "unclassified"
                if error:
                    ctx.add_tags([OUTCOME_TAGS["nfo_error"]], nfo=False)
                ctx.commit(f"reclassified as {outcome}")
            except NfoWriteError as exc:
                log.error("%s: NFO write failed: %s", ctx.label, exc)
                outcome = "nfo_write_error"
            except Exception as exc:
                log.error("%s: unexpected error: %s\n%s", ctx.label, exc, traceback.format_exc())
                outcome = "error"
            self.counts[outcome] = self.counts.get(outcome, 0) + 1
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
    "originalDescriptionField", "nfoSyncSkipTagPrefixes",
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
        elif cfg.mode == "reclassify":
            Run(stash, cfg).reclassify()
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
