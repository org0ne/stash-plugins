# Copy Buttons — stash UI plugin

Adds small click-to-copy buttons next to performer names, performer
disambiguations, and studio codes — wherever stash (or another plugin)
renders them.

## Features

- **Performer name copy button.** On every performer card (grid) and on
  the performer detail page header.
- **Performer disambiguation copy button.** Same two places, next to the
  disambiguation text (e.g. `(愛須心亜)`), which also gets a cyan tint so
  it stands out.
- **Studio code copy button**, on the scene detail page. Attaches to
  whichever position the code actually renders in:
  - if another plugin has relocated "Studio Code" into its own header
    element (`.studio-code` / `.studio-code-text` — dracula-layout uses
    this contract), the button appears there;
  - otherwise it appears next to the native "Studio Code:" line in the
    Details list.

  Either way this plugin only ever looks for those elements — it works
  the same with or without any other plugin installed.
- **Works over plain http as well as https.** Browsers only expose
  `navigator.clipboard` on https or localhost; on a LAN address over http
  the plugin falls back to the classic `execCommand('copy')` path (iOS
  Safari included). If the browser refuses the copy, the button flashes
  red instead of green.

## Install

Stash reads plugins from the `plugins` directory next to its `config.yml`
— `$HOME/.stash/plugins` for a native install, or wherever you have that
mounted in Docker.

```bash
cp -r copy-buttons /path/to/stash/config/plugins/
```

For development, symlink instead so edits land immediately:

```bash
ln -s /path/to/copy-buttons /path/to/stash/config/plugins/copy-buttons
```

Then in stash: **Settings → Plugins → Reload Plugins**, and refresh the
browser. CSS/JS edits after that need only a browser refresh; adding or
renaming a file needs another Reload Plugins.

## License

MIT — see [LICENSE](LICENSE).

The button icons are the path data of three Font Awesome Free 6.7.2 solid
icons (`copy`, `check`, `xmark`), embedded in `copy-buttons.js` so the
plugin needs no Font Awesome stylesheet or plugin on the page. Font
Awesome Free by @fontawesome — https://fontawesome.com — Copyright 2024
Fonticons, Inc. Icons are licensed CC BY 4.0
(https://creativecommons.org/licenses/by/4.0/); full license:
https://fontawesome.com/license/free.
