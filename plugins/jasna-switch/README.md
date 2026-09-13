# Jasna Switch

A Stash UI plugin that adds a **JASNA ON/OFF** toggle to the scene player,
as a status pill overlaid on the top-right of the video, with a preset pill
beside it. It stays visible in fullscreen and fades with the player controls.
When ON, the player swaps to a live HLS stream restored by
[Jasna](https://github.com/Kruk2/jasna), at the same playback position; OFF
swaps back to the normal Stash source, again preserving position. Stash
itself is unmodified.

The plugin talks only to [stash-jasna-bridge](https://github.com/org0ne/stash-jasna-bridge),
which owns the Jasna process, hands out session tokens, times idle sessions
out, caches segments and proxies the HLS stream on the Stash origin. Set up
the bridge first (its README), then install this plugin: with the bridge
reverse-proxied under the Stash domain the plugin **auto-detects** it at
`<stash-origin>/jasna`, so no plugin settings are needed.

## How it works

1. On a scene page the plugin finds the scene id from the URL and the video
   player, and reads the bridge URL (the setting, or auto-detect). It does
   not query Stash about the scene: the bridge resolves the file itself and
   rejects a scene that has none, which shows as `JASNA: ERROR`.
2. Toggle ON: captures time/paused/volume/rate, POSTs `{scene_id, time,
   preset}` to the bridge (the browser never sends filesystem paths), gets a
   session token and playlist path, attaches hls.js to the player's
   `<video>`, and seeks to the captured time. A heartbeat runs every 30s.
3. Seeking while ON needs nothing special: Jasna serves a static VOD
   playlist and the bridge renders/caches segments on demand.
4. Changing the preset pill mid-stream restarts Jasna on the new preset in
   place, no toggle-off needed. Presets come from the bridge's config plus
   any defined in the plugin's *Custom presets* setting (sent as flags).
5. Toggle OFF: destroys hls.js, ends the bridge session, and restores the
   original Stash source, seeking back.

## Labels

- `JASNA: OFF` / `JASNA: ON` - the two resting states.
- `JASNA: STARTING... / PREPARING... Ns` - Jasna is spinning up (cold) or
  rendering the first segment.
- `JASNA: BUSY` / `JASNA: TAKE OVER?` - another viewer owns Jasna; once that
  owner has stopped watching, one more click pre-empts them.
- `JASNA: RECOVERING... n` - a stream stall; the plugin is re-loading and the
  bridge is restarting Jasna behind it. Resolves to ON, or to LOST.
- `JASNA: LOST` - the bridge released the session (idle, pre-empted, or
  unrecoverable); playback dropped back to the Stash source.
- `JASNA: NO BRIDGE` - no bridge configured or detected, or the `/jasna`
  route answered with Stash's own page (the reverse proxy or tunnel has no
  `/jasna` location).

## Requirements

- [stash-jasna-bridge](https://github.com/org0ne/stash-jasna-bridge) running
  beside Jasna (Jasna 0.10.x with an NVIDIA GPU). Set it up first.
- Stash v0.31.x (tested on a v0.31.1 release and a develop build).
- Works on HTTP and HTTPS Stash alike: the bridge is served on the Stash
  origin, so there is no mixed-content problem.
- Browser: Chrome/Chromium and iOS Safari verified; Firefox works, less tested.

## Install

Easiest: add this depot as a plugin source in Stash
(Settings > Plugins > Sources):

```
https://org0ne.github.io/stash-plugins/stable/index.yml
```

then install **Jasna Switch** from the list. Open any scene: the toggle
appears top-right on the player.

With the bridge reverse-proxied under the Stash domain (the recommended
setup) there is nothing to configure - the plugin auto-detects it at
`<stash-origin>/jasna`.

**Custom presets.** The preset pill lists the presets configured in the
bridge's `bridge.toml`. To add your own without touching the bridge, fill in
Settings > Plugins > Jasna Switch > **Custom presets** with one entry per
preset, `name = flags`, separated by `;`:

```
av1 cq30 = --detection-model rfdetr-v6-large --secondary-restoration unet-4x --codec av1 --cq 30 # hq detection, av1; fast h264 = --codec h264 --cq 28
```

The flags are Jasna's own command-line flags (`jasna --help`); quote a value
with spaces (`--lut '/path/My LUT.cube'`). An unquoted `#` after the flags
starts a comment, shown in the picker instead of the word *custom*. Names
are 1-40 characters of
letters, digits, space, `.`, `_`, `+`, `-`, and may not repeat a bridge preset
name. Reload the page after changing the setting; entries the plugin cannot
parse are skipped with a `[Jasna]` line in the browser console. Custom presets
are shown as `name — comment` (or `name — custom`) in the pill and behave
like the others: picking
one mid-stream restarts Jasna on its flags. The bridge must allow them
(`jasna.custom_presets = true` alongside `manage_process = true` in
`bridge.toml`); it refuses flags that would take Jasna out from under it
(`--stream*`, `--input`/`--output`, license and post-export flags), and an
unpermitted or rejected preset shows as `JASNA: ERROR` with the reason in the
console.

You can also manage them from the player: the preset pill's dropdown ends
with **Add**, **Rename** and **Remove custom preset** entries. Add asks for a
`name = flags # comment` line; Rename and Remove act on the custom preset
currently selected in the pill (the bridge's own presets are edited in
`bridge.toml`). Changes are written back to the Custom presets setting through
Stash, so they persist and other browsers pick them up on their next page load.

**Overriding the bridge URL.** Set Settings > Plugins > Jasna Switch >
**Bridge URL** only to point elsewhere, e.g. an absolute
`http://host:8770` for a plain-HTTP Stash on a different origin. An absolute
URL must also be added to `ui.csp.connect-src` in `jasna-switch.yml`
(same-origin `/jasna` needs nothing), then reload plugins. **Bridge token**
is only for the bridge's `auth.mode = token`.

For development, symlink this folder into Stash's `plugins/` directory and
reload:

```sh
ln -s /path/to/stash-plugins/plugins/jasna-switch <stash config dir>/plugins/jasna-switch
```

## Headless end-to-end test

`tools/headless-test/repro.js` drives a real scene page in headless Chrome
over the DevTools protocol and can run a full ON -> OFF -> ON cycle while
reporting readyState, position, and any Stash-side source swap. Needs
`google-chrome` and Node 20+:

```sh
STASH_URL=http://localhost:9999 JASNA_URL=http://192.168.1.50:8765 \
  node --experimental-websocket tools/headless-test/repro.js 34503 20 play 600 cycle
```

`STASH_URL` is where scene pages are loaded from; `JASNA_URL` only filters
the network log output. Arguments: scene id, poll seconds, `fresh` |
`play` | `play-pause`, seek target in seconds, and `cycle` to run
ON -> OFF -> ON. Chrome is started with `--ignore-certificate-errors`, so a
self-signed proxy cert is fine.

## Notes

- One Jasna session at a time (a single GPU pipeline); the bridge coordinates
  ownership and takeover between viewers.
- hls.js 1.7.2 is bundled (no CDN, no `script-src` exception).
- On a stall, the plugin rides out hls.js's retries and shows RECOVERING
  while the bridge restarts Jasna on the same token; see the bridge README.
- The badge's corner is chosen from a small dropdown on the badge itself (the
  square glyph ◰◳◲◱ shows the current corner). Stash's plugin settings can't
  render a dropdown, so the picker lives on the player and the choice is
  remembered per browser. In a bottom corner the badge sits above the control
  bar and scrubber (their height is measured at runtime, so it stays clear on
  desktop, phone and in fullscreen).
