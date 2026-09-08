# Jasna Switch

A Stash UI plugin that adds a **JASNA ON/OFF** toggle to the scene player.
When ON, the player swaps to a live HLS stream produced by
Jasna running in `--stream` mode, at the same
playback position. When OFF, it swaps back to the normal Stash source,
again preserving position. Stash itself is unmodified; Jasna can run on a
different machine as long as it can read the same media paths.

Status: PoC complete. Design notes, test logs, the original plan, and the
roadmap are kept outside this repo.

## How it works

1. On a scene page the plugin looks up the scene's file path via Stash
   GraphQL.
2. Toggle ON: captures time/paused/volume/rate, POSTs the path to Jasna's
   `/open`, waits for `/stream.m3u8`, attaches hls.js directly to the
   player's `<video>` element, and seeks to the captured time.
3. Seeking while ON needs nothing special: Jasna serves a static VOD
   playlist and renders segments on demand.
4. Toggle OFF: destroys hls.js, restores the original Stash source on the
   raw element, seeks back, and POSTs `/stop`.

## Requirements

- Stash v0.31.x (tested on a v0.31.1 release and a develop build).
- Jasna 0.10.x with NVIDIA GPU, started as `jasna --stream --no-browser`
  plus whatever processing flags you want (settings are launch-time only;
  there is no runtime settings API).
- Jasna and Stash must see the media at the **same absolute path**.
- Browser: Chrome/Chromium verified. Firefox and Safari untested.
- If Stash is served over **HTTPS**, Jasna must also be reachable over
  HTTPS (mixed-content rule). See "HTTPS Stash" below. Plain-HTTP Stash
  can talk to Jasna's plain-HTTP port directly.

## Install

Easiest: add this depot as a plugin source in Stash
(Settings > Plugins > Sources):

```
https://org0ne.github.io/stash-plugins/stable/index.yml
```

then install **Jasna Switch** from the list. For development, symlink this
folder into Stash's `plugins/` directory instead:

```sh
ln -s /path/to/stash-plugins/plugins/jasna-switch <stash config dir>/plugins/jasna-switch
```

and Settings > Plugins > Reload plugins (or `mutation { reloadPlugins }`
via GraphQL).

Configure the Jasna endpoint in Settings > Plugins > Jasna Switch >
**Jasna URL**, e.g. `http://192.168.11.113:8765`. If left empty the
default in `jasna-switch.js` is used.

The Jasna origin must also be allowed by Stash's Content Security Policy.
`jasna-switch.yml` ships with the two origins used during development under
`ui.csp.connect-src`; add yours there if it differs, then reload plugins.

Open any scene: the toggle appears directly below the player (under the
scrubber strip).

## HTTPS Stash

Browsers block an HTTPS page from fetching `http://` resources, and Jasna's
stream server is plain HTTP. Two options:

- Preferred: put Jasna behind the same reverse proxy/domain as Stash
  (e.g. `https://stash.example/jasna/` -> `127.0.0.1:8765`).
- Quick hack used for the PoC: `tools/https-proxy/proxy.py`, a tiny
  TLS-terminating TCP proxy. Generate a cert first (not committed):

  ```sh
  cd tools/https-proxy
  openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout jasna-proxy.key -out jasna-proxy.crt \
    -subj "/CN=jasna-proxy" \
    -addext "subjectAltName=IP:192.168.11.113,IP:127.0.0.1,DNS:localhost"
  python3 proxy.py   # listens :8766, forwards to 127.0.0.1:8765
  ```

  Visit `https://<host>:8766/` once in the browser and accept the warning,
  then use that URL as the Jasna URL.

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

## Known limitations (PoC)

- One Jasna session at a time; no multi-user coordination.
- The browser sends raw filesystem paths to Jasna; no path mapping.
- No auth between browser and Jasna.
- hls.js is loaded from jsdelivr (needs internet and a `script-src`
  CSP entry, already in the manifest).
