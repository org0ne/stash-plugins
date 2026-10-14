# jav-nfo-refresh

Refreshes Japanese-source metadata for tagged scenes from **javstash.org**,
**libredmm.com** and **jav321.com**, in that priority order (first hit
wins). For each scene it:

- writes the Japanese title and description into the scene's custom fields
  (`Original Title`, `Original Description`);
- optionally translates them into the scene's title and details, rebuilding
  the title as `{Performers} - [{Group}] {Title} [{Code}]`;
- turns the scraped genres into tags (created if missing, translated to
  English, existing tags never removed);
- records the javstash scene UUID as a stash ID, and the source's URLs;
- mirrors all of that into the `.nfo` sidecar sitting next to the video
  file, using the same element names Jellyfin and nfoSceneParser use
  (`originaltitle`, `original_plot`, `genre`, `stashid`, `URL`, ...).

It is the plugin form of the standalone `jav_nfo_refresh_unified.py` CLI.
The scraping, translation and NFO code is unchanged and lives in
`jav_nfo_core.py`; `jav-nfo-refresh.py` is the Stash shell around it.

## Install

1. Install from the plugin source (Settings → Plugins → Sources), or symlink
   this folder into your Stash `plugins/` directory and reload plugins.
2. Install the Python packages into the interpreter Stash runs plugins
   with (Settings → System → Python path):

   ```
   /path/to/stash/.venv/bin/pip install -r requirements.txt
   ```

   `google-cloud-translate` is only needed for the `cloud-llm` engine.
3. Create the trigger tag (default `JAV NFO Refresh`) and, if you have not
   already, configure javstash as a stash-box under Settings → Metadata
   Providers. The plugin reuses that API key unless you set its own.

## Use

Tag the scenes you want refreshed with the trigger tag, then run one of the
tasks under Settings → Tasks → Plugin Tasks:

| Task | What it does |
| --- | --- |
| Check settings | Logs every effective setting and where it comes from, verifies the API key, credentials file and trigger tag, and runs a one-word test translation through the configured engine. Writes nothing. Run this first. |
| Refresh tagged scenes | Look each tagged scene up; scenes a source already refreshed are skipped without a request. |
| Refresh tagged scenes (dry run) | Same, but only logs what would change. |
| Rescrape tagged scenes | Query the sources even for already-refreshed scenes. Existing translations are kept unless the description changed. |
| Re-translate tagged scenes | No lookups. Re-translate title and details from the existing `Original Title` / `Original Description` (or the NFO). Genres untouched. |
| Re-translate tagged scenes (dry run) | Same, but only logs. |
| Reclassify failures | One-time: sort scenes tagged `Metadata Refresh Failed` before the outcome tags existed into No Studio Code / Not Found / Source Error, from the Stash log. |

Progress and per-scene outcomes go to the Stash log (Settings → Logs,
level Info or lower). Every run starts with a header line stating the
effective mode, sources and translation engine, and source errors are
logged at Error level.

### How a scene is judged

- **Studio code**: the scene's Studio Code field, then the NFO `tagline`,
  then the filename. Edition letters are kept (`KTRA-309E`, `IBW-610Z`,
  `MXPC-S011`) and the bare form (`KTRA-309`) is also tried on the
  sources; part markers (`-pt1`, ` Part 2`) are dropped. When the Studio
  Code disagrees with the filename the Studio Code wins and a warning is
  logged.
- **Already refreshed by a source**: its marker tag (`nfo refreshed
  javstash` / `nfo refreshed LibreDMM` / `nfo refreshed jav321`) on the
  scene, the same marker as an NFO genre, a javstash stash ID on the scene,
  or a legacy `<stashid endpoint="javstash">` in the NFO. A scene refreshed
  by a higher-priority source is never sent to a lower one.
- **Already translated**: the NFO's `title_translated` / `title_backup`,
  the `Translated At` custom field, or an English title sitting next to a
  Japanese `Original Title` (scenes imported by nfoSceneParser from
  already-translated NFOs carry no marker of their own). Translated scenes
  keep their title and details; a *new* description is still translated.
- **Failures**: a scene no source could match gets the `Metadata Refresh
  Failed` tag (and NFO genre), cleared by any later success.
- **Manual override**: a scene tagged `Metadata Refreshed Manually` is
  never touched.

### Outcome tags

Every run leaves its outcome on the scene as tags, all nested under a
`Metadata Refresh` parent so one filter catches them. Each tag's name says
what to do about it; the same text is stored as the tag's description.

| Tag | Set when | What you do |
| --- | --- | --- |
| Refresh: No Studio Code | no code in Stash, tagline or filename | Enter the Studio Code, re-tag |
| Refresh: Not Found | every source missed the code | Check the code by hand, or tag `Metadata Refreshed Manually` |
| Refresh: Source Error | a source returned errors (outage, rate limit) | Nothing; the trigger tag stays and the next run retries |
| Refresh: Code Mismatch | Studio Code disagrees with the filename | Decide which is right, fix, re-tag |
| Refresh: Alternate Match | a source matched another form of the code | Spot-check it is the same release |
| Refresh: Merged | a lower source only filled gaps on a scene a higher one owned | Spot-check the mixed data |
| Refresh: No Original Text | re-translate found no Japanese text | Run Refresh, or fill the custom fields |
| Refresh: Translation Failed | title/description translation returned nothing | Re-run; nothing was overwritten |
| Refresh: Genres Incomplete | some genres were skipped | Rescrape later |
| Refresh: No NFO | no sidecar next to the file | Generate one if you want it |
| Refresh: NFO Error | sidecar unreadable, invalid or not writable | Fix the file or the share |

A tag is removed automatically the next time its condition is checked and
found clear. Source Error, Translation Failed, Genres Incomplete and NFO
Error keep the trigger tag on the scene so a plain re-run retries it; the
others drop it (when "Remove trigger tag when done" is on) so the queue
drains to scenes that need a person. `Metadata Refresh Failed` stays as
the umbrella parent of No Studio Code and Not Found, and is no longer
applied on source errors. The one-time **Reclassify failures** task sorts
scenes tagged Failed before these tags existed, using the Stash log.

When a lower-priority source wins a scene a higher one refreshed earlier,
the existing data is protected: the winner only fills missing fields and
its URLs are merged rather than replacing the list.

## Settings

| Setting | Default | Notes |
| --- | --- | --- |
| Trigger tag | `JAV NFO Refresh` | Must exist in Stash. |
| Remove trigger tag when done | off | Removed after any terminal outcome; kept on source errors so the next run retries. |
| Sources | all | Comma-separated subset of `javstash`, `libredmm`, `jav321`. Priority order is fixed. |
| javstash API key | stash-box key | |
| Request delay | 1 s | Per source, every HTTP call. |
| libredmm processing wait | 30 s / 0 s | 30 when libredmm is the first selected source, else 0. |
| Update release date | off | Scene date + NFO `premiered`/`releasedate`. |
| Disable translation | off | Also skips genre tags, which are only written translated. |
| Translation engine | `deep-translator` | `deep-translator`, `cloud-nmt` or `cloud-llm`. The free engine is rate-limited by Google and fails in bursts; the cloud engines are reliable. |
| Google Cloud API key / project ID / region / credentials file | | Per engine. The credentials file may be an absolute path or a bare filename for a key placed in the plugin folder (`*.json` there is git-ignored); the project ID is read from it when blank. |
| Plain translated title | off | Skip the `{Performers} - [{Group}] {Title} [{Code}]` rebuild. |
| Disable Stash updates | off | NFO only. |
| Disable NFO writes | off | Stash only; the sidecar is never read either. |
| Disable NFO backup | off | First write keeps `.nfo.bak` and `<title_backup>`. |
| Don't tag failures | off | |
| Original title / description custom field | `Original Title` / `Original Description` | |

`{Group}` comes from a path component named `Lada` or `Leaked`, or an
existing `[Group]` bracket in the old title (see `GROUP_DIR_NAMES` in
`jav_nfo_core.py`).

## Differences from the CLI

Gone: `--target` (the trigger tag selects scenes), `--skip-refreshed-by` /
`--only-refreshed-by` (filter on the marker tags in Stash instead and tag
the result), the misses CSV (the failed tag and the Stash log cover it),
and libredmm sign-in (it never changed the results). `--dry-run`,
`--rescrape` and `--translate-only --retranslate` became tasks.
