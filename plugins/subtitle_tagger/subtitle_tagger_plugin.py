#!/usr/bin/env python3
"""Stash plugin entry point for subtitle_tagger.

Stash launches this with a JSON payload on stdin describing the server
connection and the task (or hook) being run. Unlike the standalone CLI, this
runs *on the Stash host*, so scene paths reported by Stash are directly
readable and no path mapping is involved.

Modes:
    hook    triggered per scene by Scene.Create.Post (set automatically)
    all     walk every scene in the library
    folder  walk only scenes under the configured folder path

Logging uses Stash's stderr control-code protocol so messages appear in the
Stash log viewer at the right level.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from subtitle_tagger import (  # noqa: E402
    MANAGED_PREFIXES,
    Stash,
    StashError,
    command_file_for,
    extract_tag_names,
    read_command_file,
    scene_paths,
    scene_tag_names,
)

PLUGIN_ID = "subtitle_tagger"

# Stash reads stderr lines prefixed with \x01<level>\x02 and routes them to the
# matching log level in the UI.
LOG_LEVELS = {"trace": "t", "debug": "d", "info": "i", "warning": "w", "error": "e"}


def log(message: str, level: str = "info") -> None:
    code = LOG_LEVELS.get(level, "i")
    for line in str(message).split("\n"):
        sys.stderr.write(f"\x01{code}\x02{line}\n")
    sys.stderr.flush()


def progress(fraction: float) -> None:
    """Report 0.0-1.0 completion to the Stash task progress bar."""
    sys.stderr.write(f"\x01p\x02{min(max(fraction, 0.0), 1.0):.4f}\n")
    sys.stderr.flush()


def connect(server: dict) -> Stash:
    """Build a client from the server_connection block Stash provides."""
    scheme = server.get("Scheme", "http")
    host = server.get("Host", "localhost")
    # Stash reports a wildcard bind address as its host; that is not dialable.
    if host in ("0.0.0.0", "::", ""):
        host = "localhost"
    port = server.get("Port", 9999)

    cookie = None
    session = server.get("SessionCookie") or {}
    if session.get("Name") and session.get("Value"):
        cookie = f"{session['Name']}={session['Value']}"

    return Stash(f"{scheme}://{host}:{port}", cookie=cookie)


def plugin_settings(stash: Stash) -> dict:
    """This plugin's settings as configured in Settings > Plugins."""
    query = """
    query Configuration {
      configuration { plugins }
    }
    """
    try:
        plugins = stash.call(query)["configuration"]["plugins"] or {}
    except StashError as exc:
        log(f"Could not read plugin settings: {exc}", "warning")
        return {}
    return plugins.get(PLUGIN_ID) or {}


def desired_tags(scene: dict) -> list[str] | None:
    """Tag names implied by a scene's sidecar, or None if there is no sidecar.

    The None/empty distinction matters for pruning: an empty list means a
    sidecar was read but named no models, which is not licence to strip a
    scene's existing tags.
    """
    found = False
    names: list[str] = []
    for path in scene_paths(scene):
        command_file = command_file_for(Path(path))
        if command_file is None:
            continue
        text = read_command_file(command_file)
        if text is None:
            log(f"Unreadable or non-text sidecar: {command_file}", "warning")
            continue
        found = True
        for name in extract_tag_names(text):
            if name not in names:
                names.append(name)
    return names if found else None


def missing_from(scene: dict, wanted: list[str]) -> list[str]:
    """The subset of `wanted` that `scene` does not already carry."""
    existing = scene_tag_names(scene)
    return [name for name in wanted if name not in existing]


def stale_on(scene: dict, wanted: list[str]) -> list[str]:
    """Managed tags on `scene` that its current sidecar no longer implies.

    Restricted to the plugin's own prefixes, so tags applied by anyone else are
    never candidates for removal.
    """
    return sorted(
        name for name in scene_tag_names(scene)
        if name.startswith(MANAGED_PREFIXES) and name not in wanted
    )


def run_hook(stash: Stash, hook_context: dict, create_tags: bool,
             dry_run: bool, prune: bool) -> None:
    """Handle a single scene created by a scan."""
    scene_id = hook_context.get("id")
    if scene_id is None:
        log("Hook fired with no scene id", "warning")
        return

    scene = stash.find_scene(scene_id)
    if scene is None:
        log(f"Scene {scene_id} vanished before it could be tagged", "warning")
        return

    wanted = desired_tags(scene)
    if wanted is None:
        log(f"Scene {scene_id}: no sidecar", "debug")
        return

    names = missing_from(scene, wanted)
    # Pruning needs a sidecar that actually named something; an empty parse is
    # treated as "unknown", not as "this scene should have no tags".
    stale = stale_on(scene, wanted) if (prune and wanted) else []

    if not names and not stale:
        log(f"Scene {scene_id}: nothing to change", "debug")
        return

    if dry_run:
        if names:
            log(f"[dry-run] scene {scene_id}: +{', '.join(names)}")
        if stale:
            log(f"[dry-run] scene {scene_id}: -{', '.join(stale)}")
        return

    if names:
        unresolved, failed, errors = stash.apply_tags(
            [scene_id], names, create=create_tags)
        for message in errors:
            log(f"Scene {scene_id}: {message}", "error")
        if not failed:
            applied = [n for n in names if n not in unresolved]
            if applied:
                log(f"scene {scene_id}: +{', '.join(applied)}")
        for name in unresolved:
            log(f"Tag {name!r} does not exist, skipping", "debug")
    if stale:
        _, failed, errors = stash.strip_tags([scene_id], stale)
        for message in errors:
            log(f"Scene {scene_id}: {message}", "error")
        if not failed:
            log(f"scene {scene_id}: -{', '.join(stale)}")


def run_bulk(stash: Stash, folder: str | None, create_tags: bool,
             dry_run: bool, prune: bool) -> None:
    """Walk the library (or one folder) and reconcile tags against sidecars."""
    if folder:
        log(f"Scanning scenes under {folder}")
    else:
        log("Scanning all scenes")
    if prune:
        log("Pruning enabled: tags no longer implied by a sidecar are removed")

    log(f"Cached {stash.prefetch_tags()} existing tag(s)", "debug")

    scenes = list(stash.iter_scenes(path_prefix=folder))
    if not scenes:
        log("No scenes matched", "warning")
        return

    # Scenes needing an identical change are collected together, so one request
    # covers the whole group instead of one per scene per tag.
    add_groups: dict[tuple[str, ...], list[str]] = {}
    drop_groups: dict[tuple[str, ...], list[str]] = {}
    total = len(scenes)
    up_to_date = 0
    for index, scene in enumerate(scenes, start=1):
        wanted = desired_tags(scene)
        if wanted is not None:
            names = missing_from(scene, wanted)
            stale = stale_on(scene, wanted) if (prune and wanted) else []
            if names:
                add_groups.setdefault(tuple(names), []).append(scene["id"])
            if stale:
                drop_groups.setdefault(tuple(stale), []).append(scene["id"])
            if not names and not stale:
                up_to_date += 1
        if index % 100 == 0 or index == total:
            progress(index / total)

    to_add = sum(len(ids) for ids in add_groups.values())
    to_drop = sum(len(ids) for ids in drop_groups.values())
    log(f"{to_add} scene(s) need tags, {to_drop} have stale tags, "
        f"{up_to_date} already current")

    if dry_run:
        for names, ids in add_groups.items():
            log(f"[dry-run] {len(ids)} scene(s): +{', '.join(names)}")
        for names, ids in drop_groups.items():
            log(f"[dry-run] {len(ids)} scene(s): -{', '.join(names)}")
        return

    # The write phase can take far longer than the scan phase on a large
    # library, so keep the Stash progress bar moving through it rather than
    # leaving the task apparently frozen at 100%.
    written = [0]
    work = max(to_add + to_drop, 1)

    def advance(done_in_group: int, group_total: int) -> None:
        progress((written[0] + done_in_group) / work)

    applied = removed = 0
    failed_scenes: set[str] = set()
    for names, ids in add_groups.items():
        unresolved, failed, errors = stash.apply_tags(
            ids, list(names), create=create_tags, on_progress=advance)
        written[0] += len(ids)
        for message in errors:
            log(f"{len(failed)} of {len(ids)} scene(s) "
                f"[+{', '.join(names)}]: {message}", "error")
        failed_scenes.update(failed)
        for name in unresolved:
            log(f"Tag {name!r} does not exist, skipping", "debug")
        added = [n for n in names if n not in unresolved]
        ok = len(ids) - len(failed)
        if added and ok:
            log(f"{ok} scene(s): +{', '.join(added)}")
            applied += ok * len(added)

    for names, ids in drop_groups.items():
        _, failed, errors = stash.strip_tags(ids, list(names), on_progress=advance)
        written[0] += len(ids)
        for message in errors:
            log(f"{len(failed)} of {len(ids)} scene(s) "
                f"[-{', '.join(names)}]: {message}", "error")
        failed_scenes.update(failed)
        ok = len(ids) - len(failed)
        if ok:
            log(f"{ok} scene(s): -{', '.join(names)}")
            removed += ok * len(names)

    if failed_scenes:
        log(f"{len(failed_scenes)} scene(s) could not be updated; "
            f"re-run the task to retry just those", "warning")

    log(f"Done: {applied} tag(s) added, {removed} removed, across {total} scene(s)")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError) as exc:
        log(f"Invalid plugin input: {exc}", "error")
        return 1

    args = payload.get("args") or {}
    stash = connect(payload.get("server_connection") or {})

    settings = plugin_settings(stash)
    # Task args win over the saved settings, so a task can override a default.
    create_tags = not args.get(
        "noCreateTags", settings.get("noCreateTags", False)
    )
    dry_run = bool(args.get("dryRun", settings.get("dryRun", False)))
    prune = bool(args.get("removeStale", settings.get("removeStaleTags", False)))

    # A tuned batch size only matters for the bulk tasks, but applying it here
    # keeps one code path.
    try:
        batch = int(args.get("sceneBatch", settings.get("sceneBatch") or 0))
    except (TypeError, ValueError):
        batch = 0
    if batch > 0:
        stash.scene_batch = batch

    hook_context = args.get("hookContext")
    try:
        if hook_context:
            # Stash boolean settings are unticked until the user turns them
            # on, so the default here must match what the UI shows: off.
            if not settings.get("enableScanHook", False):
                log("Scan hook is off - tick 'Tag On Scan' in plugin settings",
                    "debug")
            else:
                run_hook(stash, hook_context, create_tags, dry_run, prune)
        elif args.get("scene_id") is not None or args.get("mode") == "scene":
            # One named scene, from another plugin or a webhook: the
            # WhisperJAV Fleet plugin queues this right after its rescan so
            # a scene whose subtitle just landed gets its model tags without
            # a folder-wide pass. A Stash rescan fires no hook for a caption
            # attaching to an existing scene, so this is the only automatic
            # route for that case. An explicit ask, so the "Tag On Scan"
            # switch does not apply.
            scene_id = args.get("scene_id")
            if scene_id is None:
                log("mode 'scene' needs a scene_id argument", "error")
                return 1
            run_hook(stash, {"id": scene_id}, create_tags, dry_run, prune)
        else:
            mode = args.get("mode", "all")
            folder = None
            if mode == "folder":
                folder = (settings.get("folderPath") or "").strip()
                if not folder:
                    log(
                        "No folder configured. Set 'Folder Path' in "
                        "Settings > Plugins > Subtitle Tagger, then run this task again.",
                        "error",
                    )
                    return 1
            run_bulk(stash, folder, create_tags, dry_run, prune)
    except StashError as exc:
        log(f"Stash error: {exc}", "error")
        return 1
    except Exception:  # noqa: BLE001 - never let a crash abort a library scan
        log(traceback.format_exc(), "error")
        return 1

    print(json.dumps({"output": "ok"}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
