# stash-plugins

A [stash](https://github.com/stashapp/stash) plugin source — install any
plugin below directly from **Settings → Plugins → Sources**, or clone the
one you want and symlink it into your `plugins/` folder for development.

## Install as a source

Add this URL under Settings → Plugins → Sources:

```
https://org0ne.github.io/stash-plugins/stable/index.yml
```

Stash will list every plugin in this repo, ready to install with one click.

## Plugins

| Plugin | Description |
| --- | --- |
| [collection-colors](plugins/collection-colors) | Colors scene cards by which configured Library path they live under, auto-discovered — no hardcoded paths. |

## Repo structure

```
plugins/
  <plugin-id>/
    <plugin-id>.yml   <- manifest; filename (minus .yml) IS the plugin id
    <plugin-id>.js
    <plugin-id>.css
    README.md
    LICENSE
```

Each plugin is self-contained and independently zipped — nothing here
requires another plugin in this repo, unless its manifest's `ui.requires`
says otherwise.

## How the source is built

`build_site.py` scans `plugins/*/`, and for each `plugins/<id>/<id>.yml`
it finds:

1. reads `name`/`description`/`version` from the manifest,
2. zips every file in that plugin's directory,
3. records the zip's sha256 and the last git commit touching that
   directory (for the build-suffixed version and date),
4. writes one `index.yml` covering every plugin found.

`.github/workflows/deploy.yml` runs that on every push to `main` and
publishes the result to GitHub Pages — same index schema
[stashapp/CommunityScripts](https://github.com/stashapp/CommunityScripts)
uses, so any stash instance that already has a source like that configured
can add this one the same way.

## Adding a new plugin

1. `mkdir plugins/<new-id>` and put `<new-id>.yml` (+ `.js`/`.css` as
   needed) in it — the manifest filename must match the directory name.
2. Add a row to the table above.
3. Push to `main`. The Actions workflow rebuilds and republishes the
   index automatically — nothing else to do.

## Local development

Symlink a plugin's folder into your stash instance's plugins directory so
edits land without reinstalling:

```bash
ln -s /path/to/stash-plugins/plugins/collection-colors /path/to/stash/config/plugins/collection-colors
```

Then in stash: **Settings → Plugins → Reload Plugins** after adding or
renaming a file; a browser refresh alone picks up CSS/JS edits.
