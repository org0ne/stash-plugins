#!/usr/bin/env python3
"""
jasna-batch.py -- Stash plugin entry point.

Tag a scene "Jasna: <preset>" and it is queued on stash-jasna-bridge for an
offline Jasna restoration with that preset's flags. The bridge runs the
jobs one at a time on the GPU it shares with the live JASNA ON stream,
then runs this plugin's "Sync jobs" task after every state change. Sync
moves the original scene's status tag (Queued / Processing / Done /
Failed) and, for a finished job, prepares the result for Stash: copies the
source folder's sidecars next to the restored file under the new name,
writes an edited .nfo (marker in the title, extra genres, Jellyfin/Kodi
leftovers stripped), drops a -jasna-command.txt sidecar and scans the new
folder. Everything the NFO needs is in place before the scan, so the
nfoSceneParser already hooked on Scene.Create.Post fills the new scene
from it as the scan creates it; this plugin does nothing NFO -> Stash.

Invoked by Stash with a JSON payload on stdin:
  {"server_connection": {...},
   "args": {"mode": "check"|"queue"|"sync", "dry_run": bool, "job_id": str}
        or {"hookContext": {"id": ..., "type": "Scene.Update.Post", ...}}}

Everything that needs no Stash lives in jasna_batch_core.py.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

import stashapi.log as log
from stashapi.stashapp import StashInterface

import jasna_batch_core as core

PLUGIN_ID = "jasna-batch"
PLUGIN_VERSION = "0.1.0"

PARENT_TAG = "Jasna Batch"
STATUS_TAGS = {
    "queued": "Jasna: Queued",
    "running": "Jasna: Processing",
    "done": "Jasna: Done",
    "failed": "Jasna: Failed",
}
TAG_DESCRIPTIONS = {
    PARENT_TAG: "Tags of the jasna-batch plugin. 'Jasna: <preset>' queues a scene for an offline Jasna "
                "restoration; the others show where its job is.",
    "queued": "Waiting on the bridge's job queue (jasna-batch).",
    "running": "Jasna is restoring this scene's file now (jasna-batch). JASNA ON is unavailable meanwhile.",
    "done": "Restored and ingested: the new scene lives in the target folder (jasna-batch).",
    "failed": "The job or its ingestion failed; the reason is in the Stash log and the bridge's job log. "
              "Fix the cause and run the Sync jobs task to retry ingestion, or re-tag the scene to run again.",
}
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8770/jasna"
DEFAULT_MARKER = "[LTX]"
DEFAULT_GENRES = "LTX, Jasna"
DEFAULT_IGNORE = "*.bak, .lada_*"
PENDING_SCAN = "pending-scan"   # ingest()'s answer while the scan it queued has not run yet
SCENE_FRAGMENT = "id title tags { id name } files { id path }"
KNOWN_SETTINGS = {"bridgeUrl", "bridgeToken", "queuePageUrl", "presets", "targetRoot", "titleMarker",
                  "extraGenres", "cleanHtml", "ignorePatterns", "stopAfter"}


class Config:
    def __init__(self, settings: Dict[str, Any], args: Dict[str, Any]):
        def text(key: str, default: str = "") -> str:
            value = settings.get(key)
            return (value.strip() if isinstance(value, str) else "") or default

        self.raw = dict(settings)
        self.bridge_url = text("bridgeUrl", DEFAULT_BRIDGE_URL).rstrip("/")
        self.bridge_token = text("bridgeToken")
        self.queue_page_url = text("queuePageUrl")
        self.presets, self.preset_problems = core.parse_presets(text("presets"))
        self.target_root = text("targetRoot").rstrip("/")
        self.marker = text("titleMarker", DEFAULT_MARKER)
        self.genres = [g.strip() for g in text("extraGenres", DEFAULT_GENRES).split(",") if g.strip()]
        self.clean_html = bool(settings.get("cleanHtml"))
        self.ignore_patterns = [p.strip() for p in text("ignorePatterns", DEFAULT_IGNORE).split(",") if p.strip()]
        self.stop_after = text("stopAfter").lower()   # "" or "prepare"
        self.mode = (args.get("mode") or "").strip().lower()
        self.dry_run = bool(args.get("dry_run"))
        self.job_id = str(args.get("job_id") or "").strip()
        self.event = str(args.get("event") or "").strip()
        # Set by the follow-up Sync this plugin queues behind a scan: the
        # scan for cfg.job_id has run by the time this one executes.
        self.after_scan = bool(args.get("after_scan"))

    def preset(self, name: str) -> Optional[core.Preset]:
        for p in self.presets:
            if p.name.lower() == name.lower():
                return p
        return None


# ---------------------------------------------------------------------------
# Bridge client
# ---------------------------------------------------------------------------

class BridgeError(Exception):
    def __init__(self, status: int, message: str, body: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


class Bridge:
    def __init__(self, cfg: Config):
        self.base = cfg.bridge_url
        self.token = cfg.bridge_token

    def call(self, method: str, path: str, body: Optional[dict] = None, timeout: float = 30) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                ctype = resp.headers.get_content_type()
        except urllib.error.HTTPError as err:
            with err:
                raw = err.read()
            try:
                payload = json.loads(raw.decode())
            except ValueError:
                payload = {"error": raw.decode(errors="replace")[:200] or f"HTTP {err.code}"}
            raise BridgeError(err.code, payload.get("error") or f"HTTP {err.code}", payload)
        except (urllib.error.URLError, OSError) as err:
            raise BridgeError(0, f"cannot reach the bridge at {self.base}: {err}")
        if ctype == "text/plain":
            return raw.decode(errors="replace")
        try:
            return json.loads(raw.decode())
        except ValueError:
            raise BridgeError(0, f"{self.base}{path} did not answer JSON (is {self.base} routed to the bridge?)")

    def health(self) -> dict:
        return self.call("GET", "/health")

    def submit(self, scene_id: str, file_path: str, output_path: str, preset: core.Preset) -> dict:
        return self.call("POST", "/jobs", {"scene_id": str(scene_id), "file_path": file_path,
                                           "output_path": output_path, "preset": preset.name,
                                           "flags": preset.flags})

    def jobs(self, unacknowledged: bool = True) -> List[dict]:
        q = "?unacknowledged=1" if unacknowledged else ""
        return self.call("GET", "/jobs" + q).get("jobs", [])

    def job(self, job_id: str) -> dict:
        return self.call("GET", f"/jobs/{job_id}")["job"]

    def ack(self, job_id: str) -> None:
        self.call("POST", f"/jobs/{job_id}/ack")

    def log_tail(self, job_id: str, lines: int = 15) -> str:
        try:
            text = self.call("GET", f"/jobs/{job_id}/log")
        except BridgeError:
            return ""
        return "\n".join(str(text).rstrip().splitlines()[-lines:])


# ---------------------------------------------------------------------------
# Stash helpers
# ---------------------------------------------------------------------------

class Tags:
    """Case-insensitive name -> id over every tag, loaded once; creates on
    demand (never in a dry run)."""

    def __init__(self, stash: StashInterface, dry_run: bool):
        self.stash = stash
        self.dry_run = dry_run
        self._ids: Dict[str, str] = {}
        self._parents: Dict[str, List[str]] = {}
        for tag in stash.find_tags(fragment="id name parents { id }") or []:
            self._ids.setdefault(tag["name"].strip().lower(), tag["id"])
            self._parents[tag["id"]] = [p["id"] for p in tag.get("parents") or []]
        self.created: List[str] = []

    def get(self, name: str) -> Optional[str]:
        return self._ids.get(name.strip().lower())

    def ensure(self, name: str, description: str = "", parent_id: Optional[str] = None) -> Optional[str]:
        tag_id = self.get(name)
        if tag_id:
            if parent_id and parent_id not in self._parents.get(tag_id, []) and not self.dry_run:
                parents = self._parents.get(tag_id, []) + [parent_id]
                self.stash.update_tag({"id": tag_id, "parent_ids": parents})
                self._parents[tag_id] = parents
            return tag_id
        if self.dry_run:
            log.info(f"[dry-run] would create tag {name!r}")
            return None
        tag_in: Dict[str, Any] = {"name": name.strip()}
        if description:
            tag_in["description"] = description
        if parent_id:
            tag_in["parent_ids"] = [parent_id]
        created = self.stash.create_tag(tag_in)
        self._ids[name.strip().lower()] = created["id"]
        self._parents[created["id"]] = [parent_id] if parent_id else []
        self.created.append(name)
        log.info(f"created tag {name!r} (id {created['id']})")
        return created["id"]


def scene_label(scene: dict) -> str:
    path = scene_primary_path(scene)
    return f"scene {scene['id']} ({os.path.basename(path) if path else 'no file'})"


def scene_primary_path(scene: dict) -> Optional[str]:
    for f in scene.get("files") or []:
        if f.get("path"):
            return f["path"]
    return None


def scene_tag_names(scene: dict) -> List[str]:
    return [t["name"] for t in scene.get("tags") or []]


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

class IngestError(Exception):
    pass


class Run:
    def __init__(self, stash: StashInterface, cfg: Config):
        self.stash = stash
        self.cfg = cfg
        self.bridge = Bridge(cfg)
        self.tags = Tags(stash, cfg.dry_run)
        self.counts = {"queued": 0, "skipped": 0, "errors": 0, "ingested": 0, "tagged": 0}

    # -- tags --------------------------------------------------------------

    def ensure_tag_tree(self) -> None:
        parent = self.tags.ensure(PARENT_TAG, TAG_DESCRIPTIONS[PARENT_TAG])
        for key, name in STATUS_TAGS.items():
            self.tags.ensure(name, TAG_DESCRIPTIONS[key], parent)
        for p in self.cfg.presets:
            self.tags.ensure(p.tag, f"Queue this scene for a Jasna restoration with preset {p.name!r}: "
                                    f"{' '.join(p.flags)}" + (f" ({p.comment})" if p.comment else ""), parent)

    def set_status(self, scene: dict, status: Optional[str], drop_triggers: List[str] = (), note: str = "") -> None:
        """Replace the scene's jasna-batch status tag with `status` (None =
        none) and drop the named trigger tags. One sceneUpdate, only if
        something changes."""
        status_ids = {self.tags.get(n) for n in STATUS_TAGS.values()} - {None}
        drop_ids = {self.tags.get(n) for n in drop_triggers} - {None}
        current = [t["id"] for t in scene.get("tags") or []]
        wanted = [t for t in current if t not in status_ids and t not in drop_ids]
        if status:
            sid = self.tags.get(STATUS_TAGS[status])
            if sid and sid not in wanted:
                wanted.append(sid)
        if sorted(wanted) == sorted(current):
            return
        label = STATUS_TAGS[status] if status else "no status tag"
        if self.cfg.dry_run:
            log.info(f"[dry-run] {scene_label(scene)}: would set {label}" + (f" ({note})" if note else ""))
            return
        if not scene.get("tags") and not self.stash.find_scene(scene["id"], fragment="id"):
            return  # a stand-in for a deleted scene: nothing to tag
        self.stash.update_scene({"id": scene["id"], "tag_ids": wanted})
        self.counts["tagged"] += 1
        log.info(f"{scene_label(scene)}: {label}" + (f" ({note})" if note else ""))

    # -- queueing ----------------------------------------------------------

    def triggers_on(self, scene: dict) -> List[core.Preset]:
        names = {n.lower() for n in scene_tag_names(scene)}
        return [p for p in self.cfg.presets if p.tag.lower() in names]

    def submit_scene(self, scene: dict, preset: core.Preset) -> None:
        source = scene_primary_path(scene)
        label = scene_label(scene)
        if not source:
            log.warning(f"{label}: has no file; leaving the {preset.tag!r} tag")
            self.counts["errors"] += 1
            return
        if len(scene.get("files") or []) > 1:
            log.info(f"{label}: has {len(scene['files'])} files; the primary one is restored")
        plan = core.plan_output(source, self.cfg.target_root, preset.name, self.cfg.marker)
        if os.path.exists(plan.video):
            log.info(f"{label}: {plan.video} already exists; skipped (same scene, same preset)")
            self.counts["skipped"] += 1
            self.set_status(scene, "done", [preset.tag], "output already existed")
            return
        if self.cfg.dry_run:
            log.info(f"[dry-run] {label}: would queue preset {preset.name!r} -> {plan.video}")
            self.counts["queued"] += 1
            return
        try:
            result = self.bridge.submit(scene["id"], source, plan.video, preset)
        except BridgeError as err:
            if err.status == 409 and err.body.get("job"):
                log.info(f"{label}: already queued on the bridge as job {err.body['job'].get('id')}")
                self.set_status(scene, "queued", [preset.tag])
                return
            log.error(f"{label}: the bridge refused the job for preset {preset.name!r}: {err} "
                      f"(the {preset.tag!r} tag is kept; fix the cause and run 'Queue tagged scenes')")
            self.counts["errors"] += 1
            self.set_status(scene, "failed", [], str(err)[:80])
            return
        job = result["job"]
        log.info(f"{label}: queued as job {job['id']} (position {result.get('position')}), preset "
                 f"{preset.name!r} -> {plan.video}")
        self.counts["queued"] += 1
        self.set_status(scene, "queued", [preset.tag])

    def hook(self, context: dict) -> None:
        if not str(context.get("type") or "").startswith("Scene.Update"):
            return
        scene_id = context.get("id")
        if not scene_id:
            return
        scene = self.stash.find_scene(scene_id, fragment=SCENE_FRAGMENT)
        if not scene:
            return
        presets = self.triggers_on(scene)
        if not presets:
            return  # every other scene edit: nothing to do
        if not self.cfg.target_root:
            log.error("Target root is not set (Settings > Plugins > Jasna Batch); the trigger tag is kept")
            return
        for preset in presets:
            self.submit_scene(scene, preset)

    def queue(self) -> None:
        if not self.cfg.presets:
            raise SystemExit("No presets configured (Settings > Plugins > Jasna Batch > Presets).")
        if not self.cfg.target_root:
            raise SystemExit("Target root is not set.")
        self.ensure_tag_tree()
        for preset in self.cfg.presets:
            tag_id = self.tags.get(preset.tag)
            if not tag_id:
                continue
            scenes = self.stash.find_scenes(
                f={"tags": {"value": [tag_id], "modifier": "INCLUDES", "depth": 0}}, fragment=SCENE_FRAGMENT) or []
            scenes.sort(key=lambda s: scene_primary_path(s) or "")
            log.info(f"{len(scenes)} scene(s) tagged {preset.tag!r}")
            for i, scene in enumerate(scenes):
                log.progress(i / max(1, len(scenes)))
                self.submit_scene(scene, preset)
        log.progress(1.0)
        c = self.counts
        log.info(f"Queue done: {c['queued']} queued, {c['skipped']} skipped, {c['errors']} error(s)"
                 + (" [dry run]" if self.cfg.dry_run else ""))

    # -- sync --------------------------------------------------------------

    def sync(self) -> None:
        """Reconcile status tags with the bridge's jobs and ingest the
        finished ones. Run by the bridge after every job state change, and
        by hand from Settings > Tasks."""
        if self.cfg.job_id:
            log.debug(f"sync triggered by the bridge: job {self.cfg.job_id} {self.cfg.event}")
        self.ensure_tag_tree()
        try:
            jobs = self.bridge.jobs(unacknowledged=True)
        except BridgeError as err:
            raise SystemExit(f"Cannot list jobs: {err}")
        if not jobs:
            log.info("No unacknowledged jobs on the bridge.")
            return
        for i, job in enumerate(jobs):
            log.progress(i / len(jobs))
            try:
                self.sync_job(job)
            except Exception as err:  # keep going through the list
                log.error(f"job {job.get('id')}: {err}")
                self.counts["errors"] += 1
        log.progress(1.0)
        c = self.counts
        log.info(f"Sync done: {c['ingested']} ingested, {c['tagged']} scene(s) re-tagged, {c['errors']} error(s)"
                 + (" [dry run]" if self.cfg.dry_run else ""))

    def sync_job(self, job: dict) -> None:
        scene = self.stash.find_scene(job["scene_id"], fragment=SCENE_FRAGMENT)
        state = job["state"]
        src = os.path.basename(job.get("stash_source") or "")
        if scene is None:
            log.warning(f"job {job['id']} ({src}): scene {job['scene_id']} no longer exists in Stash"
                        + ("; ingesting the result anyway" if state == "done" else ""))
            if state == "done":
                # The restored file is still worth a scene of its own.
                scene = {"id": job["scene_id"], "tags": [], "files": [{"path": job.get("stash_source")}]}
            else:
                self.ack(job)
                return
        if state in ("queued", "running"):
            self.set_status(scene, state, note=f"job {job['id']}")
            return
        if state == "cancelled":
            self.set_status(scene, None, note=f"job {job['id']} cancelled")
            self.ack(job)
            return
        if state == "failed":
            tail = self.bridge.log_tail(job["id"])
            log.error(f"{scene_label(scene)}: job {job['id']} failed: {job.get('error')}"
                      + (f"\n--- bridge job log (tail) ---\n{tail}" if tail else ""))
            self.set_status(scene, "failed", note=f"job {job['id']}")
            self.ack(job)
            return
        # done
        if self.cfg.dry_run:
            self.describe_ingest(job)
            return
        try:
            new_id = self.ingest(job, scene)
        except IngestError as err:
            log.error(f"{scene_label(scene)}: job {job['id']} finished but ingestion failed: {err}. "
                      f"Fix the cause and run 'Sync jobs' to retry.")
            self.counts["errors"] += 1
            self.set_status(scene, "failed", note="ingestion failed")
            return
        if new_id == PENDING_SCAN:
            return  # the follow-up Sync behind the scan finishes this job
        self.counts["ingested"] += 1
        self.set_status(scene, "done", note=f"new scene {new_id}" if new_id else "prepared")
        self.ack(job)

    def ack(self, job: dict) -> None:
        if self.cfg.dry_run:
            return
        try:
            self.bridge.ack(job["id"])
        except BridgeError as err:
            log.warning(f"job {job['id']}: could not acknowledge on the bridge: {err}")

    # -- ingestion ---------------------------------------------------------

    def plan_for(self, job: dict) -> core.OutputPlan:
        source, video = job["stash_source"], job["stash_output"]
        return core.OutputPlan(source, os.path.dirname(source), os.path.splitext(os.path.basename(source))[0],
                               os.path.dirname(video), os.path.splitext(os.path.basename(video))[0], video)

    def describe_ingest(self, job: dict) -> None:
        plan = self.plan_for(job)
        side = core.plan_sidecars(plan, self.cfg.ignore_patterns)
        log.info(f"[dry-run] job {job['id']}: would ingest {plan.video}")
        for s, d in side.renames + side.copies + side.trees:
            log.info(f"[dry-run]   {os.path.basename(s)} -> {os.path.relpath(d, plan.dest_dir)}")
        if side.nfo:
            log.info(f"[dry-run]   {os.path.basename(side.nfo[0])} -> {os.path.basename(side.nfo[1])} (edited)")
        for name, why in side.skipped:
            log.info(f"[dry-run]   skip {name}: {why}")

    def ingest(self, job: dict, scene: dict) -> Optional[str]:
        plan = self.plan_for(job)
        if not os.path.isfile(plan.video):
            raise IngestError(f"restored file not found at {plan.video} (is the share mounted on the Stash host?)")
        if not os.path.isdir(plan.source_dir):
            raise IngestError(f"source folder is gone: {plan.source_dir}")
        side = core.plan_sidecars(plan, self.cfg.ignore_patterns)
        copied = 0
        for src, dst in side.renames + side.copies:
            if _same_size(src, dst):
                continue
            shutil.copy2(src, dst)
            copied += 1
        for src, dst in side.trees:
            shutil.copytree(src, dst, dirs_exist_ok=True)
            copied += 1
        for name, why in side.skipped:
            log.debug(f"  skipped {name}: {why}")
        nfo_written = False
        if side.nfo:
            nfo_src, nfo_dst = side.nfo
            try:
                with open(nfo_src, "r", encoding="utf-8-sig") as fh:
                    original = fh.read()
                result = core.edit_nfo(original, self.cfg.marker, self.cfg.genres, self.cfg.clean_html)
            except (OSError, core.ET.ParseError) as err:
                log.warning(f"  {os.path.basename(nfo_src)}: cannot edit ({err}); copied unchanged")
                shutil.copy2(nfo_src, nfo_dst)
            else:
                with open(nfo_dst, "w", encoding="utf-8") as fh:
                    fh.write(result.text)
                log.info(f"  wrote {os.path.basename(nfo_dst)}: title {result.title_after!r}, genres added "
                         f"{result.genres_added}" + (" (fixed bare '&')" if result.fixed_ampersands else ""))
            nfo_written = True
        else:
            log.warning(f"  no {plan.source_stem}.nfo beside the source; nfoSceneParser will have nothing to "
                        f"fill the new scene from")
        info_path = os.path.join(plan.dest_dir, plan.new_stem + "-jasna-command.txt")
        with open(info_path, "w", encoding="utf-8") as fh:
            fh.write(core.command_sidecar(job, self.cfg.preset(job["preset"]), PLUGIN_VERSION))
        log.info(f"{scene_label(scene)}: prepared {plan.dest_dir} ({copied} sidecar(s) copied, "
                 f"{'NFO edited' if nfo_written else 'no NFO'})")
        if self.cfg.stop_after == "prepare":
            log.info("  stopAfter = prepare: not scanning")
            return None
        # Scan just the new folder. The NFO is complete by now, so the
        # nfoSceneParser hooked on Scene.Create.Post fills the scene as the
        # scan creates it. Stash runs its jobs one at a time and this task
        # IS a Stash job, so the scan cannot be waited for from here (that
        # deadlocked until the timeout, seen 2026-10-23): queue the scan,
        # then queue another Sync behind it to find the scene and finish.
        new_scene = self.find_scene_by_path(plan.video)
        if new_scene is not None:
            log.info(f"  scene {new_scene['id']} {new_scene.get('title')!r}")
            return new_scene["id"]
        if self.cfg.after_scan and self.cfg.job_id == job["id"]:
            raise IngestError(f"Stash did not create a scene for {plan.video} after the scan (is "
                              f"{self.cfg.target_root} one of the library paths, and is .mp4 not excluded?)")
        try:
            scan_job = self.stash.metadata_scan([plan.dest_dir])
            self.stash.run_plugin_task(PLUGIN_ID, "Sync jobs", {"job_id": job["id"], "event": "scanned",
                                                                 "after_scan": True})
        except Exception as err:
            raise IngestError(f"could not queue the scan of {plan.dest_dir}: {err}")
        log.info(f"  scan queued (Stash job {scan_job}); the Sync behind it marks the scene Done")
        return PENDING_SCAN

    def find_scene_by_path(self, path: str) -> Optional[dict]:
        found = self.stash.find_scenes(
            f={"path": {"value": os.path.basename(path), "modifier": "INCLUDES"}}, fragment=SCENE_FRAGMENT) or []
        for scene in found:
            if any(f.get("path") == path for f in scene.get("files") or []):
                return scene
        return None

    # -- check -------------------------------------------------------------

    def check(self) -> None:
        cfg, raw = self.cfg, self.cfg.raw
        problems: List[str] = []

        def src(key: str) -> str:
            return "setting" if raw.get(key) not in (None, "", False) else "default"

        def line(label: str, value: Any, key: Optional[str] = None, note: str = "") -> None:
            origin = f" [{src(key)}]" if key else ""
            log.info(f"  {label:<26} {value}{origin}" + (f"  -- {note}" if note else ""))

        log.info(f"=== {PLUGIN_ID} v{PLUGIN_VERSION} settings check ===")
        unknown = sorted(k for k in raw if k not in KNOWN_SETTINGS)
        if unknown:
            problems.append(f"unknown setting key(s) stored for this plugin: {unknown}")

        log.info("Bridge")
        line("Bridge URL", cfg.bridge_url, "bridgeUrl")
        line("Bridge token", "set" if cfg.bridge_token else "none", "bridgeToken")
        line("Queue page", cfg.queue_page_url or "<stash-origin>/jasna/queue", "queuePageUrl",
             "linked under this setting on Settings > Plugins")
        batch_info: dict = {}
        try:
            health = self.bridge.health()
            line("Bridge", f"v{health.get('version')} up {health.get('uptime_seconds')}s")
            batch = health.get("batch") or {}
            if not batch.get("enabled"):
                problems.append("the bridge has batch.enabled = false (bridge.toml); jobs cannot be queued")
            else:
                line("Bridge batch queue", f"{batch.get('queued')} queued, running: "
                     f"{(batch.get('running') or {}).get('id') or 'none'}, "
                     f"{batch.get('unacknowledged')} unacknowledged")
                check = self.bridge.call("GET", "/batch/check", timeout=180)
                batch_info = check
                for c in check.get("checks", []):
                    line(("ok  " if c["ok"] else "FAIL") + " " + c["label"], c.get("detail", ""))
                    if not c["ok"]:
                        problems.append(f"bridge check failed: {c['label']} - {c.get('detail', '')}")
        except BridgeError as err:
            line("Bridge", f"UNREACHABLE: {err}")
            problems.append(f"bridge: {err}")

        log.info("Presets")
        if not cfg.presets:
            problems.append("no presets configured")
        for p in cfg.presets:
            line(p.tag, " ".join(p.flags), "presets", p.comment)
        for problem in cfg.preset_problems:
            line("preset problem", problem)
            problems.append(f"preset: {problem}")
        if batch_info.get("models") is not None:
            for p in cfg.presets:
                if any(f.startswith("ltx") for f in p.flags) and "ltx-restore" not in batch_info["models"]:
                    problems.append(f"preset {p.name!r} uses an LTX model but the bridge host has no ltx-restore "
                                    f"in model_weights (Jasna would stop at its download prompt)")

        log.info("Output")
        if not cfg.target_root:
            line("Target root", "NOT SET", "targetRoot")
            problems.append("Target root is not set")
        else:
            ok = os.path.isdir(cfg.target_root) and os.access(cfg.target_root, os.W_OK)
            line("Target root", cfg.target_root, "targetRoot", "writable" if ok else "NOT a writable directory on the Stash host")
            if not ok:
                problems.append(f"target root {cfg.target_root} is not a writable directory on the Stash host")
            roots = batch_info.get("output_roots") or []
            if roots and not any(cfg.target_root.startswith(r) for r in roots):
                problems.append(f"the bridge only accepts outputs under {roots}; target root is {cfg.target_root}")
            try:
                paths = [p["path"] for p in (self.stash.get_configuration(
                    "general { stashes { path excludeVideo } }") or {}).get("general", {}).get("stashes", [])]
                covered = [p for p in paths if cfg.target_root.startswith(p.rstrip("/"))]
                line("Library path", covered[0] if covered else "NONE covers the target root", None,
                     "" if covered else "add it under Settings > Library, or the scan finds nothing")
                if not covered:
                    problems.append("no Stash library path covers the target root")
            except Exception as err:
                line("Library path", f"could not read ({err})")
        line("Title marker", cfg.marker, "titleMarker", f"folder and <title>: 'A - B' -> 'A - {cfg.marker} B'")
        line("Extra genres", ", ".join(cfg.genres), "extraGenres")
        line("Clean HTML in NFO", cfg.clean_html, "cleanHtml")
        line("Ignore patterns", ", ".join(cfg.ignore_patterns), "ignorePatterns")
        line("Stop after", cfg.stop_after or "(full chain)", "stopAfter")

        log.info("Ingestion")
        # The NFO -> Stash step is not this plugin's: it is whatever parser
        # runs on scene creation. Say which one that is, or that none does.
        try:
            plugins = self.stash.call_GQL("{ plugins { id enabled hooks { hooks } } }").get("plugins") or []
            on_create = [p["id"] for p in plugins if p.get("enabled")
                         and any("Scene.Create.Post" in (h.get("hooks") or []) for h in p.get("hooks") or [])]
            nfo_ones = [p for p in on_create if "nfo" in p.lower()]
            if nfo_ones:
                line("NFO parser on scan", ", ".join(nfo_ones), None, "hooked on Scene.Create.Post")
            elif on_create:
                line("NFO parser on scan", "none named *nfo*", None, f"Scene.Create.Post hooks: {on_create}")
                problems.append("no NFO parser plugin is hooked on Scene.Create.Post; the new scene would stay "
                                "unfilled (install/enable nfoSceneParser)")
            else:
                line("NFO parser on scan", "NONE")
                problems.append("no plugin is hooked on Scene.Create.Post; the new scene would stay unfilled "
                                "(install/enable nfoSceneParser)")
        except Exception as err:
            line("NFO parser on scan", f"could not list plugins ({err})")

        log.info("Tags")
        self.ensure_tag_tree()
        if self.tags.created:
            line("Created", ", ".join(self.tags.created))
        for name in [p.tag for p in cfg.presets] + list(STATUS_TAGS.values()):
            tag_id = self.tags.get(name)
            if tag_id:
                count = len(self.stash.find_scenes(
                    f={"tags": {"value": [tag_id], "modifier": "INCLUDES", "depth": 0}}, fragment="id") or [])
                line(name, f"{count} scene(s)")

        if problems:
            for p in problems:
                log.error(p)
            raise SystemExit(f"{len(problems)} problem(s) found; see above.")
        log.info("All checks passed.")


def _same_size(src: str, dst: str) -> bool:
    try:
        return os.path.getsize(src) == os.path.getsize(dst)
    except OSError:
        return False


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    stash = StashInterface(payload.get("server_connection") or {})
    settings = (stash.get_configuration() or {}).get("plugins", {}).get(PLUGIN_ID) or {}
    args = payload.get("args") or {}
    cfg = Config(settings, args)
    run = Run(stash, cfg)
    try:
        if "hookContext" in args:
            run.hook(args["hookContext"] or {})
        elif cfg.mode == "check":
            run.check()
        elif cfg.mode == "queue":
            run.queue()
        elif cfg.mode == "sync":
            run.sync()
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
