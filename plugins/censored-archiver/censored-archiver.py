#!/usr/bin/env python3
"""
censored-archiver.py -- Stash plugin entry point.

Finds every censored scene whose studio code (scene.code, trimmed, case
ignored) is also the code of a scene in one of the reference libraries
(restored versions: LTX, Jasna DL, ...) and moves its whole scene folder
into the archive root, mirroring the tree:

  Censored/Censored HD/ABP-984 .../  ->  Archived/Censored/Censored HD/ABP-984 .../

Videos go through Stash's moveFiles, so each scene keeps its id, metadata,
history and captions and no scan is needed; the other files in the folder
(NFO, artwork, subtitles, extrafanart/, ...) follow by rename once every
video has arrived. Nothing is overwritten or deleted. The run stops at the
first error, and every moved folder is appended to a JSONL undo log.

Invoked by Stash with a JSON payload on stdin:
  {"server_connection": {...}, "args": {"mode": "check"|"archive", "dry_run": bool}}

Everything that needs no Stash lives in censored_archiver_core.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import stashapi.log as log
from stashapi.stashapp import StashInterface

import censored_archiver_core as core

PLUGIN_ID = "censored-archiver"
PLUGIN_VERSION = "0.1.1"

LOG_NAME = ".censored-archiver.jsonl"
SCENE_FRAGMENT = "id code files { id path }"


class Config:
    def __init__(self, settings: Dict[str, Any], args: Dict[str, Any]):
        def text(key: str) -> str:
            value = settings.get(key)
            return value.strip() if isinstance(value, str) else ""

        self.censored_roots = core.parse_roots(text("censoredRoots"))
        self.reference_roots = core.parse_roots(text("referenceRoots"))
        self.archive_root = core.clean_dir(core.unquote(text("archiveRoot")))
        # The undo log lives with the archive by default: plugin folders are
        # replaced when the plugin is updated from its source.
        self.log_file = core.unquote(text("logFile")) or (os.path.join(self.archive_root, LOG_NAME) if self.archive_root else "")
        self.clean_after = bool(settings.get("cleanAfter"))
        self.mode = (args.get("mode") or "check").strip().lower()
        self.dry_run = bool(args.get("dry_run"))

    def problems(self) -> List[str]:
        out = []
        if not self.censored_roots:
            out.append("Censored roots is not set")
        if not self.reference_roots:
            out.append("Reference roots is not set")
        if not self.archive_root:
            out.append("Archive root is not set")
        out += core.nesting_problems({"censored": self.censored_roots, "reference": self.reference_roots,
                                      "archive": [self.archive_root] if self.archive_root else []})
        return out


def path_filter(folder: str) -> dict:
    """Scenes with a file under `folder`. Stash splits an unquoted INCLUDES
    value into words ("Web Censored/" would match every "Censored" path),
    so the path goes in quotes unless it holds one itself."""
    value = folder.rstrip("/") + "/"
    if '"' not in value:
        value = f'"{value}"'
    return {"path": {"value": value, "modifier": "INCLUDES"}}


class ArchiveError(Exception):
    """Stops the run: something moved, or was about to, that should not."""


class Run:
    def __init__(self, stash: StashInterface, cfg: Config):
        self.stash = stash
        self.cfg = cfg
        self._libraries: Optional[List[str]] = None

    def library_paths(self) -> List[str]:
        if self._libraries is None:
            stashes = (self.stash.get_configuration("general { stashes { path } }") or {}) \
                .get("general", {}).get("stashes", [])
            self._libraries = [core.clean_dir(s["path"]) for s in stashes if s.get("path")]
        return self._libraries

    def scenes_under(self, roots: List[str]) -> List[dict]:
        """Every scene with a file under one of the roots, once each. The
        path filter is a substring match, so callers re-check with is_under."""
        seen: Dict[str, dict] = {}
        for root in roots:
            for scene in self.stash.find_scenes(f=path_filter(root), fragment=SCENE_FRAGMENT) or []:
                seen[scene["id"]] = scene
        return list(seen.values())

    def stash_files_under(self, folder: str) -> Dict[str, dict]:
        """Path -> scene for every scene file under the folder."""
        out: Dict[str, dict] = {}
        for scene in self.scenes_under([folder]):
            for f in scene.get("files") or []:
                if f.get("path") and core.is_under(f["path"], folder):
                    out[f["path"]] = scene
        return out

    # -- check -------------------------------------------------------------

    def check(self) -> None:
        cfg = self.cfg
        problems = cfg.problems()
        log.info(f"{PLUGIN_ID} {PLUGIN_VERSION}: check settings")
        libraries = self.library_paths()

        def covered(path: str) -> str:
            return core.covering_root(path, libraries) or ""

        for label, roots in (("Censored root", cfg.censored_roots), ("Reference root", cfg.reference_roots)):
            for root in roots:
                ok = os.path.isdir(root)
                count = self.stash.find_scenes(f=path_filter(root), filter={"per_page": 1}, fragment="id",
                                               get_count=True)[0]
                log.info(f"  {label}: {root} -- {'found' if ok else 'NOT FOUND'} on the Stash host, "
                         f"{count} scene(s), library {covered(root) or 'NONE'}")
                if not ok:
                    problems.append(f"{label.lower()} {root} is not a directory on the Stash host")
        if cfg.archive_root:
            ok = os.path.isdir(cfg.archive_root) and os.access(cfg.archive_root, os.W_OK)
            lib = covered(cfg.archive_root)
            log.info(f"  Archive root: {cfg.archive_root} -- {'writable' if ok else 'NOT a writable directory'}, "
                     f"library {lib or 'NONE'}")
            if not ok:
                problems.append(f"archive root {cfg.archive_root} is not a writable directory on the Stash host")
            if not lib:
                problems.append("no Stash library path covers the archive root; moveFiles only moves into a library")
        log.info(f"  Undo log: {cfg.log_file or '(none)'}")
        log.info(f"  Clean afterwards: {cfg.clean_after}")
        if problems:
            for p in problems:
                log.error(p)
            raise SystemExit(f"{len(problems)} problem(s) found; see above.")
        log.info("All checks passed.")

    # -- archive -----------------------------------------------------------

    def archive(self) -> None:
        cfg = self.cfg
        problems = cfg.problems()
        if problems:
            raise SystemExit("; ".join(problems) + " (Settings > Plugins > Censored Archiver).")
        tag = " [dry run]" if cfg.dry_run else ""
        log.info(f"{PLUGIN_ID} {PLUGIN_VERSION}{tag}: censored {cfg.censored_roots}, reference "
                 f"{cfg.reference_roots}, archive {cfg.archive_root}")
        if not cfg.dry_run and not os.path.isdir(cfg.archive_root):
            raise SystemExit(f"Archive root {cfg.archive_root} is not there (is the share mounted?)")

        refcodes = core.reference_codes(self.scenes_under(cfg.reference_roots), cfg.reference_roots)
        log.info(f"{len(refcodes)} distinct code(s) in the reference libraries")
        folders = core.merge_nested(core.candidate_folders(self.scenes_under(cfg.censored_roots),
                                                           cfg.censored_roots, refcodes))
        n_files = sum(len(c) for c in folders.values())
        log.info(f"{n_files} censored file(s) in {len(folders)} folder(s) have a reference version")

        c = {"moved": 0, "skipped": 0}
        moved_from: List[str] = []
        errors: List[str] = []
        for i, (folder, candidates) in enumerate(sorted(folders.items())):
            log.progress(i / max(1, len(folders)))
            try:
                outcome = self.archive_folder(folder, candidates)
            except ArchiveError as err:
                errors.append(str(err))
                # A real run stops here; a dry run lists every problem.
                if not cfg.dry_run:
                    break
                log.error(str(err))
                continue
            c[outcome] += 1
            if outcome == "moved":
                moved_from.append(folder)
        log.progress(1.0)

        if moved_from and cfg.clean_after and not cfg.dry_run:
            job = self.stash.metadata_clean(paths=moved_from, dry_run=False)
            log.info(f"Clean queued for {len(moved_from)} old folder(s) (job {job})")
        verb = "would move" if cfg.dry_run else "moved"
        summary = (f"Archive{tag}: {c['moved']} folder(s) {verb} to {cfg.archive_root}, {c['skipped']} skipped"
                   + (f"; undo log {cfg.log_file}" if c["moved"] and not cfg.dry_run and cfg.log_file else ""))
        if errors and not cfg.dry_run:
            log.error(errors[0])
            log.info(summary)
            raise SystemExit(f"Stopped at the first error: {errors[0]}")
        log.info(summary)
        if errors:
            raise SystemExit(f"{len(errors)} folder(s) would stop a real run; see above.")

    def archive_folder(self, folder: str, candidates: List[dict]) -> str:
        """Move one censored scene folder, whole. Returns moved or skipped;
        raises ArchiveError for anything that has to stop the run."""
        name = os.path.basename(folder)
        codes = ", ".join(sorted({c["code"] for c in candidates}))
        matched = ", ".join(sorted({m for c in candidates for m in c["matched"]}))
        dest, why = core.archive_destination(folder, self.cfg.censored_roots, self.cfg.archive_root)
        if dest is None:
            log.warning(f"{name} [{codes}]: skipped: {why}")
            return "skipped"
        if not os.path.isdir(folder):
            raise ArchiveError(f"{folder}: the folder is not there (is the share mounted?)")
        missing = [c["path"] for c in candidates if not os.path.isfile(c["path"])]
        if missing:
            raise ArchiveError(f"{name}: Stash lists {', '.join(map(os.path.basename, missing))} but it is not on disk")
        stray = core.stray_videos(folder, candidates, self.stash_files_under(folder))
        if stray:
            log.warning(f"{name} [{codes}]: skipped: the folder also holds {', '.join(stray)}")
            return "skipped"
        moves, taken = core.plan_archive(folder, dest)
        if taken:
            raise ArchiveError(f"{name}: {dest} already has {', '.join(taken[:5])}"
                               + (f" and {len(taken) - 5} more" if len(taken) > 5 else "")
                               + " (nothing is overwritten)")
        videos = {c["path"]: c for c in candidates}
        others = len(moves) - len(videos)
        if self.cfg.dry_run:
            log.info(f"[dry run] {name} [{codes}, in {matched}]: would move to {dest} "
                     f"({len(videos)} video(s) through Stash, {others} other file(s))")
            return "moved"

        moved_files: List[dict] = []
        moved_other: List[Tuple[str, str]] = []
        try:
            self.move_folder(folder, moves, videos, moved_files, moved_other)
        except Exception as err:
            core.remove_empty_dirs(dest)   # only folders this left empty
            self.write_log(core.log_record(folder, dest, candidates, moved_files, moved_other, str(err)))
            raise ArchiveError(f"{name}: archiving stopped: {err}. What moved is in {dest} (see the undo log), "
                               f"the rest is in {folder}") from err
        self.write_log(core.log_record(folder, dest, candidates, moved_files, moved_other))
        self.verify(dest, candidates)
        log.info(f"{name} [{codes}, in {matched}]: archived to {dest} ({len(videos)} video(s), {others} other file(s))")
        return "moved"

    def move_folder(self, folder: str, moves: List[Tuple[str, str]], videos: Dict[str, dict],
                    moved_files: List[dict], moved_other: List[Tuple[str, str]]) -> None:
        """Videos first, through moveFiles, each arrival checked on disk; then
        the rest by rename, and the emptied folder is removed. moved_files and
        moved_other are filled as things move, for the undo log."""
        by_dir: Dict[str, List[str]] = {}
        for src, dst in moves:
            if src in videos:
                by_dir.setdefault(os.path.dirname(dst), []).append(src)
        for dst_dir, sources in sorted(by_dir.items()):
            os.makedirs(dst_dir, exist_ok=True)
            self.stash.call_GQL("mutation MoveFiles($input: MoveFilesInput!) { moveFiles(input: $input) }",
                                {"input": {"ids": [videos[s]["file_id"] for s in sources],
                                           "destination_folder": dst_dir}})
            for src in sources:
                dst = os.path.join(dst_dir, os.path.basename(src))
                if os.path.lexists(src) or not os.path.exists(dst):
                    raise ArchiveError(f"Stash's moveFiles did not move {os.path.basename(src)} to {dst_dir}")
                moved_files.append({"scene_id": videos[src]["scene_id"], "file_id": videos[src]["file_id"],
                                    "source": src, "destination": dst})
        for src, dst in moves:
            if src in videos:
                continue
            if os.path.lexists(dst):   # appeared since the plan was made
                raise ArchiveError(f"{dst} already exists (nothing is overwritten)")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)   # a rename on one filesystem
            moved_other.append((src, dst))
        left = core.remove_empty_dirs(folder)
        if left:
            log.warning(f"  {folder} kept: {', '.join(left[:5])} appeared there during the move")

    def verify(self, dest: str, candidates: List[dict]) -> None:
        """Stash itself has to show each scene's file in its new place."""
        for cand in candidates:
            scene = self.stash.find_scene(cand["scene_id"], fragment="files { id path }") or {}
            paths = {f["id"]: f["path"] for f in scene.get("files") or []}
            if not core.is_under(paths.get(cand["file_id"], ""), dest):
                raise ArchiveError(f"scene {cand['scene_id']}: Stash still has file {cand['file_id']} at "
                                   f"{paths.get(cand['file_id'], '(no such file)')}, not under {dest}")

    def write_log(self, record: dict) -> None:
        if not self.cfg.log_file:
            return
        record = {"time": datetime.now().isoformat(timespec="seconds"), **record}
        try:
            with open(self.cfg.log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as err:
            # The move itself is done and Stash knows it; losing the undo line
            # must not hide that, but it does stop the run.
            log.error(f"Could not write the undo log {self.cfg.log_file}: {err}. Record: {json.dumps(record)}")
            raise ArchiveError(f"undo log not writable: {err}") from err


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    stash = StashInterface(payload.get("server_connection") or {})
    settings = (stash.get_configuration() or {}).get("plugins", {}).get(PLUGIN_ID) or {}
    cfg = Config(settings, payload.get("args") or {})
    run = Run(stash, cfg)
    try:
        if cfg.mode == "check":
            run.check()
        elif cfg.mode == "archive":
            run.archive()
        else:
            raise SystemExit(f"Unknown task mode {cfg.mode!r}.")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            log.error(str(exc.code))
            log.exit(err=str(exc.code))
        raise
    log.exit("ok")


if __name__ == "__main__":
    main()
