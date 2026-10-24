#!/usr/bin/env python3
"""Tag Stash scenes with the Whisper models used to transcribe and subtitle them.

Scans a directory tree for WhisperJAV sidecar files (``*-whisperjav-command.txt``
or ``*-whisper-command.txt``), extracts every ASR and translation model named in
the recorded command, locates the matching scene in a Stash instance by file
path, and applies ``Transcription: <model>`` and ``Subtitled: <model>`` tags.

Model flags are listed in TRANSCRIPTION_FLAGS / TRANSLATION_FLAGS below; add a
flag there to have it recognised. Values are also pulled out of JSON-valued
flags such as ``--generator-config '{"model_id": ...}'``.

Because Stash usually runs on a different machine, local paths are rewritten to
Stash-side paths with one or more ``--path-map LOCAL=REMOTE`` rules.

Only the standard library is required.

Examples:
    export STASH_URL=http://192.168.11.113:7777
    export STASH_API_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1aWQiOiJ1c2VyIiwic3ViIjoiQVBJS2V5IiwiaWF0IjoxNzYzODM3Njk0fQ.kputb9p0j94syisDn3OKI25r9XBRDnaty-9b-l34uJ4

    # See what would happen, without changing anything
    ./subtitle_tagger.py /mnt/media --path-map /mnt/media=/data/media --dry-run

    # Apply the tags
    ./subtitle_tagger.py /mnt/media --path-map /mnt/media=/data/media
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# Sidecar naming varies between wrapper versions; both spellings are accepted.
SUFFIXES = ("-whisperjav-command.txt", "-whisper-command.txt")

# Extensions tried when the text file's stem does not already carry one.
VIDEO_EXTENSIONS = (
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".m4v",
    ".mpg", ".mpeg", ".flv", ".webm", ".ts", ".m2ts",
)


def flag_re(name: str) -> re.Pattern[str]:
    """Match ``--flag VALUE``, ``--flag=VALUE`` and quoted variants.

    At least one leading dash is required: a bare word such as "model" inside a
    plot summary must never be read as a flag. ``-``/``_`` are interchangeable,
    and the lookbehind keeps the flag from matching partway through a longer
    token such as ``--qwen-model-id``.
    """
    return re.compile(
        r"(?<![\w./-])-{1,2}" + name.replace("-", "[-_]") + r"[=\s]+"
        r"(\"[^\"]+\"|'[^']+'|\S+)",
        re.IGNORECASE,
    )


TRANSCRIPTION_PREFIX = "Transcription: "
TRANSLATION_PREFIX = "Subtitled: "

# Only tags carrying one of these prefixes are ever removed. Anything else on
# a scene was put there by someone else and is left strictly alone.
MANAGED_PREFIXES = (TRANSCRIPTION_PREFIX, TRANSLATION_PREFIX)

# Flags whose value names the ASR model outright. Pipeline/runtime selectors
# (--mode, --pass1-pipeline, --asr) are deliberately absent: they pick an engine
# or preset, not a model, and tagging them buries the real signal.
TRANSCRIPTION_FLAGS = (
    "model",             # global Whisper override, e.g. large-v2, turbo
    "pass1-model",       # ensemble pass 1, e.g. kotoba-whisper-v2.0
    "pass2-model",       # ensemble pass 2
    "hf-model-id",       # --mode transformers
    "qwen-model-id",     # --mode qwen
    "crispasr-backend",  # whispercpp | parakeet | cohere - no model-id flag exists
)

# Backend names that only imply a model. Consulted when nothing above matched,
# so "--generator qwen3 --generator-config {model_id: Qwen/...}" tags the real
# model id once rather than tagging both the backend and the model.
TRANSCRIPTION_FALLBACK_FLAGS = (
    "qwen-generator",    # qwen3 | anime-whisper
    "generator",         # --pipeline decoupled TextGenerator
)

# The model that produced the translated subtitles.
TRANSLATION_FLAGS = ("translate-model",)

# Consulted only when no explicit translation model was given. For a cloud
# service the provider may be the only record of what translated the file, so
# "deepseek" alone still earns a tag - but it is not worth a second tag next to
# an explicit model, and "ollama" names a host rather than a model.
TRANSLATION_FALLBACK_FLAGS = ("translate-provider",)

# Flags carrying a JSON blob that can nest a model id, e.g.
#   --generator-config '{"model_id": "Qwen/Qwen3-ASR-1.7B", "batch_size": 4}'
JSON_TRANSCRIPTION_FLAGS = (
    "overrides",
    "pass1-overrides", "pass2-overrides",
    "pass1-params", "pass2-params",
    "pass1-hf-params", "pass2-hf-params",
    "pass1-qwen-params", "pass2-qwen-params",
    "generator-config",
)

# Keys searched for inside those JSON blobs.
JSON_MODEL_KEYS = ("model_id", "model", "model_name", "hf_model_id",
                   "qwen_model_id", "generator", "generator_backend")

# Short backend names that denote a specific published model. Canonicalising
# here means --qwen-generator anime-whisper and --qwen-model-id
# litagin/anime-whisper collapse to one tag instead of two.
ALIASES = {
    "anime-whisper": "litagin/anime-whisper",
}

# Flags whose value is arbitrary human prose supplied by the user. Their values
# are stripped out before any model flag is matched, so a title, plot summary or
# performer name can never contribute a tag.
FREE_TEXT_FLAGS = (
    "translate-title",
    "translate-plot",
    "translate-actress",
    "credit",
    "initial-prompt",
    "context",
    "qwen-context",
)

# A quoted value ends at its closing quote; an unquoted one runs until the next
# flag or the end of the line, mirroring how a shell would group it.
FREE_TEXT_RE = re.compile(
    r"(?<![\w./-])-{1,2}(?:"
    + "|".join(f.replace("-", "[-_]") for f in FREE_TEXT_FLAGS)
    + r")(?:=|\s+)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\n]*?(?=\s-{1,2}[A-Za-z]|$))",
    re.IGNORECASE | re.MULTILINE,
)


def mask_free_text(text: str) -> str:
    """Blank out user-supplied prose so it cannot be scanned for model flags."""
    return FREE_TEXT_RE.sub(" ", text)


# Legacy sidecars are appended to on re-runs, each record introduced by a bare
# timestamp line. Only the last record describes the subtitles on disk today.
RUN_SEPARATOR_RE = re.compile(r"^\s*\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\s*$", re.M)


def latest_run(text: str) -> str:
    """The final run record in a sidecar that accumulated several."""
    marks = [m.start() for m in RUN_SEPARATOR_RE.finditer(text)]
    return text[marks[-1]:] if len(marks) > 1 else text


# Whisper can translate to English itself instead of transcribing. When it does,
# the ASR model is what produced the subtitles, so it is reported as a
# translation model and there is no separate transcription step to report.
TASK_RE = flag_re("task")
SUBS_LANGUAGE_RE = flag_re("subs-language")
DIRECT_TRANSLATE_VALUES = {"translate", "direct-to-english", "direct_to_english"}


def is_direct_translation(text: str) -> bool:
    """True when the ASR pass emitted English rather than a native transcript."""
    for pattern in (TASK_RE, SUBS_LANGUAGE_RE):
        for value in extract_values(text, pattern):
            if value.lower() in DIRECT_TRANSLATE_VALUES:
                return True
    return False


# Legacy wrappers open each record with "<app> <version>" on a line of its own,
# e.g. "whisper-ctranslate2 0.2.4" or "whisper-faster-xxl 1.0.1". These tools
# produced the subtitle file themselves, so the application is recorded as a
# translation tag. The version is dropped: one tag per tool, not per release.
# WhisperJAV's key/value preamble does not match this shape and is not tagged
# here - those sidecars name their translator explicitly instead.
APP_LINE_RE = re.compile(r"^[ \t]*([A-Za-z][\w.+-]*)[ \t]+v?\d[\w.]*[ \t]*$", re.M)


def legacy_app_names(text: str) -> list[str]:
    """Application names recorded in a legacy sidecar's header line."""
    names = []
    for raw in APP_LINE_RE.findall(text):
        name = clean_value(raw)
        if name and name not in names:
            names.append(name)
    return names


# The "script version=0.x" wrappers record key=value lines instead of flags.
KV_ASR_RE = re.compile(r"^\s*whisper[ _]model\s*=\s*(\S+)", re.M | re.I)
KV_LOCAL_MODEL_RE = re.compile(r"^\s*local_model\s*=\s*(\S+)", re.M | re.I)
KV_DEEPL_RE = re.compile(r"^\s*deepl[ _]translation\s*=\s*(\S+)", re.M | re.I)
# postprocess_model= cleans up already-translated text and is deliberately not
# tagged: it did not produce the subtitles.

# Some wrappers write JSON with unescaped inner quotes, e.g.
#   --pass1-qwen-params "{"framer": "vad-grouped", "generator_backend": "..."}"
# which no JSON parser will accept, so fall back to reading the pairs directly.
JSON_KV_RE = re.compile(
    r"[\"']("
    + "|".join(sorted(JSON_MODEL_KEYS, key=len, reverse=True))
    + r")[\"']\s*:\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)

# Text encodings seen across sidecar generations, tried in order.
ENCODINGS = ("utf-8-sig", "utf-8", "utf-16", "cp1252")


def read_command_file(path: Path) -> str | None:
    """Decode a sidecar, or None if it is unreadable or not text at all."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in ENCODINGS:
        try:
            text = raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError, LookupError):
            continue
        # A NUL this early means the decode produced noise, not text.
        if "\x00" in text[:400]:
            continue
        return text
    return None


# Placeholders that carry no model information.
IGNORED_VALUES = {"auto", "none", "null", "default", "", "-"}

TRANSCRIPTION_PATTERNS = tuple(flag_re(f) for f in TRANSCRIPTION_FLAGS)
TRANSCRIPTION_FALLBACK_PATTERNS = tuple(
    flag_re(f) for f in TRANSCRIPTION_FALLBACK_FLAGS)
TRANSLATION_PATTERNS = tuple(flag_re(f) for f in TRANSLATION_FLAGS)
TRANSLATION_FALLBACK_PATTERNS = tuple(
    flag_re(f) for f in TRANSLATION_FALLBACK_FLAGS)
JSON_PATTERNS = tuple(flag_re(f) for f in JSON_TRANSCRIPTION_FLAGS)


def _messages(failures: list[tuple]) -> list[str]:
    """Distinct error texts from a list of (chunk, exception) failures."""
    seen: list[str] = []
    for _, exc in failures:
        text = str(exc)
        if text not in seen:
            seen.append(text)
    return seen


# Scene ids sent in one bulkSceneUpdate. Keeps a single request from growing
# unbounded on a large library while still collapsing hundreds of mutations.
# Scene ids per bulkSceneUpdate. Stash pays a fixed cost per request - one
# transaction, one SQLite commit, one round of hook dispatch - so larger chunks
# are markedly faster; client-side per-request overhead is only ~2ms and does
# not matter. The original 30s timeout, not the chunk size, is what made large
# chunks fail, and that is now a separate 300s write timeout with per-chunk
# isolation and retry. Lower this only if chunks start timing out.
SCENE_BATCH = 200

# Stash surfaces a vanished scene or tag as a SQLite integrity failure.
STALE_ID_HINTS = ("foreign key", "constraint failed")


class StashError(RuntimeError):
    """A GraphQL request failed or returned errors."""


@dataclass
class Stash:
    """Minimal Stash GraphQL client."""

    url: str
    api_key: str | None = None
    cookie: str | None = None
    timeout: int = 60
    # Mutations do real work per scene and can legitimately take minutes on a
    # large library; reads should still fail fast.
    write_timeout: int = 300
    scene_batch: int = SCENE_BATCH
    _tag_cache: dict[str, int] = field(default_factory=dict, repr=False)
    _tags_prefetched: bool = field(default=False, repr=False)

    @property
    def endpoint(self) -> str:
        base = self.url.rstrip("/")
        return base if base.endswith("/graphql") else base + "/graphql"

    def call(self, query: str, variables: dict | None = None,
             timeout: int | None = None) -> dict:
        payload = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["ApiKey"] = self.api_key
        if self.cookie:
            headers["Cookie"] = self.cookie

        req = urllib.request.Request(self.endpoint, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                body = json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:500]
            raise StashError(f"HTTP {exc.code} from {self.endpoint}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise StashError(f"Cannot reach {self.endpoint}: {exc.reason}") from exc
        except (http.client.HTTPException, OSError) as exc:
            # A server restart mid-run drops the socket; RemoteDisconnected and
            # friends are not URLError subclasses, so catch them explicitly.
            raise StashError(f"Connection to {self.endpoint} failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise StashError(f"Malformed response from {self.endpoint}: {exc}") from exc

        if body.get("errors"):
            messages = "; ".join(e.get("message", str(e)) for e in body["errors"])
            raise StashError(messages)
        return body["data"]

    # -- scenes ----------------------------------------------------------

    def find_scenes_by_path(self, path: str) -> list[dict]:
        """Scenes whose file path contains `path`, newest API-compatible form."""
        query = """
        query FindScenes($filter: SceneFilterType!) {
          findScenes(scene_filter: $filter, filter: {per_page: 100}) {
            scenes { id title files { path } tags { id name } }
          }
        }
        """
        variables = {"filter": {"path": {"value": path, "modifier": "INCLUDES"}}}
        return self.call(query, variables)["findScenes"]["scenes"]

    def find_scene(self, scene_id: str) -> dict | None:
        """A single scene by id, or None if it no longer exists."""
        query = """
        query FindScene($id: ID!) {
          findScene(id: $id) { id title files { path } tags { id name } }
        }
        """
        return self.call(query, {"id": str(scene_id)})["findScene"]

    def iter_scenes(self, path_prefix: str | None = None, page_size: int = 200):
        """Yield every scene, optionally limited to those under `path_prefix`.

        Paginates so a large library never has to fit in one response.
        """
        query = """
        query FindScenes($filter: SceneFilterType, $page: Int!, $per: Int!) {
          findScenes(scene_filter: $filter,
                     filter: {page: $page, per_page: $per, sort: "path"}) {
            count
            scenes { id title files { path } tags { id name } }
          }
        }
        """
        scene_filter = None
        if path_prefix:
            scene_filter = {"path": {"value": path_prefix, "modifier": "INCLUDES"}}

        page = 1
        seen = 0
        while True:
            variables = {"filter": scene_filter, "page": page, "per": page_size}
            result = self.call(query, variables)["findScenes"]
            scenes = result["scenes"]
            if not scenes:
                return
            yield from scenes
            seen += len(scenes)
            if seen >= result["count"]:
                return
            page += 1

    ADD_QUERY = """
        mutation AddTags($ids: [ID!], $tags: [ID!]) {
          bulkSceneUpdate(input: {ids: $ids, tag_ids: {ids: $tags, mode: ADD}}) {
            id
          }
        }
        """

    REMOVE_QUERY = """
        mutation StripTags($ids: [ID!], $tags: [ID!]) {
          bulkSceneUpdate(input: {ids: $ids, tag_ids: {ids: $tags, mode: REMOVE}}) {
            id
          }
        }
        """

    def _bulk_chunks(self, query: str, scene_ids: list[str], tag_ids: list[int],
                     on_progress=None) -> tuple[list[str], list[tuple]]:
        """Apply one mutation per chunk of scenes.

        A failing chunk no longer costs the whole group: the rest still run, and
        the caller is told exactly which scenes were missed. A timed-out chunk is
        retried once, because the write may well have landed server-side after
        the client gave up and both ADD and REMOVE are idempotent.
        """
        ok: list[str] = []
        failures: list[tuple] = []
        seen = 0
        batch = max(1, self.scene_batch)
        for start in range(0, len(scene_ids), batch):
            chunk = scene_ids[start:start + batch]
            payload = {"ids": [str(s) for s in chunk],
                       "tags": [str(t) for t in tag_ids]}
            try:
                self.call(query, payload, timeout=self.write_timeout)
                ok.extend(chunk)
            except StashError as exc:
                if "timed out" in str(exc).lower():
                    try:
                        self.call(query, payload, timeout=self.write_timeout)
                        ok.extend(chunk)
                        exc = None
                    except StashError as retry_exc:
                        exc = retry_exc
                if exc is not None:
                    failures.append((chunk, exc))
            seen += len(chunk)
            if on_progress:
                on_progress(seen, len(scene_ids))
        return ok, failures

    def add_tags(self, scene_ids: list[str], tag_ids: list[int],
                 on_progress=None) -> tuple[list[str], list[tuple]]:
        """Attach every tag to every scene, chunked. ADD leaves other tags alone."""
        return self._bulk_chunks(self.ADD_QUERY, scene_ids, tag_ids, on_progress)

    def remove_tags(self, scene_ids: list[str], tag_ids: list[int],
                    on_progress=None) -> tuple[list[str], list[tuple]]:
        """Detach every listed tag from every listed scene, chunked."""
        return self._bulk_chunks(self.REMOVE_QUERY, scene_ids, tag_ids, on_progress)

    def strip_tags(self, scene_ids: list[str], names: list[str],
                   on_progress=None) -> tuple[list[str], list[str], list[str]]:
        """Detach tags by name. Never creates a tag.

        Returns (unresolved names, scene ids not updated, error messages).
        """
        ids, unresolved = self.resolve_tags(names, create=False)
        if not ids:
            return unresolved, list(scene_ids), []
        _, failures = self.remove_tags(scene_ids, ids, on_progress)
        return unresolved, [i for c, _ in failures for i in c], _messages(failures)

    def resolve_tags(self, names: list[str], create: bool = True
                     ) -> tuple[list[int], list[str]]:
        """(ids, unresolvable names) for a list of tag names."""
        ids: list[int] = []
        unresolved: list[str] = []
        for name in names:
            tag_id = self.ensure_tag(name, create=create)
            if tag_id is None:
                unresolved.append(name)
            elif tag_id not in ids:
                ids.append(tag_id)
        return ids, unresolved

    def apply_tags(self, scene_ids: list[str], names: list[str],
                   create: bool = True, on_progress=None
                   ) -> tuple[list[str], list[str], list[str]]:
        """Resolve names and attach them, re-resolving once if an id is stale.

        Chunks that fail no longer abort the rest of the group. Returns
        (unresolved names, scene ids not updated, error messages).
        """
        ids, unresolved = self.resolve_tags(names, create=create)
        if not ids:
            return unresolved, list(scene_ids), []

        _, failures = self.add_tags(scene_ids, ids, on_progress)

        # A tag deleted from the UI mid-run leaves a dangling cached id, which
        # Stash rejects with a foreign key error. Drop the cache and retry only
        # the chunks that hit it.
        stale = [f for f in failures
                 if any(h in str(f[1]).lower() for h in STALE_ID_HINTS)]
        if stale:
            for name in names:
                self._tag_cache.pop(name, None)
            ids, unresolved = self.resolve_tags(names, create=create)
            if ids:
                retry_ids = [i for c, _ in stale for i in c]
                _, retried = self.add_tags(retry_ids, ids)
                failures = [f for f in failures if f not in stale] + retried

        return unresolved, [i for c, _ in failures for i in c], _messages(failures)

    def prefetch_tags(self) -> int:
        """Cache every existing tag in one pass instead of a query per name."""
        query = """
        query AllTags($page: Int!, $per: Int!) {
          findTags(filter: {page: $page, per_page: $per}) {
            count
            tags { id name }
          }
        }
        """
        page, seen = 1, 0
        while True:
            result = self.call(query, {"page": page, "per": 1000})["findTags"]
            tags = result["tags"]
            if not tags:
                break
            for tag in tags:
                self._tag_cache.setdefault(tag["name"], int(tag["id"]))
            seen += len(tags)
            if seen >= result["count"]:
                break
            page += 1
        self._tags_prefetched = True
        return seen

    def ensure_tag(self, name: str, create: bool = True) -> int | None:
        """Return the id of `name`, creating the tag if it does not exist."""
        if name in self._tag_cache:
            return self._tag_cache[name]

        if self._tags_prefetched:
            # Every existing tag is already cached, so a name that is not there
            # under any casing genuinely does not exist. No query needed.
            lowered = name.lower()
            for known, known_id in self._tag_cache.items():
                if known.lower() == lowered:
                    self._tag_cache[name] = known_id
                    return known_id
        else:
            found = self.lookup_tag(name)
            if found is not None:
                self._tag_cache[name] = found
                return found

        if not create:
            return None

        mutation = """
        mutation CreateTag($name: String!) {
          tagCreate(input: {name: $name}) { id name }
        }
        """
        try:
            tag = self.call(mutation, {"name": name})["tagCreate"]
        except StashError:
            # Another worker may have created it between our lookup and this
            # mutation; Stash rejects the duplicate name. Re-read before giving up.
            found = self.lookup_tag(name)
            if found is None:
                raise
            self._tag_cache[name] = found
            return found

        self._tag_cache[name] = int(tag["id"])
        return self._tag_cache[name]

    def lookup_tag(self, name: str) -> int | None:
        """The id of an existing tag named `name`, or None."""
        query = """
        query FindTags($filter: TagFilterType!) {
          findTags(tag_filter: $filter, filter: {per_page: 100}) {
            tags { id name }
          }
        }
        """
        variables = {"filter": {"name": {"value": name, "modifier": "EQUALS"}}}
        for tag in self.call(query, variables)["findTags"]["tags"]:
            # EQUALS is case-insensitive server-side; accept that as a match
            # rather than creating a near-duplicate tag.
            if tag["name"].lower() == name.lower():
                return int(tag["id"])
        return None


def parse_path_map(rules: list[str]) -> list[tuple[str, str]]:
    """Turn ``LOCAL=REMOTE`` strings into ordered rewrite pairs."""
    mapping = []
    for rule in rules:
        local, sep, remote = rule.partition("=")
        if not sep or not local or not remote:
            raise argparse.ArgumentTypeError(
                f"--path-map expects LOCAL=REMOTE, got {rule!r}"
            )
        mapping.append((local.rstrip("/"), remote.rstrip("/")))
    # Longest local prefix first, so nested mounts win over their parents.
    mapping.sort(key=lambda pair: len(pair[0]), reverse=True)
    return mapping


def remap(path: Path, mapping: list[tuple[str, str]]) -> str:
    """Rewrite a local path into its Stash-side equivalent."""
    text = str(path)
    for local, remote in mapping:
        if text == local or text.startswith(local + os.sep):
            remainder = text[len(local):].lstrip(os.sep)
            return str(PurePosixPath(remote) / remainder) if remainder else remote
    return text


def clean_value(raw: str) -> str | None:
    """Normalise one captured flag value, or None if it is not a model."""
    value = raw.strip().strip("\"'").rstrip(",;").strip()
    # A leading dash means the flag took no value and we captured the next flag.
    if not value or value.startswith("-"):
        return None
    if value.lower() in IGNORED_VALUES:
        return None
    return ALIASES.get(value.lower(), value)


def extract_values(text: str, pattern: re.Pattern[str]) -> list[str]:
    """Every distinct value for one flag in a command file, in order."""
    values = []
    for raw in pattern.findall(text):
        value = clean_value(raw)
        if value and value not in values:
            values.append(value)
    return values


def walk_json_models(node: object) -> list[str]:
    """Model ids nested anywhere inside a parsed JSON blob."""
    found = []
    if isinstance(node, dict):
        for key, sub in node.items():
            if key.lower() in JSON_MODEL_KEYS and isinstance(sub, str):
                value = clean_value(sub)
                if value:
                    found.append(value)
            else:
                found.extend(walk_json_models(sub))
    elif isinstance(node, list):
        for sub in node:
            found.extend(walk_json_models(sub))
    return found


def extract_json_models(text: str) -> list[str]:
    """Model ids hiding inside JSON-valued flags such as --generator-config."""
    found = []
    for pattern in JSON_PATTERNS:
        for raw in pattern.findall(text):
            blob = raw.strip().strip("\"'")
            try:
                found.extend(walk_json_models(json.loads(blob)))
            except (json.JSONDecodeError, ValueError):
                continue

    # Wrappers that double-quote a JSON blob containing double quotes produce
    # something no parser accepts, and the flag capture stops at the first inner
    # quote. Read the key/value pairs straight out of the command text instead.
    for _, value in JSON_KV_RE.findall(text):
        cleaned = clean_value(value)
        if cleaned and cleaned not in found:
            found.append(cleaned)
    return found


def extract_kv_models(text: str) -> tuple[list[str], list[str]]:
    """(asr, translation) models from the key=value wrapper format."""
    asr = [v for v in (clean_value(m) for m in KV_ASR_RE.findall(text)) if v]

    translation = [v for v in (clean_value(m) for m in KV_LOCAL_MODEL_RE.findall(text)) if v]
    for value in KV_DEEPL_RE.findall(text):
        if value.strip().lower() in ("enabled", "true", "yes", "1"):
            translation.append("DeepL")
    return asr, translation


def collect(text: str, patterns: tuple[re.Pattern[str], ...]) -> list[str]:
    """De-duplicated values across several flag patterns, in order."""
    found: list[str] = []
    for pattern in patterns:
        for value in extract_values(text, pattern):
            if value not in found:
                found.append(value)
    return found


def extract_models(text: str) -> tuple[list[str], list[str]]:
    """(transcription models, translation models) named in a command file.

    Only the most recent run record is considered, user-supplied prose is
    stripped first, and values are de-duplicated per category after aliasing.
    When the ASR pass translated directly to English there is no separate
    transcription step, so its model is reported as a translation model.
    """
    text = mask_free_text(latest_run(text))
    kv_asr, kv_translation = extract_kv_models(text)

    asr = collect(text, TRANSCRIPTION_PATTERNS)
    for value in extract_json_models(text) + kv_asr:
        if value not in asr:
            asr.append(value)
    if not asr:
        asr = collect(text, TRANSCRIPTION_FALLBACK_PATTERNS)

    translation = collect(text, TRANSLATION_PATTERNS)
    for value in kv_translation:
        if value not in translation:
            translation.append(value)
    if not translation:
        translation = collect(text, TRANSLATION_FALLBACK_PATTERNS)

    apps = legacy_app_names(text)
    if apps:
        # Legacy wrapper: the application is what subtitled the file, so it
        # takes the Subtitled: tag and the ASR model stays a transcription
        # model whatever --task says. These tools name themselves in a header
        # line; WhisperJAV does not, so this branch never applies to it.
        for name in apps:
            if name not in translation:
                translation.append(name)
    elif is_direct_translation(text):
        # The ASR pass emitted English, so it never produced a native
        # transcript and cannot earn a Transcription: tag either way.
        if not translation:
            translation = asr
        # When a separate LLM translator is also documented, that LLM produced
        # the subtitles that ended up on disk and takes the Subtitled: tag
        # outright; the ASR model is not reported alongside it.
        asr = []

    # A model must never be reported under both prefixes: "Transcription: medium"
    # alongside "Subtitled: medium" says nothing useful and reads as a bug. The
    # translation side wins, because that names what produced the subtitle file
    # that actually reached the viewer.
    asr = [value for value in asr if value not in translation]

    return asr, translation


def extract_tag_names(text: str) -> list[str]:
    """All Stash tag names implied by a command file's model flags."""
    transcription, translation = extract_models(text)
    return (
        [TRANSCRIPTION_PREFIX + m for m in transcription]
        + [TRANSLATION_PREFIX + m for m in translation]
    )


def strip_suffix(name: str) -> str:
    """Drop whichever recognised sidecar suffix a filename carries."""
    for suffix in SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def resolve_media_path(command_file: Path) -> Path:
    """Best guess at the media file a *-whisper-command.txt file describes."""
    stem = strip_suffix(command_file.name)
    candidate = command_file.with_name(stem)

    # "movie.mkv-whisper-command.txt" -> the name already has its extension.
    if candidate.exists() or candidate.suffix.lower() in VIDEO_EXTENSIONS:
        return candidate

    # "movie-whisper-command.txt" -> look for a sibling media file.
    for ext in VIDEO_EXTENSIONS:
        sibling = command_file.with_name(stem + ext)
        if sibling.exists():
            return sibling

    return candidate


def command_file_for(media_path: Path) -> Path | None:
    """The ``*-whisper-command.txt`` belonging to a media file, if it exists.

    Inverse of `resolve_media_path`, for use when Stash hands us the scene path
    and we need to find the sidecar. Handles both naming styles:
    ``movie.mkv-whisper-command.txt`` and ``movie-whisper-command.txt``.
    """
    for suffix in SUFFIXES:
        for candidate in (
            media_path.with_name(media_path.name + suffix),
            media_path.with_name(media_path.stem + suffix),
        ):
            if candidate.is_file():
                return candidate
    return None


def scene_tag_names(scene: dict) -> set[str]:
    """Names of the tags a scene already carries."""
    return {t["name"] for t in (scene.get("tags") or []) if t.get("name")}


def scene_paths(scene: dict) -> list[str]:
    """Every file path attached to a scene."""
    return [f["path"] for f in (scene.get("files") or []) if f.get("path")]


def match_scene(scenes: list[dict], remote_path: str, strict: bool = False) -> dict | None:
    """Pick the scene that actually owns `remote_path`."""
    for scene in scenes:
        for file in scene.get("files") or []:
            if file["path"] == remote_path:
                return scene

    if strict:
        return None

    # Fall back to a unique basename match when the two sides disagree on the
    # directory prefix but agree on the file itself. This also papers over a
    # wrong --path-map, so --strict-path exists to catch that instead.
    basename = PurePosixPath(remote_path).name
    candidates = [
        scene
        for scene in scenes
        if any(PurePosixPath(f["path"]).name == basename for f in scene.get("files") or [])
    ]
    return candidates[0] if len(candidates) == 1 else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Tag Stash scenes with the Whisper models used on them.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("directory", type=Path, help="directory to scan (recursively)")
    parser.add_argument(
        "--url",
        default=os.environ.get("STASH_URL", "http://localhost:9999"),
        help="Stash base URL (env: STASH_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("STASH_API_KEY"),
        help="Stash API key (env: STASH_API_KEY)",
    )
    parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        metavar="LOCAL=REMOTE",
        help="rewrite a local path prefix to its Stash-side equivalent; "
             "repeatable, longest prefix wins",
    )
    parser.add_argument(
        "--strict-path",
        action="store_true",
        help="require an exact path match; without this a unique filename match "
             "is accepted as a fallback, which can mask a wrong --path-map",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=SCENE_BATCH,
        metavar="N",
        help=f"scenes per bulk update request (default: {SCENE_BATCH}); "
             "lower it only if requests time out",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="also remove Transcription:/Subtitled: tags the sidecar no longer "
             "names, e.g. after re-running whisper with a different model",
    )
    parser.add_argument(
        "--no-create-tags",
        action="store_true",
        help="skip scenes whose tag does not already exist instead of creating it",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without writing to Stash",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="log every file")
    args = parser.parse_args(argv)

    if not args.directory.is_dir():
        parser.error(f"not a directory: {args.directory}")

    try:
        mapping = parse_path_map(args.path_map)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    stash = Stash(args.url, args.api_key, scene_batch=max(1, args.batch_size))
    command_files = sorted(
        {f for suffix in SUFFIXES for f in args.directory.rglob("*" + suffix)}
    )
    if not command_files:
        print(f"No {' or '.join(SUFFIXES)} files found under {args.directory}")
        return 0

    try:
        stash.prefetch_tags()
    except StashError as exc:
        # Not fatal: ensure_tag falls back to querying each name individually.
        print(f"! could not prefetch tags ({exc})", file=sys.stderr)

    tagged = skipped = unmatched = pruned = 0

    for command_file in command_files:
        text = read_command_file(command_file)
        if text is None:
            print(f"! {command_file}: unreadable or not text", file=sys.stderr)
            skipped += 1
            continue

        tag_names = extract_tag_names(text)
        if not tag_names:
            if args.verbose:
                print(f"- {command_file.name}: no --pass1-model or --translate-model")
            skipped += 1
            continue

        media_path = resolve_media_path(command_file)
        remote_path = remap(media_path, mapping)

        try:
            scenes = stash.find_scenes_by_path(remote_path)
            scene = match_scene(scenes, remote_path, args.strict_path)
            if scene is None and not args.strict_path:
                scenes = stash.find_scenes_by_path(PurePosixPath(remote_path).name)
                scene = match_scene(scenes, remote_path, strict=False)
        except StashError as exc:
            print(f"! {media_path.name}: search failed ({exc})", file=sys.stderr)
            skipped += 1
            continue

        if scene is None:
            print(f"? no scene for {remote_path}", file=sys.stderr)
            unmatched += 1
            continue

        existing = scene_tag_names(scene)
        pending = [name for name in tag_names if name not in existing]
        # Only ever a candidate for removal if the sidecar named something; an
        # empty parse means "unknown", not "this scene should have no tags".
        stale = sorted(
            name for name in existing
            if args.prune and tag_names
            and name.startswith(MANAGED_PREFIXES) and name not in tag_names
        )

        if stale and not args.dry_run:
            _, failed, errors = stash.strip_tags([scene["id"]], stale)
            for message in errors:
                print(f"! {remote_path}: pruning failed ({message})", file=sys.stderr)
            if failed:
                skipped += 1
            else:
                for name in stale:
                    print(f"- scene {scene['id']}: {name}  ({remote_path})")
                    pruned += 1
        elif stale:
            for name in stale:
                print(f"[dry-run] scene {scene['id']}: -{name!r}  ({remote_path})")
                pruned += 1

        if not pending:
            if args.verbose and not stale:
                print(f"= scene {scene['id']}: already current  ({remote_path})")
            continue

        if args.dry_run:
            for tag_name in pending:
                print(f"[dry-run] scene {scene['id']}: +{tag_name!r}  ({remote_path})")
                tagged += 1
            continue

        # One request per scene rather than one per tag.
        unresolved, failed, errors = stash.apply_tags(
            [scene["id"]], pending, create=not args.no_create_tags)
        for message in errors:
            print(f"! {remote_path}: tagging failed ({message})", file=sys.stderr)
        if failed:
            skipped += 1
            continue

        for tag_name in unresolved:
            print(f"- {tag_name!r} does not exist, skipping {remote_path}")
            skipped += 1
        for tag_name in pending:
            if tag_name in unresolved:
                continue
            print(f"+ scene {scene['id']}: {tag_name}  ({remote_path})")
            tagged += 1

    print(
        f"\n{len(command_files)} command file(s): "
        f"{tagged} tag(s) applied, {pruned} removed, "
        f"{unmatched} unmatched, {skipped} skipped"
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
