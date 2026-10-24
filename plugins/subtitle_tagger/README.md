# Subtitle Tagger

Tags Stash scenes with the models WhisperJAV used to transcribe and translate
them, read from the command sidecar written next to each video.

Reads sidecars named `*-whisperjav-command.txt` or
`*-whisper-command.txt` (both are accepted).

### Transcription models — tagged `Transcription: <model>`

| Flag | Notes |
| --- | --- |
| `--model` | global Whisper override, e.g. `large-v2`, `turbo` |
| `--pass1-model`, `--pass2-model` | ensemble passes |
| `--hf-model-id` | `--mode transformers` |
| `--qwen-model-id` | `--mode qwen` |
| `--crispasr-backend` | `whispercpp`, `parakeet`, `cohere` |
| `--qwen-generator`, `--generator` | fallback only, when no explicit model id is present |
| `--overrides`, `--passN-params`, `--passN-hf-params`, `--passN-qwen-params`, `--generator-config` | JSON blobs — `model_id` / `model` keys are read from inside |

### Translation models — tagged `Subtitled: <model>`

| Flag | Notes |
| --- | --- |
| `--translate-model` | explicit model, e.g. `deepseek-reasoner`, `translategemma:12b` |
| `--translate-provider` | fallback only — `deepseek`, `gemini`, `claude`, … |

### Legacy sidecar formats

Sidecars written by older tooling are recognised alongside current WhisperJAV:

| Format | Recognised from |
| --- | --- |
| `whisper-ctranslate2` | `--model`, `--task` |
| `whisper-faster-xxl` / `faster-whisper-xxl` | `--model`, `--task` |
| `script version=0.3x` key/value | `whisper model=`, `deepl translation=` |
| `script version=0.9x` key/value | `translation_backend=`, `local_model=` |
| WhisperJAV `Args:` / `Command :` preamble | the flags on that line |

Three behaviours specific to these:

- **Direct translation.** `--task translate` (or `--subs-language
  direct-to-english`) means Whisper produced English itself, with no separate
  transcription step — the ASR model is reported as `Subtitled: <model>` only.
  `--task transcribe`, or no task at all, yields `Transcription: <model>`.
  If a separate LLM translator is documented as well, that LLM produced the
  final subtitles and takes the `Subtitled:` tag outright; the ASR model is
  not reported alongside it.
- **Re-runs.** A sidecar appended to across several runs is read from its last
  record only; that is what produced the subtitles now on disk.
- **`postprocess_model=`** is ignored — it edits already-translated text rather
  than producing the subtitles.

Sidecars are decoded as UTF-8 (with or without BOM), UTF-16, or CP1252. Files
that are not text are reported and skipped.

### Explicitly ignored

Flags carrying arbitrary human prose are stripped out **before** any model flag
is matched, so a title or plot summary can never become a tag:

`--translate-title`, `--translate-plot`, `--translate-actress`, `--credit`,
`--initial_prompt`, `--context`, `--qwen-context`

`--initial_prompt` in particular is written unquoted and runs to the end of the
line in the legacy formats; everything after it is discarded.

Pipeline and runtime selectors (`--mode`, `--passN-pipeline`, `--asr`) are
deliberately **not** tagged either: they name an engine or preset, not a model.

`--qwen-generator anime-whisper` is canonicalised to `litagin/anime-whisper` so
it collapses with the equivalent `--qwen-model-id`. Placeholder values (`auto`,
`none`, `default`) are ignored.

Two ways to run it. Both share the same parsing and tagging code.

## As a Stash plugin (recommended)

Runs on the Stash host, so scene paths are read directly and no path mapping is needed.

### Install

Copy these three files into a folder under your Stash plugins directory
(`~/.stash/plugins/subtitle_tagger/` by default, on the Stash machine):

    subtitle_tagger.yml
    subtitle_tagger_plugin.py
    subtitle_tagger.py

Then **Settings > Plugins > Reload Plugins**. Requires `python3` on the Stash host.

### Configure

Under **Settings > Plugins > Subtitle Tagger**:

- **Folder Path** — library folder used by the "Tag Folder" task, as Stash sees it.
- **Scenes Per Request** — scenes covered by each bulk update (default 200).
  Stash pays a fixed cost per request, so larger is faster; lower it only if
  the log shows request timeouts.
- **Tag On Scan** — tag each new scene automatically as it is scanned in (default on).
- **Do Not Create Tags** — only apply tags that already exist.
- **Dry Run** — log what would happen, change nothing.

### Run

Automatic: the `Scene.Create.Post` hook fires per scene during a library scan.
Note that a *rescan* fires no hook when a new caption attaches to a scene
that already exists, so a subtitle produced later is not tagged by the hook.

From another plugin: `runPluginTask(plugin_id: "subtitle_tagger", task_name:
"Tag Scene", args_map: {scene_id: <id>})` tags that one scene. The WhisperJAV
Fleet plugin queues this right after its completion rescan, which covers the
rescan gap above.

Manual, from **Settings > Tasks > Plugin Tasks**:

- **Tag All Scenes** — walk the whole library.
- **Tag Folder** — walk only the configured Folder Path.
- **Retag All Scenes (Remove Stale)** — reconcile: add missing tags *and* remove
  ones the sidecar no longer names. Use after re-running whisper.
- **Retag Folder (Remove Stale)** — as above, limited to Folder Path.
- **Dry Run - All Scenes** / **Dry Run - Remove Stale** — preview either.

### Removing stale tags

Re-running whisper with a different model leaves the old `Transcription:` /
`Subtitled:` tags behind. Enable **Remove Stale Tags** (or use a Retag task, or
`--prune` on the CLI) to reconcile each scene against its current sidecar.

Three guards make this safe to run across a whole library:

- Only tags starting with `Transcription: ` or `Subtitled: ` are ever removed.
  Every other tag on the scene is untouched.
- A scene with no readable sidecar is skipped entirely — its tags are left
  exactly as they are.
- A sidecar that parses to no models at all is treated as *unknown*, not as
  "this scene should have no tags", so nothing is removed.

Preview with **Dry Run - Remove Stale** before running it for real.

Output appears in the Stash log viewer (**Settings > Logs**).

## As a standalone CLI

For running from a different machine than Stash. Standard library only.

    export STASH_URL=http://your-stash-host:9999
    export STASH_API_KEY=your-api-key

    ./subtitle_tagger.py /path/to/media \
        --path-map /local/prefix=/stash/side/prefix --dry-run -v

Because the CLI walks a local tree and searches Stash by path, `--path-map`
translates local paths to their Stash equivalents. `--strict-path` requires an
exact match instead of falling back to a unique filename match.

## Auditing legacy sidecars

`sample_commands.py` copies a representative subset of sidecars out of a media
tree so their formats can be inspected — useful for finding commands written by
older tooling (`faster-whisper-xxl`, `whisper-ctranslate2`, plain `whisper`)
whose flags the tagger may not yet recognise.

    ./sample_commands.py /mnt/media --dest ./audit -n 60 --dry-run   # report only
    ./sample_commands.py /mnt/media --dest ./audit -n 60             # copy

Samples are spread evenly across the span of file dates rather than drawn at
random, so a rarely-used old tool is as likely to appear as a recent one. Add
`--by-variant` to spread across distinct command shapes instead — better when
the goal is format coverage rather than a timeline. `--seed` makes the sample
reproducible (default 0).

Copies are renamed `<date>_<seq>_<original>` and a `manifest.tsv` records the
original path, timestamp and detected tool for each.
