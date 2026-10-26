"""jasna_batch_core.py -- the parts of the jasna-batch plugin that need no
Stash: preset parsing, output naming, the sidecar copy plan, the NFO edits
and the command sidecar. Pure functions over paths and text, so they are
testable without a Stash instance (tests/test_core.py).

The NFO edits are a port of clean_nfo_flexible.py (Scott's CLI): strip the
tags Jellyfin/Kodi leave behind (art, fileinfo, resume, ...), drop actor
<thumb>s, insert the marker after the first " - " of the title, add genres
after the last <genre>, optionally unescape HTML in the text fields, and
pretty-print keeping the original XML declaration.
"""
from __future__ import annotations

import fnmatch
import html
import os
import re
import shlex
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Tuple
from xml.dom import minidom

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".flv",
              ".webm", ".vob", ".iso", ".divx", ".rm", ".rmvb", ".3gp", ".ogv"}
OUTPUT_EXT = ".mp4"
# Flags the bridge refuses in a preset (it sets input/output itself); checked
# here too so Check settings can say so before anything is queued.
FLAG_DENYLIST = frozenset({
    "-h", "--help", "--version", "--benchmark", "--benchmark-filter", "--benchmark-video",
    "--input", "--output", "--output-pattern", "--working-directory", "--segments",
    "--stream", "--stream-port", "--stream-segment-duration", "--no-browser",
    "--license-email", "--license-key", "--license-code", "--activation-key", "--machine-key",
    "--post-export-action", "--post-export-command", "--post-export-video-command",
})
PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,39}$")
RESERVED_PRESET_NAMES = {"queued", "processing", "done", "failed"}


# ---------------------------------------------------------------------------
# Presets: "name = flags # comment; name2 = flags"
# ---------------------------------------------------------------------------

@dataclass
class Preset:
    name: str
    flags: List[str]
    comment: str = ""

    @property
    def tag(self) -> str:
        return trigger_tag(self.name)


def trigger_tag(preset_name: str) -> str:
    return f"Jasna: {preset_name}"


def parse_presets(text: str) -> Tuple[List[Preset], List[str]]:
    """Parse the Presets setting. Returns (presets, problems); an entry
    with a problem is skipped and described. Same syntax as jasna-switch's
    Custom presets: entries separated by ";", "name = flags", an unquoted
    "#" after the flags starts a comment."""
    presets: List[Preset] = []
    problems: List[str] = []
    seen = set()
    for raw in (text or "").split(";"):
        entry = raw.strip()
        if not entry:
            continue
        if "=" not in entry:
            problems.append(f"{entry!r}: expected 'name = flags'")
            continue
        name, rest = entry.split("=", 1)
        name = name.strip()
        if not PRESET_NAME_RE.match(name):
            problems.append(f"{name!r}: a preset name is 1-40 characters of letters, digits, space, . _ + -")
            continue
        if name.lower() in RESERVED_PRESET_NAMES:
            problems.append(f"{name!r}: that name is reserved for a status tag")
            continue
        if name.lower() in seen:
            problems.append(f"{name!r}: duplicate preset name")
            continue
        try:
            tokens = shlex.split(rest, comments=True)
        except ValueError as err:
            problems.append(f"{name!r}: cannot parse flags ({err})")
            continue
        comment = ""
        if "#" in rest:
            # shlex dropped it; keep the human text for logs
            comment = rest.split("#", 1)[1].strip()
        if not tokens:
            problems.append(f"{name!r}: no flags (the bridge needs at least one; use e.g. --codec hevc)")
            continue
        if not tokens[0].startswith("-"):
            problems.append(f"{name!r}: flags must start with a flag, not {tokens[0]!r}")
            continue
        bad = [t for t in tokens if (t.split("=", 1)[0] if t.startswith("-") else "") in FLAG_DENYLIST]
        if bad:
            problems.append(f"{name!r}: {', '.join(bad)} not allowed in a preset (the bridge sets those)")
            continue
        seen.add(name.lower())
        presets.append(Preset(name, tokens, comment))
    return presets, problems


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------

def insert_marker(name: str, marker: str) -> str:
    """'CODE Performer - Title (date)' -> 'CODE Performer - [LTX] Title (date)'.
    The marker goes after the first ' - ' (clean_nfo_flexible's rule); a name
    without one gets it as a prefix. Idempotent."""
    marker = marker.strip()
    if not marker or marker in name:
        return name
    if " - " in name:
        return name.replace(" - ", f" - {marker} ", 1)
    return f"{marker} {name}"


def preset_slug(preset_name: str) -> str:
    """The preset's name as it appears in the output file name: spaces and
    anything outside [A-Za-z0-9._+-] become '-'."""
    return re.sub(r"[^A-Za-z0-9._+-]+", "-", preset_name.strip()).strip("-") or "jasna"


def output_stem(source_stem: str, preset_name: str) -> str:
    return f"{source_stem}_{preset_slug(preset_name)}_jasna"


@dataclass
class OutputPlan:
    source: str
    source_dir: str
    source_stem: str
    dest_dir: str
    new_stem: str
    video: str          # dest_dir/new_stem.mp4


def plan_output(source: str, target_root: str, preset_name: str, marker: str) -> OutputPlan:
    """Where a restoration of `source` goes: a folder under target_root
    named after the source's folder with the marker inserted, so every part
    of a multi-file release lands in the same place."""
    source_dir = os.path.dirname(source)
    source_stem = os.path.splitext(os.path.basename(source))[0]
    folder = insert_marker(os.path.basename(source_dir.rstrip("/")) or source_stem, marker)
    dest_dir = os.path.join(target_root.rstrip("/"), folder)
    new_stem = output_stem(source_stem, preset_name)
    return OutputPlan(source, source_dir, source_stem, dest_dir, new_stem, os.path.join(dest_dir, new_stem + OUTPUT_EXT))


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------

@dataclass
class SidecarPlan:
    renames: List[Tuple[str, str]] = field(default_factory=list)   # (src, dst): bound to this file's stem
    copies: List[Tuple[str, str]] = field(default_factory=list)    # folder-level files (fanart.jpg, ...)
    trees: List[Tuple[str, str]] = field(default_factory=list)     # folder-level dirs (extrafanart/, .actors/)
    skipped: List[Tuple[str, str]] = field(default_factory=list)   # (name, why)
    nfo: Optional[Tuple[str, str]] = None                          # (src .nfo, dst .nfo)


def is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def stem_bound(name: str, stem: str) -> bool:
    """True if `name` belongs to the file with this stem: the stem itself
    followed by '.', '-' or '_' (CODE-1.en.srt, CODE-1-whisper-command.txt,
    CODE-1_CAPS.jpg). The separator matters: CODE-10.mkv is not CODE-1's."""
    if not name.startswith(stem):
        return False
    rest = name[len(stem):]
    return rest == "" or rest[0] in "._-"


def plan_sidecars(plan: OutputPlan, ignore_patterns: Iterable[str] = ()) -> SidecarPlan:
    out = SidecarPlan()
    try:
        names = sorted(os.listdir(plan.source_dir))
    except OSError as err:
        out.skipped.append((plan.source_dir, f"cannot list: {err}"))
        return out
    patterns = [p.strip() for p in ignore_patterns if p and p.strip()]
    other_stems = [os.path.splitext(n)[0] for n in names if is_video(n) and os.path.splitext(n)[0] != plan.source_stem]
    # Longest stems first, so CODE-1-pt10 wins over CODE-1-pt1.
    other_stems.sort(key=len, reverse=True)
    for name in names:
        src = os.path.join(plan.source_dir, name)
        if any(fnmatch.fnmatch(name.lower(), p.lower()) for p in patterns):
            out.skipped.append((name, "ignore pattern"))
            continue
        if os.path.isdir(src):
            out.trees.append((src, os.path.join(plan.dest_dir, name)))
            continue
        if is_video(name):
            out.skipped.append((name, "video file"))
            continue
        if stem_bound(name, plan.source_stem) and (
                not any(stem_bound(name, o) and len(o) > len(plan.source_stem) for o in other_stems)):
            new_name = plan.new_stem + name[len(plan.source_stem):]
            if name.lower().endswith(".nfo") and name[len(plan.source_stem):].lower() == ".nfo":
                out.nfo = (src, os.path.join(plan.dest_dir, plan.new_stem + ".nfo"))
            else:
                out.renames.append((src, os.path.join(plan.dest_dir, new_name)))
            continue
        bound_elsewhere = next((o for o in other_stems if stem_bound(name, o)), None)
        if bound_elsewhere:
            out.skipped.append((name, f"belongs to {bound_elsewhere}"))
            continue
        out.copies.append((src, os.path.join(plan.dest_dir, name)))
    return out


# ---------------------------------------------------------------------------
# NFO edits (port of clean_nfo_flexible.py)
# ---------------------------------------------------------------------------

TAGS_TO_REMOVE = [
    'lockdata', 'dateadded', 'art', 'isuserfavorite',
    'playcount', 'watched', 'resume', 'fileinfo',
    'votes', 'tag', 'tags', 'lastplayed', 'set', 'thumb', 'fanart', 'path', 'writer', 'credits', 'file',
    'filename', 'catalog', 'comment', 'notes', 'people', 'filenameandpath',
]
HTML_FIELDS_TO_CLEAN = ['plot', 'outline', 'tagline', 'title', 'originaltitle', 'original_title']


def _remove_tags_recursively(parent: ET.Element) -> None:
    for child in list(parent):
        if child.tag in TAGS_TO_REMOVE:
            parent.remove(child)
        else:
            _remove_tags_recursively(child)


def _remove_actor_thumbs(root: ET.Element) -> None:
    for actor in root.findall('.//actor'):
        for thumb in actor.findall('thumb'):
            actor.remove(thumb)


def _detect_indent(original_text: str, tag_name: str) -> str:
    match = re.search(rf'^[ \t]+(?=<{tag_name}\b)', original_text, re.MULTILINE)
    return match.group(0) if match else "  "


def _add_genres(root: ET.Element, original_text: str, genres_to_add: Iterable[str]) -> List[str]:
    """After the last <genre>, else before the first <actor>, else at the
    end. Existing genres (exact text) are not duplicated. Returns what was
    added."""
    if root.tag != "movie":
        return []
    existing = [g.text for g in root.findall("genre") if g.text]
    wanted = []
    for g in genres_to_add:
        g = (g or "").strip()
        if g and g not in existing and g not in wanted:
            wanted.append(g)
    if not wanted:
        return []
    children = list(root)
    last_genre = max((i for i, c in enumerate(children) if c.tag == "genre"), default=-1)
    if last_genre != -1:
        pos = last_genre + 1
    else:
        first_actor = next((i for i, c in enumerate(children) if c.tag == "actor"), None)
        pos = first_actor if first_actor is not None else len(children)
    for text in wanted:
        el = ET.Element("genre")
        el.text = text
        root.insert(pos, el)
        pos += 1
    return wanted


def clean_html_escaped_text(text: Optional[str]) -> Optional[str]:
    if text is None:
        return text
    cleaned = html.unescape(text)
    cleaned = re.sub(r'(?i)<br\s*/?>', ' ', cleaned)
    cleaned = re.sub(r'(?i)</p\s*>', ' ', cleaned)
    cleaned = re.sub(r'(?i)<p\s*>', '', cleaned)
    cleaned = re.sub(r'<[^>]+>', '', cleaned)
    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    cleaned = re.sub(r'[ \t]{2,}', ' ', cleaned)
    return cleaned.strip()


def _clean_html_in_fields(root: ET.Element) -> bool:
    changed = False
    for tag in HTML_FIELDS_TO_CLEAN:
        for el in root.findall(tag):
            if el.text:
                new = clean_html_escaped_text(el.text)
                if new != el.text:
                    el.text, changed = new, True
    for actor in root.findall('.//actor'):
        for tag in ('name', 'role'):
            el = actor.find(tag)
            if el is not None and el.text:
                new = clean_html_escaped_text(el.text)
                if new != el.text:
                    el.text, changed = new, True
    return changed


def escape_bare_ampersands(text: str) -> str:
    return re.sub(r'&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)', '&amp;', text)


def _pretty(element: ET.Element) -> str:
    rough = ET.tostring(element, encoding='utf-8')
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    lines = pretty.splitlines()
    if lines and lines[0].startswith("<?xml"):
        lines = lines[1:]
    return "\n".join(line for line in lines if line.strip())


@dataclass
class NfoResult:
    text: str
    title_before: Optional[str]
    title_after: Optional[str]
    genres_added: List[str]
    fixed_ampersands: bool = False


def edit_nfo(original_text: str, marker: str, genres: Iterable[str], clean_html: bool = False) -> NfoResult:
    """The NFO as it should be written next to the restored file. Raises
    ET.ParseError if the text is not XML even after escaping bare '&'."""
    text = original_text.lstrip('﻿').lstrip()
    m = re.match(r'(<\?xml.*?\?>)', text)
    declaration = m.group(1) if m else "<?xml version='1.0' encoding='utf-8'?>"
    fixed = False
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        root = ET.fromstring(escape_bare_ampersands(text))
        fixed = True
    _remove_tags_recursively(root)
    _remove_actor_thumbs(root)
    title_el = root.find('title')
    before = title_el.text if title_el is not None else None
    if title_el is not None and title_el.text:
        title_el.text = insert_marker(title_el.text, marker)
    added = _add_genres(root, text, genres)
    if clean_html:
        _clean_html_in_fields(root)
    body = _pretty(root)
    return NfoResult(declaration + "\n" + body + "\n", before,
                     title_el.text if title_el is not None else None, added, fixed)


# ---------------------------------------------------------------------------
# Command sidecar ({new_stem}-jasna-command.txt)
# ---------------------------------------------------------------------------

def _fmt_time(epoch: Optional[float]) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(epoch)) if epoch else "-"


def _fmt_duration(seconds: Optional[float]) -> str:
    if not seconds:
        return "-"
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def _audio_line(job: dict) -> str:
    """The sidecar's Audio line from the bridge's audio_result: what the
    output fix-up did to the audio, and whether the edit list was rewritten."""
    r = job.get("audio_result") or {}
    if r.get("from"):
        text = (f"{r.get('from')} -> {r.get('to')} {r.get('bitrate')} ({r.get('channels')}ch), "
                f"re-encoded by the bridge with ffmpeg after Jasna ({r.get('seconds')} s)")
    elif job.get("audio"):
        text = f"as Jasna wrote it (already {job['audio']}, or see Notes)"
    else:
        text = "as Jasna wrote it (copied from the source)"
    if r.get("edit_list"):
        text += "; edit list rewritten by the bridge (stream-copy remux)"
    return text


def command_sidecar(job: dict, preset: Optional[Preset], plugin_version: str) -> str:
    """Text written next to the restored file: what ran, how long it took,
    where it came from. Modeled on the -whisper-command.txt sidecars."""
    cmd = job.get("command") or []
    lines = [
        f"Jasna batch (stash jasna-batch plugin {plugin_version})",
        f"Jasna version    : {job.get('jasna_version') or 'unknown'}",
        f"Preset           : {job.get('preset')}" + (f"  # {preset.comment}" if preset and preset.comment else ""),
        f"Preset flags     : {' '.join(job.get('flags') or [])}",
        f"Source file      : {job.get('stash_source')}",
        f"Source size      : {(job.get('source_bytes') or 0) / 1e9:.2f} GB",
        f"Output file      : {job.get('stash_output')}",
        f"Output size      : {(job.get('output_bytes') or 0) / 1e9:.2f} GB",
        "Audio            : " + _audio_line(job),
        f"Queued           : {_fmt_time(job.get('created'))}",
        f"Started          : {_fmt_time(job.get('started'))}",
        f"Finished         : {_fmt_time(job.get('finished'))}",
        f"Duration         : {_fmt_duration(job.get('duration_s'))}",
        f"Exit code        : {job.get('exit_code')}",
        f"Attempts         : {job.get('attempts')}",
        f"Bridge job id    : {job.get('id')}",
        f"Stash scene id   : {job.get('scene_id')} (the original)",
        "",
        "Command:",
        " ".join(shlex.quote(c) for c in cmd),
        "",
    ]
    if job.get("notes"):
        lines += ["Notes:"] + [f"  - {n}" for n in job["notes"]] + [""]
    return "\n".join(lines)
