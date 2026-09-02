# Title Scrubber — stash UI plugin

Strips clutter from scene titles wherever stash renders one — the scenes
grid and the scene detail page.

## What it strips

- A leading `Something - ` prefix before the real title (dash-joined,
  e.g. a release-group or site name tacked on the front).
- A trailing `[bracketed suffix]`.
- Redundant ` - ` dash runs left over after the above.
- A configurable list of quality/status words, matched case-insensitively
  whether they appear bare, in `[brackets]`, or in `(parens)`: `4k`, `HD`,
  `FHD`, `javplayer`, `decensored`, `uncensored`, `censored`, `leaked`,
  `subtitled`, `Lada`. Edit `TITLE_FILTER_WORDS` at the top of
  `title-scrubber.js` to change the list.

Runs as a lightweight `MutationObserver` — no GraphQL fetch, no
dependency on any other plugin — so it works standalone or alongside
anything else installed.

## Install

Stash reads plugins from the `plugins` directory next to its `config.yml`
— `$HOME/.stash/plugins` for a native install, or wherever you have that
mounted in Docker.

```bash
cp -r title-scrubber /path/to/stash/config/plugins/
```

For development, symlink instead so edits land immediately:

```bash
ln -s /path/to/title-scrubber /path/to/stash/config/plugins/title-scrubber
```

Then in stash: **Settings → Plugins → Reload Plugins**, and refresh the
browser. JS edits after that need only a browser refresh; adding or
renaming a file needs another Reload Plugins.

## License

MIT — see [LICENSE](LICENSE).
