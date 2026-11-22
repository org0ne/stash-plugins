"""
censored_archiver_core.py -- the parts of censored-archiver that need no
Stash: studio code matching, where a folder goes in the archive, the move
plan and the undo log record. Pure functions over paths and dicts, so they
are tested without a server (tests/test_core.py).
"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List, Optional, Set, Tuple

VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".wmv", ".mov", ".m4v", ".ts", ".m2ts", ".mpg", ".mpeg", ".flv",
              ".webm", ".vob", ".iso", ".divx", ".rm", ".rmvb", ".3gp", ".ogv"}
# A scene's own folder sits this many levels below its censored root
# (Censored / Censored HD / ABP-984 ... /). Anything shallower is a grouping
# folder full of other scenes and is never moved.
MIN_FOLDER_DEPTH = 2


def is_video(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in VIDEO_EXTS


def norm_code(code: Optional[str]) -> str:
    """Studio codes compare trimmed and case-folded, otherwise exactly."""
    return (code or "").strip().upper()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def clean_dir(path: str) -> str:
    """A folder path compared by prefix: one leading '/', no trailing one
    (Stash keeps a library as '//storage/.../Uncensored/' if typed so)."""
    path = path.strip()
    if not path:
        return ""
    return ("/" + path.lstrip("/")).rstrip("/") or "/"


def is_under(path: str, root: str) -> bool:
    """True if `path` is `root` or inside it."""
    path, root = clean_dir(path), clean_dir(root)
    return bool(root) and (path == root or path.startswith(root + "/"))


def parse_roots(text: str) -> List[str]:
    """A comma-separated list of folders (names may contain spaces, not
    commas), cleaned and de-duplicated in order."""
    out: List[str] = []
    for part in (text or "").split(","):
        path = clean_dir(part)
        if path and path not in out:
            out.append(path)
    return out


def covering_root(path: str, roots: Iterable[str]) -> Optional[str]:
    """The most specific of `roots` that holds `path`, if any."""
    covering = [clean_dir(r) for r in roots if is_under(path, r)]
    return max(covering, key=len) if covering else None


def nesting_problems(groups: Dict[str, List[str]]) -> List[str]:
    """Roots of different kinds (censored / reference / archive) may not be
    inside one another: a moved folder would still match, or a reference
    scene would count as censored."""
    problems = []
    names = list(groups)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            for ra in groups[a]:
                for rb in groups[b]:
                    if is_under(ra, rb) or is_under(rb, ra):
                        problems.append(f"{a} root {ra} and {b} root {rb} are inside one another")
    return problems


def archive_destination(folder: str, censored_roots: Iterable[str],
                        archive_root: str) -> Tuple[Optional[str], str]:
    """Where a censored scene folder goes: {archive_root}/{censored root's
    folder name}/{path inside it}, so Censored/Censored HD/X/ lands at
    Archived/Censored/Censored HD/X/. (None, why) for a folder that is not
    moved."""
    folder = clean_dir(folder)
    if is_under(folder, archive_root):
        return None, "already in the archive"
    root = covering_root(folder, censored_roots)
    if not root:
        return None, "not under a censored root"
    rel = folder[len(root) + 1:] if folder != root else ""
    depth = len(rel.split("/")) if rel else 0
    if depth < MIN_FOLDER_DEPTH:
        return None, (f"only {depth} level(s) below {root}; a scene's own folder is at least "
                      f"{MIN_FOLDER_DEPTH} deep, so this one holds other scenes")
    return os.path.join(clean_dir(archive_root), os.path.basename(root), rel), ""


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def reference_codes(scenes: Iterable[dict], reference_roots: List[str]) -> Dict[str, Set[str]]:
    """Normalised code -> names of the reference libraries holding a scene
    with that code. Scenes without a code are ignored."""
    out: Dict[str, Set[str]] = {}
    for scene in scenes:
        code = norm_code(scene.get("code"))
        if not code:
            continue
        for f in scene.get("files") or []:
            root = covering_root(f.get("path") or "", reference_roots)
            if root:
                out.setdefault(code, set()).add(os.path.basename(root))
    return out


def candidate_folders(scenes: Iterable[dict], censored_roots: List[str],
                      refcodes: Dict[str, Set[str]]) -> Dict[str, List[dict]]:
    """Folder -> the censored files in it whose scene's code has a reference
    version: [{"scene_id", "code", "file_id", "path", "matched"}]. Only files
    under a censored root count; a scene's other files stay where they are."""
    out: Dict[str, List[dict]] = {}
    for scene in scenes:
        code = norm_code(scene.get("code"))
        if not code or code not in refcodes:
            continue
        for f in scene.get("files") or []:
            path = f.get("path") or ""
            if covering_root(path, censored_roots):
                out.setdefault(os.path.dirname(path), []).append({
                    "scene_id": scene["id"], "code": code, "file_id": f["id"], "path": path,
                    "matched": sorted(refcodes[code])})
    return out


def merge_nested(folders: Dict[str, List[dict]]) -> Dict[str, List[dict]]:
    """Candidates in a subfolder of another candidate folder (a scene's
    extras/ part) belong to the outer folder, which moves whole."""
    out: Dict[str, List[dict]] = {}
    for folder in sorted(folders, key=lambda f: (f.count("/"), f)):
        outer = next((o for o in out if is_strictly_under(folder, o)), None)
        out.setdefault(outer or folder, []).extend(folders[folder])
    return out


def is_strictly_under(path: str, root: str) -> bool:
    return is_under(path, root) and clean_dir(path) != clean_dir(root)


def stray_videos(folder: str, candidates: List[dict], stash_files: Dict[str, dict]) -> List[str]:
    """Videos under `folder` that are not one of its candidates: a part with
    another code, or a file Stash does not know. Any of them keeps the whole
    folder where it is. `stash_files` is path -> scene for files Stash knows."""
    wanted = {c["path"] for c in candidates}
    stray = []
    for dirpath, dirs, names in os.walk(folder):
        dirs.sort()
        for name in filter(is_video, sorted(names)):
            path = os.path.join(dirpath, name)
            if path in wanted:
                continue
            rel = os.path.relpath(path, folder)
            scene = stash_files.get(path)
            if scene is None:
                stray.append(f"{rel} (not a scene in Stash)")
            else:
                stray.append(f"{rel} (scene {scene['id']}, code {norm_code(scene.get('code')) or 'none'})")
    return stray


# ---------------------------------------------------------------------------
# Moving
# ---------------------------------------------------------------------------

def plan_archive(folder: str, dest: str) -> Tuple[List[Tuple[str, str]], List[str]]:
    """Every file under `folder` with its place under `dest`, and the ones
    whose place is taken (nothing is overwritten)."""
    moves: List[Tuple[str, str]] = []
    taken: List[str] = []
    for dirpath, dirs, files in os.walk(folder):
        dirs.sort()
        rel = os.path.relpath(dirpath, folder)
        for name in sorted(files):
            dst = os.path.normpath(os.path.join(dest, rel, name))
            moves.append((os.path.join(dirpath, name), dst))
            if os.path.lexists(dst):
                taken.append(os.path.relpath(dst, dest))
    return moves, taken


def remove_empty_dirs(folder: str) -> List[str]:
    """Remove `folder` and the folders under it that are empty (deepest
    first). Returns what is left behind, relative to `folder`."""
    left: List[str] = []
    for dirpath, _dirs, files in os.walk(folder, topdown=False):
        left += [os.path.relpath(os.path.join(dirpath, f), folder) for f in files]
        try:
            os.rmdir(dirpath)
        except OSError:
            pass
    return sorted(left)


def log_record(folder: str, dest: str, candidates: List[dict], moved_files: List[dict],
               moved_other: List[Tuple[str, str]], error: str = "") -> dict:
    """One line of the JSONL undo log: enough to put the folder back with
    moveFiles (videos, by file id) and plain renames (everything else)."""
    record = {
        "source_folder": folder,
        "destination_folder": dest,
        "scenes": sorted({c["scene_id"] for c in candidates}, key=lambda s: int(s) if str(s).isdigit() else s),
        "codes": sorted({c["code"] for c in candidates}),
        "matched": sorted({m for c in candidates for m in c["matched"]}),
        "files": moved_files,
        "sidecars": [[src, dst] for src, dst in moved_other],
    }
    if error:
        record["error"] = error
    return record
