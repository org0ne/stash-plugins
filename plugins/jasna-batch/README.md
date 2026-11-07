# jasna-batch

Queue scenes for an **offline [Jasna](https://github.com/Kruk2/jasna)
restoration by tagging them**, and get the result back into Stash as a new
scene with the original's metadata.

Tag a scene `Jasna-Batch Queue: <preset>`. The plugin submits the scene's file to
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

The original scene stays where it is. Its tag moves from
`Jasna-Batch Queue: <preset>` to `Jasna-Batch Status: Queued`,
`Jasna-Batch Status: Processing`, then `Jasna-Batch Status: Done` (or
`Jasna-Batch Status: Failed`, with the reason in the Stash log and the
bridge's job log).

On success the original also gets `Jasna-Batch: Restored - <preset>`. The
plugin never removes it: the status tags change on a re-run or a failed
retry, but this one stays. A scene filter that excludes
`Jasna-Batch: Restored` with sub-tags included leaves everything already
restored out of the next pick. The new scene gets
`Jasna-Batch: Output - <preset>`.

## Tags

Every tag the plugin writes starts with `Jasna-Batch`, so it stays apart
from generic tags such as `Jasna`:

| Tag | On | Set by |
|---|---|---|
| `Jasna-Batch` | (parent of all below) | plugin |
| `Jasna-Batch Queue: <preset>` | source scene | you, to queue it; removed once queued |
| `Jasna-Batch Status: Queued` / `Processing` / `Done` / `Failed` | source scene | plugin, follows the job |
| `Jasna-Batch: Restored - <preset>`, under `Jasna-Batch: Restored` | source scene | plugin, permanent |
| `Jasna-Batch: Output - <preset>`, under `Jasna-Batch: Output` | restored scene | plugin, permanent |

The bare `Jasna-Batch: Restored` and `Jasna-Batch: Output` tags go on a
scene only when its preset is unknown: the output file name names no
configured preset, because the preset was renamed or deleted, or the file
was not made by the plugin.

**Upgrading from 0.4.x.** The tags used to be named `Jasna: <preset>`,
`Jasna: Queued` and so on, and `Jasna: Restored`, under `Jasna Batch`. The
plugin's first run renames each to its new name, keeping its id, so its
scenes and any saved filter that uses it carry over. Run **Tag restored
sources** once afterwards: it moves each restored source from the bare
`Jasna-Batch: Restored` tag to the one for its preset, and tags the
existing output scenes. Saved filters on the Restored tag need "include
sub-tags" turned on.

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
   is hooked on scene creation, and creates the tags: `Jasna-Batch`
   (parent), `Jasna-Batch Queue: <preset>` per preset, the four status tags,
   `Jasna-Batch: Restored` and `Jasna-Batch: Output`.

Then tag a scene `Jasna-Batch Queue: LTX` from its page. The hook fires on
the update, the tag turns into `Jasna-Batch Status: Queued`, and the Stash log says which job it
became. Long jobs are normal (an LTX preset can take hours per film); the
bridge's `journalctl --user -u stash-jasna-bridge` and `/jasna/health`
show progress.

## Upscaling before Jasna

Add `--upscale` to a custom preset to upscale the source 2x first:

```
LTX 2x = --upscale --restoration-model-name ltx --codec hevc --cq 24
```

The flag is a plain switch (`-upscale` works too). The method is set in the plugin
(`UPSCALE_METHOD` in `jasna_batch_core.py`, `METHODS` in
`jasna_batch_upscale.py`): `qtgmc-compact-2x`, QTGMC deshimmer and the
2xNomosUni Compact model from
upscale-toolkit. It runs on the job's GPU turn, so it counts towards the
job's time on the queue page. The upscaled file is near-lossless HEVC (QP 20)
on the bridge's local scratch, about 15-30 MB per minute of video (6 GB for
three hours of 720p), and is deleted with the job's working folder. The job
fails at the start if the scratch disk may not hold it, estimated
pessimistically from the frame count and output size.

- **720p and below.** A taller source is not queued: the scene goes to
  `Jasna-Batch Status: Failed` and loses its trigger tag.
- **Dropped frames are filled in.** A constant-rate source with frames missing
  here and there (common in `.wmv`, whose 1 ms timestamps also make 29.97 fps
  read as alternating 33 and 34 ms steps) is upscaled with frames placed by
  timestamp: the frame before a gap is repeated, so the audio stays in sync.
  The job log says how many frames were added. This needs the toolkit's
  `vs_pipeline.py --cfr`.
- **Variable frame rate sources fail** at the start of the job, because the
  pipeline writes a constant rate that would drift from the audio. A source
  counts as variable when more than 1% of its frame steps are not one frame
  long.
- **Bridge setup.** On each node that should upscale, install upscale-toolkit
  (in `~/Applications/upscale-toolkit`, or set `JASNA_UPSCALE_TOOLKIT` for the
  bridge's service) and point `batch.wrapper` at this plugin's
  `jasna_batch_upscale.py`. Nodes without a wrapper are skipped for these
  presets, and *Check settings* reports whether the wrapper can run.

## Audio an MP4 cannot hold

Jasna copies the source's audio into its MP4 output, so a source with audio
an MP4 cannot hold, such as WMA Pro in a `.wmv`, fails at the end of the job,
when Jasna first writes the output. The same wrapper fixes this for every
preset. Before Jasna starts, it asks ffmpeg whether each audio stream fits in
an MP4. If one doesn't, it remuxes the source into the job's working folder,
copying the video and converting that stream to AAC. A 2-hour WMV takes about
1.5 minutes. Other sources go to Jasna as they are. Upscale presets do the
conversion in their own mux step.

This only happens on nodes whose `batch.wrapper` points at
`jasna_batch_upscale.py`; on other nodes these sources still fail.

## Per-video colour LUT

Many older titles have a yellow or warm cast. Add `--auto-lut` to a preset
to give each source its own colour LUT:

```
LTX auto = --auto-lut --restoration-model-name ltx --codec hevc --cq 24
```

Before Jasna starts, the wrapper runs `jasna_batch_lut.py` on the source.
It samples 80 frames, which takes 15-40 seconds, and measures two things:

- **warmth**: how much redder than blue the brightest surfaces are (walls,
  sheets, clothing), and
- **skin**: how red the skin already is.

The LUT is Bombadil MIGD-358 blended with no change at all. A neutral source
(warmth 0.06 or less) gets nothing and a warm one (0.18 or more) gets the
full LUT. MIGD-358 also adds red to skin, so the blend drops to nothing when
skin is already red (0.28 full, 0.30 none). These cut-offs were judged by eye
on 25 videos. A source that needs no LUT goes to Jasna without one.
Otherwise the flag becomes Jasna's `--lut`, which Jasna applies on the GPU to
the restored frames before encoding.

Two files go next to the source:

| File | What |
|---|---|
| `<stem>.lut.json` | what was measured, the blend, the cut-offs (always) |
| `<stem>.lut.cube` | the LUT Jasna got (only when the blend is above 0) |

They are sidecars, so ingest copies them next to the restored file under its
new name (`CODE_LTX-auto_jasna.lut.json`), and the record stays with both
files.

- **Reviewing and overriding.** An existing `.lut.json` is used as it is, and
  the source is not measured again. To change one video, set
  `"manual": true` and a `"blend"` of your own (0 = no LUT, 1 = full
  MIGD-358) in its `.lut.json`, then queue it again. Delete the
  `.lut.json` to have it measured again. To look before anything runs,
  measure ahead on the bridge host:
  `~/Applications/upscale-toolkit/.venv/bin/python jasna_batch_lut.py VIDEO...`
  (any Python with numpy works).
- **Read-only source folders.** The LUT is written to the job's working
  folder and is used but not kept. The job log says so.
- **Not with `--lut`.** A preset with both is refused by *Check settings* and
  by the wrapper.
- **Bridge setup.** This uses the same `batch.wrapper` as `--upscale`. It
  also needs a Python with numpy: the wrapper uses `JASNA_LUT_PYTHON` if set,
  else its own Python if that has numpy, else upscale-toolkit's venv. It
  needs the base LUT at
  `~/Applications/JasnaCubemaps/Bombadil_LUTs/Bombadil MIGD-358.cube` (or
  `JASNA_LUT_BASE`), and a Jasna that has `--lut` (0.12.x does). Nodes
  without a wrapper are skipped for these presets.
- **Can be combined with `--upscale`.** The LUT is measured on the original
  source first, so an unreadable source fails within a minute instead of
  after the upscale.

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
  the job unacknowledged on the bridge and the scene `Jasna-Batch Status: Failed`).
- **Tag restored sources** - walk the target root and add the Restored tag
  for its preset to the source scene of every `{stem}_{preset}_jasna.mp4`
  found there, and the Output tag to that file's own scene, also for
  restorations made before this plugin or by hand. The source is the
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
