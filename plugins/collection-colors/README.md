# Collection Colors — stash UI plugin

Colors scene cards by which configured Library path (Settings → Library)
they live under — auto-discovered, no hardcoded folder paths. Each
collection gets an editable color and a clickable pill that filters the
scene list down to that folder.

## Features

- **Auto-discovery.** Reads `Settings → Library` directly via GraphQL —
  add, remove or rename a Library path and the plugin picks it up on the
  next page load, with no config file to edit.
- **Card pills.** A colored, clickable pill on every scene card (in the
  same spot as stash's own popover buttons), labeled with the folder's
  name. Clicking it filters the scene list to that folder.
- **Color picker.** Injected into the plugin's own row on
  **Settings → Plugins** — one entry per discovered path, each with a
  native color swatch, a type/paste-able hex field, and a copy-to-
  clipboard button. Saved server-side via stash's own plugin config
  storage (`configurePlugin`), not `localStorage`, so it's the same for
  every device you use stash from.
- **Deterministic defaults.** A newly discovered folder gets a stable
  color (hashed from its path) before you ever open the settings panel,
  so nothing renders uncolored while waiting on you.

## Install

Stash reads plugins from the `plugins` directory next to its `config.yml`
— `$HOME/.stash/plugins` for a native install, or wherever you have that
mounted in Docker.

```bash
cp -r collection-colors /path/to/stash/config/plugins/
```

For development, symlink instead so edits land immediately:

```bash
ln -s /path/to/collection-colors /path/to/stash/config/plugins/collection-colors
```

Then in stash: **Settings → Plugins → Reload Plugins**, and refresh the
browser. CSS/JS edits after that need only a browser refresh; adding or
renaming a file needs another Reload Plugins.

## How it works

`collection-colors.js` looks up each visible card's file path (an
`IntersectionObserver` avoids querying cards that are never scrolled into
view), matches it against the longest configured Library path it starts
with, and renders a pill in that collection's saved-or-default color. When
the JAV Layout plugin is installed the lookup goes through its
`window.JLSceneData.path`, which already fetches `files { path }` for
every card in one batched query, so the two plugins share one request per
scene; on its own, this plugin batches a `findScene(id) { files { path } }`
query itself.

Colors are stored as one JSON object (path → hex) in the plugin's own
`collectionColors` setting, written through stash's `configurePlugin`
mutation. The settings-panel UI hides that raw JSON field and replaces it
with the color-picker grid described above.

## Optional: dracula-layout integration

If dracula-layout (or any plugin exposing the same contract) is also
installed, the collection pill additionally appears on the scene detail
page, in a badge slot
(`.jl-scene-badge-slot`) reserved for exactly this purpose next to that
plugin's own resolution/fps/director readout. This plugin only ever
looks for that element and no-ops if it isn't there — nothing here
depends on dracula-layout being installed, and nothing in dracula-layout
depends on this plugin either.

## Known limitations

- Only **top-level** configured Library paths get their own color —
  a subfolder within one isn't treated as its own collection. Revisit if
  per-subfolder granularity is ever needed.
- Card pills only show up once a card scrolls into view (by design, to
  avoid querying every scene on a large library up front) — the very
  first render of a long list won't have pills until you scroll.
- Colors are global per stash instance, not per-user.

## License

MIT — see [LICENSE](LICENSE).
