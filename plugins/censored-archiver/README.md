# censored-archiver

Once a restored version of a censored scene exists (an LTX or Jasna DL
library, say), the censored original is clutter. This plugin finds every
censored scene whose **studio code** is also the code of a scene in one of
the **reference** libraries and moves its whole scene folder into an
**archive** folder that mirrors the original tree:

```
Censored/Censored HD/ABP-984 Actress - Title (2020-06-12)/
  -> Archived/Censored/Censored HD/ABP-984 Actress - Title (2020-06-12)/
```

Videos move through Stash's own `moveFiles`, so each scene keeps its id,
metadata, play history, markers and captions, and no scan is needed. The
rest of the folder (`.nfo`, artwork, subtitles, `extrafanart/`, ...)
follows by rename once the videos have arrived. Nothing is overwritten and
nothing is deleted.

It complements [jasna-batch](../jasna-batch)'s *Archive restored sources*
task, which only knows the scenes it restored itself (by their Restored
tag). This one matches by code, so it also catches restorations made
elsewhere.

## Install

1. Install from the plugin source (Settings → Plugins → Sources), or symlink
   this folder into your Stash `plugins/` directory and reload plugins.
2. Install the Python packages into the interpreter Stash runs plugins
   with (Settings → System → Python path):

   ```
   /path/to/stash/.venv/bin/pip install -r requirements.txt
   ```

3. Fill in the settings (Settings → Plugins → Censored Archiver) and run
   **Check settings**.

## Settings

| Setting | Meaning |
| --- | --- |
| Censored roots | Comma-separated folders holding the censored scenes, e.g. `/lib/Sorted/Censored, /lib/Sorted/Web Censored`. |
| Reference roots | Comma-separated folders holding the restored versions, e.g. `/lib/Sorted/LTX, /lib/Sorted/Jasna DL`. |
| Archive root | Where folders go: `{archive root}/{censored root's folder name}/{path inside it}`. Must be inside a Stash library path. |
| Undo log | JSONL file every moved folder is appended to. Blank = `.censored-archiver.jsonl` in the archive root. |
| Clean afterwards | Queue a Clean of the old folders after a run that moved something (see below). |

Paths are as the Stash host sees them. Roots of different kinds may not be
inside one another.

## Tasks

| Task | What it does |
| --- | --- |
| Check settings | Logs each root, whether it exists, how many scenes it holds and which library covers it; checks the archive root is writable and inside a library. Moves nothing. |
| Archive ... (dry run) | Logs every folder that would move, with its code, the reference library it matched and how many files go with it. Lists every problem, not just the first. |
| Archive ... | Moves the folders. Stops at the first error. |

## How a scene is picked

- Codes compare trimmed and case-folded, otherwise exactly (`abp-984 ` =
  `ABP-984`; `ABP984` does not match `ABP-984`).
- A scene is a candidate when it has a code, a file under a censored root,
  and some scene with a file under a reference root has the same code. Only
  the scene's files under a censored root move; its other files stay put.

## How a folder moves

For each candidate's folder, in order:

1. **Skipped** (logged, the run goes on) when the folder is less than two
   levels below its censored root (`Censored/Censored HD/` itself holds
   other scenes), or when it holds a video that is not a candidate: a part
   with another code, or a file Stash does not know.
2. **Stops the run** when a video Stash lists is not on disk, or when any
   file's place in the archive is already taken.
3. Every video goes through `moveFiles`, and each one is checked: gone from
   the old place, present in the new one.
4. Every other file follows by rename. Subfolders keep their layout. The
   emptied folder is removed.
5. Stash is asked again for each scene. Its file has to show the archive
   path.

Candidates in a subfolder of another candidate's folder move with the outer
folder.

Afterwards, old folder records and any images Stash had indexed in the old
folder (fanart, `.actors/`, when the censored library includes images) stay
in the database until the next **Clean**. Turn on *Clean afterwards* to have
the run queue that Clean for the old folders itself.

## Undoing a move

Each line of the undo log is one folder:

```json
{"time": "...", "source_folder": "...", "destination_folder": "...",
 "scenes": ["123"], "codes": ["ABP-984"], "matched": ["Jasna DL"],
 "files": [{"scene_id": "123", "file_id": "456", "source": "...", "destination": "..."}],
 "sidecars": [["<old path>", "<new path>"], ...]}
```

To put a folder back, call `moveFiles(input: {ids: [<file_id>],
destination_folder: "<dirname of source>"})` for each entry in `files`,
then move each `sidecars` pair back. A line with an `"error"` key is a
folder that stopped part way. It lists only what had moved by then.

## Tests

```
python3 -m unittest discover -s tests
```
