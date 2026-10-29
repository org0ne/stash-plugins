# jasna-batch

Queue scenes for an **offline [Jasna](https://github.com/Kruk2/jasna)
restoration by tagging them**, and get the result back into Stash as a new
scene with the original's metadata.

Tag a scene `Jasna: <preset>`. The plugin submits the scene's file to
[stash-jasna-bridge](https://github.com/org0ne/stash-jasna-bridge), which
runs `jasna --input ... --output ...` with that preset's flags, one job at a
time, on the same GPU as the live JASNA ON stream (the
[jasna-switch](../jasna-switch) player toggle shows `JASNA: BATCH` while a
job runs). When the job finishes the plugin:

1. copies the source folder's sidecars next to the restored file, renamed
   to its new stem (`CODE.en.srt` -> `CODE_LTX_jasna.en.srt`, the
   `-whisper-command.txt`, `.vtt`, `.subtrans`, ...); `fanart.jpg`,
   `folder.jpg`, `cover.jpg`, `extrafanart/`, `.actors/` are copied as they
   are;
2. writes `CODE_LTX_jasna.nfo` from the source `.nfo` with the title marker
   inserted (`Performer - Title` -> `Performer - [LTX] Title`), the extra
   genres added after the last `<genre>`, and the Jellyfin/Kodi leftovers
   stripped (`<art>` with its old absolute paths, `<fileinfo>`, `<resume>`,
   `<playcount>`, actor `<thumb>`s, ...). This is clean_nfo_flexible.py's
   behaviour;
3. writes `CODE_LTX_jasna-jasna-command.txt`: the exact Jasna command, the
   preset, start/finish times, duration, exit code, Jasna version, sizes;
4. scans the new folder. The NFO is complete before the scan, so the
   nfoSceneParser you already run (hooked on scene creation) fills the new
   scene from it as the scan creates it: title, performers, studio, date,
   genres as tags, stash ids. This plugin does nothing NFO -> Stash itself.

The original scene stays where it is. Its tag moves from `Jasna: <preset>`
to `Jasna: Queued`, `Jasna: Processing`, then `Jasna: Done` (or `Jasna:
Failed`, with the reason in the Stash log and the bridge's job log). On
success it also gets `Jasna: Restored` (the **Restored tag** setting), which
the plugin never removes: the status tags change on a re-run or a failed
retry, this one stays, so a scene filter with "tags exclude `Jasna:
Restored`" leaves everything already restored out of the next pick.

## Where things land

```
{targetRoot}/{source folder name, marker inserted}/{source stem}_{preset}_jasna.mp4
```

For `/lib/Censored/Censored HD/MIGD-100 Rio Hamasaki - Title (2008-02-01)/MIGD-100.mkv`
and preset `LTX` under target root `/lib/LTX`:

```
/lib/LTX/MIGD-100 Rio Hamasaki - [LTX] Title (2008-02-01)/
    MIGD-100_LTX_jasna.mp4
    MIGD-100_LTX_jasna.nfo
    MIGD-100_LTX_jasna.en.srt
    MIGD-100_LTX_jasna-jasna-command.txt
    fanart.jpg  folder.jpg  extrafanart/
```

- **Multi-file folders.** Each Stash scene is one file; tagging one part
  restores that part only, and its output joins the same target folder as
  the other parts (the folder name comes from the source folder). Sidecars
  of the other parts are left alone.
- **Same scene, same preset:** if the output file already exists the scene
  is marked Done and nothing is queued. A different preset gives a
  different stem (`_LTXhq_jasna.mp4`) in the same folder.
- `*.bak` and `.lada_*` are not copied (*Sidecars to ignore*).

## Install

1. Set up stash-jasna-bridge with `jasna.manage_process = true` and a
   `[batch]` section (`enabled = true`, optionally `output_roots`,
   `scratch_dir` on a local disk, `min_free_gb`). Restart it.
2. Install this plugin from the plugin source (Settings > Plugins >
   Sources) or symlink the folder into Stash's `plugins/` directory, and
   `pip install -r requirements.txt` into the Python Stash runs plugins
   with (Settings > System > Python path).
3. Settings > Plugins > Jasna Batch: set **Target root** (a folder under
   one of your Stash library paths, visible to the bridge host under the
   same path or via its `[[paths.map]]`) and **Presets**, e.g.

   ```
   LTX = --restoration-model-name ltx --codec hevc --cq 24 # standard LTX;
   LTX hq = --restoration-model-name ltx-8step --codec av1 --cq 30
   ```

   Leave **Bridge URL** empty for a bridge on the Stash host at its default
   port; this plugin runs on the server, so the browser's `/jasna` route
   does not apply. The scan of each new folder uses Stash's own scan
   defaults plus whatever **Generate on scan** names (default `sprites`,
   the scene scrubber, which a stock Stash leaves off during scans).
   **Audio codec** (default `aac`) makes the bridge re-encode any non-AAC
   audio after Jasna finishes: Jasna copies the source's audio stream, so
   an MP3 or AC3 source would otherwise become an MP4 Stash plays without
   sound. The command sidecar says what was done.
4. Run the **Check settings** task (Settings > Tasks). It asks the bridge
   whether it can run a job (binary, models, activation, scratch space,
   GPU), verifies the target root, the library path and that an NFO parser
   is hooked on scene creation, and creates the tags: `Jasna Batch`
   (parent), `Jasna: <preset>` per preset, the four status tags and
   `Jasna: Restored`.

Then tag a scene `Jasna: LTX` from its page. The hook fires on the update,
the tag turns into `Jasna: Queued`, and the Stash log says which job it
became. Long jobs are normal (an LTX preset can take hours per film); the
bridge's `journalctl --user -u stash-jasna-bridge` and `/jasna/health`
show progress.

## Several bridges (one per GPU host)

List them in **Bridge URL** as `name = url` entries separated by `;`, with
what each node is worth and what it may run:

```
debeast = http://127.0.0.1:8770/jasna speed=3; rtx3060 = http://192.168.11.157:8770/jasna only=LTX
```

Each node is a stash-jasna-bridge with `[batch]` enabled on its own GPU
host. A tagged scene goes to the node with the least waiting time: the
seconds of source video queued and still running there (Stash knows the
durations; the running job counts by the percent the bridge reports),
divided by the node's `speed` (relative throughput, default 1; a 4090
against a 3060 is about 3). `only=A, B` keeps a node to those presets
(say, a card without the VRAM for an LTX model). An unreachable node, or
one whose batch queue is off, is skipped with the reason in the log; a
scene already queued or running for that preset on any node is not queued
again. *Sync jobs* walks every node (each bridge's callback runs it; a
node that is down is reported and skipped), the command sidecar names the
node, and *Check settings* checks each node and says which presets it
takes. Every node needs the media and the target root at the same paths
(or its own `[[paths.map]]`), a `stash.url` that reaches this Stash for
the callback and scene lookups, and the plugin's Bridge token if it uses
`auth.mode = token`. The jasna-switch plugin takes the same list (with the
browser-side addresses) so the live toggle can use the second GPU too.

## Watching the queue

The bridge serves the queue as a page for a phone or desktop browser at
`<bridge>/queue` (with the usual reverse-proxied bridge:
`https://<your stash>/jasna/queue`): the running job with its elapsed
time, the queued jobs in order with a Remove button, and the last finished
ones with duration, size, error and a link to Jasna's log. Scenes are named
with their Stash titles and link to the scene. The link is shown under the
**Queue page** setting on Settings > Plugins: one page showing every node
(each with its GPU meter, running and queued jobs, and **Pause** / **Pause
now** / **Resume** buttons) plus one link per node. Set that setting only
when a bridge is on another origin (`name = url; ...` for several).

**Pause** lets the running job finish and starts no other on that node;
**Pause now** also stops the running job and puts it back at the head of
the queue (Jasna cannot resume a run, so its progress is lost), which frees
the GPU for other work within seconds. A paused node gets no new jobs from
this plugin (they go to another node, or wait on the least loaded one when
every node is paused) until **Resume**. The pause survives a bridge restart.

## Tasks

- **Check settings** - everything above, writes only tags.
- **Queue tagged scenes** - submit every scene carrying a trigger tag
  (scenes tagged while the bridge was down). The hook does the same per
  scene as it is tagged.
- **Sync jobs** - match status tags to the bridge's jobs and ingest the
  finished ones. The bridge runs this itself after every job state change
  (`batch.notify_plugin` / `notify_task` in `bridge.toml`); run it by hand
  to retry an ingestion after fixing its cause (a failed ingestion leaves
  the job unacknowledged on the bridge and the scene `Jasna: Failed`).
- **Tag restored sources** - walk the target root and add the Restored tag
  to the source scene of every `{stem}_{preset}_jasna.mp4` found there, also
  for restorations made before this plugin or by hand. The source is the
  scene outside the target root whose file has that stem; a stem that
  matches no scene or several is logged and left alone. Run it once after
  installing, and again after restoring something outside the plugin.
- Queue, sync and tag restored sources each have a **dry run** variant.

**Stop after** = `prepare` halts the chain after the folder is prepared,
without scanning, to check the files by hand first.

## Requirements

- stash-jasna-bridge with the batch queue (0.4.0+ for pausing), Jasna 0.11.x with the
  models your presets use already downloaded (Jasna's download prompt is
  interactive; a headless job would stop there), and the per-machine
  activation done.
- Stash v0.31.x; `stashapp-tools` in Stash's plugin Python.
- An NFO parser hooked on scene creation, i.e. nfoSceneParser with its
  `Scene.Create.Post` hook enabled, for the NFO -> Stash step.

## Notes

- Nothing is deleted or overwritten: the source stays, an existing output
  is never replaced (the bridge fails the job and keeps its result in its
  scratch folder), and sidecars are copied only when missing or of a
  different size.
- Tests for the naming, sidecar and NFO logic need no Stash:
  `python3 -m unittest discover -s tests -v` from this folder.
