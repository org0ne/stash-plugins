// Jasna Switch (PoC) - Stash UI plugin
//
// Implements Phases 2-7 of stash-jasna-poc.md, corrected against a live
// Jasna 0.10.0 instance:
//   - a JASNA ON/OFF button injected next to the scene player
//   - GraphQL scene/file lookup on scene page load
//   - Stash <-> Jasna source switching, preserving position/state
//   - fail-back to normal Stash playback on any Jasna error
//
// Verified against a real Jasna server (2026-09-08): /open takes only
// {path}, no start/seek parameter. /stream.m3u8 is a single static VOD
// playlist covering the whole file (#EXT-X-ENDLIST, sequence 0), and
// segments are generated lazily on request (near-instant if cached/
// adjacent, ~1-1.5s for a cold jump to an untouched region). This means
// positioning is done entirely by seeking the <video> element itself -
// there is no Jasna-side "restart at position" call, so Phase 6's
// planned seek-restart logic is unnecessary and has been removed.
//
// UI note: PluginApi.patch.after/instead on ScenePlayer (and even on
// unrelated components like MainNavBar.UtilityItems) crashes this Stash
// build (v0.31.1-175-gafce9689) with a React error #31 / missing
// IntlProvider - verified by bisection against a live instance on
// 2026-09-08. So the button is injected via plain DOM manipulation
// anchored to `.scene-player-container`, watched with a MutationObserver,
// instead of going through PluginApi.patch.
//
// Two ways to reach Jasna, chosen by the plugin settings:
//   bridge mode (Bridge URL set): talk to stash-jasna-bridge, which owns
//     the Jasna process, hands out a session token, times idle sessions
//     out and proxies the HLS stream. Heartbeats every 30s while ON; the
//     session is ended on OFF, scene change and pagehide (sendBeacon).
//   direct mode (Bridge URL empty): the original PoC path straight to
//     Jasna's own /open, /stop and /stream.m3u8. Kept until the bridge is
//     deployed everywhere; see bridge-plan.md in the notes.

(function () {
  const PluginApi = window.PluginApi;

  // --- Configuration ---
  // The Jasna endpoint comes from the plugin setting "Jasna URL"
  // (Settings > Plugins > Jasna Switch), read via GraphQL at load time.
  // DEFAULT_JASNA_URL is the fallback when the setting is empty.
  //
  // Direct mode only works from a plain-HTTP Stash (an HTTPS page cannot
  // fetch Jasna's plain-HTTP stream: mixed content). HTTPS setups use
  // bridge mode with the bridge under the Stash domain instead. Whatever
  // the origin is, it must also be listed under ui.csp.connect-src in
  // jasna-switch.yml.
  const DEFAULT_JASNA_URL = "http://192.168.11.113:8765";
  const PLUGIN_ID = "jasna-switch"; // derived by Stash from the .yml filename
  const JASNA_READY_TIMEOUT_MS = 5000;
  const JASNA_POLL_INTERVAL_MS = 250;
  const HEARTBEAT_MS = 30000;
  let JASNA_URL = DEFAULT_JASNA_URL;
  // Bridge mode: "Bridge URL" setting, absolute (http://host:8770) or relative
  // to the Stash origin ("/jasna" when reverse-proxied under the same domain).
  let BRIDGE_URL = "";
  let BRIDGE_TOKEN = "";

  async function loadPluginSettings() {
    try {
      const resp = await fetch("/graphql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ query: "{ configuration { plugins } }" }),
      });
      const json = await resp.json();
      const cfg = json.data && json.data.configuration && json.data.configuration.plugins;
      const mine = (cfg && cfg[PLUGIN_ID]) || {};
      if (typeof mine.jasnaUrl === "string" && mine.jasnaUrl.trim()) {
        JASNA_URL = mine.jasnaUrl.trim().replace(/\/+$/, "");
      }
      if (typeof mine.bridgeUrl === "string" && mine.bridgeUrl.trim()) {
        BRIDGE_URL = mine.bridgeUrl.trim().replace(/\/+$/, "");
        if (BRIDGE_URL.startsWith("/")) BRIDGE_URL = window.location.origin + BRIDGE_URL;
      }
      if (typeof mine.bridgeToken === "string") BRIDGE_TOKEN = mine.bridgeToken.trim();
    } catch (err) {
      console.log("[Jasna] WARNING: could not read plugin settings, using default URL: " + err.message);
    }
    console.log("[Jasna] Endpoint: " + (BRIDGE_URL ? "bridge " + BRIDGE_URL : "direct " + JASNA_URL));
  }

  const PRESET_STORAGE_KEY = "jasna-switch:preset";

  function readSavedPreset() {
    try { return localStorage.getItem(PRESET_STORAGE_KEY); } catch (e) { return null; }
  }
  function savePreset(name) {
    try { localStorage.setItem(PRESET_STORAGE_KEY, name); } catch (e) { /* private mode */ }
  }

  // Populate the preset picker and warmth hint from the bridge. Only bridge
  // mode has presets; direct mode leaves state.presets empty (no picker).
  async function loadBridgePresets() {
    if (!bridgeMode()) return;
    try {
      const resp = await fetch(`${BRIDGE_URL}/presets`, { credentials: "include", headers: bridgeHeaders(false) });
      if (!resp.ok) { log(`presets HTTP ${resp.status}`); return; }
      checkBridgeResponseIsJson(resp, "/presets");
      const data = await resp.json();
      state.presets = Array.isArray(data.presets) ? data.presets : [];
      state.warm = !!data.warm;
      const names = state.presets.map((p) => p.name);
      const saved = readSavedPreset();
      state.preset = saved && names.includes(saved) ? saved : (data.default || null);
      log(`Presets: ${names.join(", ") || "(none)"}; default ${data.default}; warm ${state.warm}`);
    } catch (err) {
      log("could not load presets: " + err.message);
    }
    ensureButtonMounted();
    renderPresetSelect();
  }

  function bridgeMode() {
    return !!BRIDGE_URL;
  }

  // --- Controller state (Phase 2) ---
  // The browser player is the source of truth for currentTime; this
  // object only tracks what's needed to switch sources and restore state.
  const state = {
    sceneId: null,
    path: null,
    source: "stash", // "stash" | "jasna" | "switching"
    currentTime: 0,
    playing: false,
    volume: 1.0,
    playbackRate: 1.0,
    stashSrc: null,
    stashType: null,
    cancelSwitch: false,
    // bridge mode
    sessionToken: null,
    heartbeatTimer: null,
    preset: null,        // chosen preset name (bridge mode); null = bridge default
    presets: [],         // [{name, description}] from GET /presets
    warm: false,         // bridge reports Jasna warm (cold-start hint)
    canTakeover: false,  // last busy response said the idle owner can be taken over
  };

  function log(msg) {
    console.log("[Jasna] " + msg);
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  // --- UI wiring: plain DOM button, not a React component (see UI note
  // above - PluginApi.patch is broken on this Stash build). ---
  let uiButtonEl = null;

  function setButtonLabel(text) {
    if (uiButtonEl) uiButtonEl.textContent = text;
    renderPresetSelect();
  }

  function setButtonDisabled(disabled) {
    if (uiButtonEl) uiButtonEl.disabled = disabled;
  }

  function getVideoJsPlayer() {
    const iu = PluginApi.utils && PluginApi.utils.InteractiveUtils;
    return iu ? iu.getPlayer() : null;
  }

  // --- Phase 3: scene/file discovery via GraphQL ---

  // performers is not used here; it is requested because another plugin's
  // fetch hook (CleanCards' processScene) assumes every findScene response
  // has it and throws otherwise (seen 2026-09-08).
  async function fetchScene(sceneId) {
    const query = `
      query FindSceneJasna($id: ID!) {
        findScene(id: $id) {
          id
          files {
            path
            duration
          }
          performers {
            id
          }
        }
      }
    `;
    const resp = await fetch("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ query, variables: { id: sceneId } }),
    });
    if (!resp.ok) {
      throw new Error(`GraphQL request failed: HTTP ${resp.status}`);
    }
    const json = await resp.json();
    if (json.errors && json.errors.length) {
      throw new Error(json.errors[0].message);
    }
    return json.data && json.data.findScene;
  }

  function extractSceneId(pathname) {
    const m = pathname.match(/^\/scenes\/(\d+)/);
    return m ? m[1] : null;
  }

  async function handleLocationChange(pathname) {
    const sceneId = extractSceneId(pathname);

    if (!sceneId) {
      state.sceneId = null;
      state.path = null;
      setButtonDisabled(true);
      return;
    }

    if (sceneId === state.sceneId) return;

    // Leaving a scene while Jasna is active: free the GPU stream.
    if (state.source === "jasna") requestJasnaStop();
    if (state.sessionToken) endBridgeSession("scene change");

    // New scene: reset everything, including any captured Stash source.
    endJasnaSuppression();
    state.sceneId = sceneId;
    state.source = "stash";
    state.stashSrc = null;
    state.stashType = null;
    setButtonLabel("JASNA: OFF");
    log(`Scene ${sceneId}`);

    try {
      const scene = await fetchScene(sceneId);
      const file = scene && scene.files && scene.files[0];
      if (!file) throw new Error("no video file found for scene");
      if (scene.files.length > 1) {
        log(`WARNING: scene has ${scene.files.length} files, using the first`);
      }
      state.path = file.path;
      log(`File: ${file.path}`);
      setButtonDisabled(false);
    } catch (err) {
      log(`ERROR: scene lookup failed: ${err.message}`);
      state.path = null;
      setButtonDisabled(true);
    }
  }

  // --- Bridge client (bridge mode) ---

  function bridgeHeaders(json) {
    const h = {};
    if (json) h["Content-Type"] = "application/json";
    if (BRIDGE_TOKEN) h["Authorization"] = "Bearer " + BRIDGE_TOKEN;
    return h;
  }

  class BridgeBusyError extends Error {
    constructor(info) {
      super("Jasna is busy");
      this.info = info;
    }
  }

  // The Bridge URL answered, but not with JSON. That is almost always
  // Stash's own SPA page: a reverse proxy or tunnel in front of Stash with
  // no /jasna location (seen 2026-09-09 through a Cloudflare Tunnel that
  // pointed straight at Stash, bypassing the proxy that has the route).
  class BridgeNotRoutedError extends Error {}

  function checkBridgeResponseIsJson(resp, what) {
    const ct = (resp.headers.get("content-type") || "").split(";")[0].trim();
    if (/json/i.test(ct)) return;
    throw new BridgeNotRoutedError(
      `bridge ${what} answered HTTP ${resp.status} ${ct || "(no content-type)"} instead of JSON - ` +
      `${BRIDGE_URL} is not routed to the bridge (proxy/tunnel missing the /jasna location?)`
    );
  }

  async function bridgeCreateSession(sceneId, time, opts) {
    opts = opts || {};
    const payload = { scene_id: sceneId, time };
    if (state.preset) payload.preset = state.preset;
    if (opts.force) payload.force = true;
    const resp = await fetch(`${BRIDGE_URL}/session`, {
      method: "POST",
      headers: bridgeHeaders(true),
      credentials: "include",
      body: JSON.stringify(payload),
    });
    if (resp.ok) checkBridgeResponseIsJson(resp, "/session");
    let body = null;
    try {
      body = await resp.json();
    } catch (err) {
      // non-JSON error page from a proxy; fall through to the status check
    }
    if (resp.status === 409) throw new BridgeBusyError(body || {});
    if (!resp.ok) {
      throw new Error(`bridge /session failed: HTTP ${resp.status}` + (body && body.error ? ` (${body.error})` : ""));
    }
    return body;
  }

  // sendBeacon is POST-only and header-less, so the bridge accepts
  // POST /session/<token>/end as an alias for DELETE. The token in the
  // path is the credential, so no auth header is needed on these routes.
  function endBridgeSession(why) {
    const token = state.sessionToken;
    if (!token) return;
    state.sessionToken = null;
    stopHeartbeat();
    const url = `${BRIDGE_URL}/session/${token}/end`;
    log(`Ending bridge session (${why})`);
    let sent = false;
    if (why === "pagehide" && navigator.sendBeacon) {
      sent = navigator.sendBeacon(url);
    }
    if (!sent) {
      fetch(url, { method: "POST", keepalive: true, credentials: "include" }).catch((err) => {
        log(`WARNING: ending session failed: ${err.message}`);
      });
    }
  }

  function stopHeartbeat() {
    if (state.heartbeatTimer) {
      clearInterval(state.heartbeatTimer);
      state.heartbeatTimer = null;
    }
  }

  function startHeartbeat(player) {
    stopHeartbeat();
    state.heartbeatTimer = setInterval(async () => {
      const token = state.sessionToken;
      if (!token || state.source !== "jasna") return;
      try {
        const resp = await fetch(`${BRIDGE_URL}/session/${token}/heartbeat`, {
          method: "POST",
          headers: bridgeHeaders(true),
          credentials: "include",
          body: JSON.stringify({ time: player.currentTime(), paused: player.paused() }),
        });
        if (resp.status === 410 || resp.status === 404) {
          // The bridge released us (idle, pre-empted or restarted).
          log("Bridge session lost; restoring Stash playback");
          state.sessionToken = null;
          stopHeartbeat();
          const time = player.currentTime();
          const wasPlaying = !player.paused();
          player.pause();
          restoreStashSource(player, time, wasPlaying);
          state.source = "stash";
          setButtonLabel("JASNA: LOST");
          setTimeout(() => setButtonLabel("JASNA: OFF"), 3000);
        } else if (!resp.ok) {
          log(`WARNING: heartbeat HTTP ${resp.status}`);
        }
      } catch (err) {
        log(`WARNING: heartbeat failed: ${err.message}`);
      }
    }, HEARTBEAT_MS);
  }

  window.addEventListener("pagehide", () => {
    if (state.sessionToken) endBridgeSession("pagehide");
    else if (state.source === "jasna") requestJasnaStop();
  });

  // --- Jasna control (Phases 4-6) ---

  async function requestJasnaOpen(path) {
    log(`Requesting stream: ${path}`);
    const resp = await fetch(`${JASNA_URL}/open`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
    if (!resp.ok) {
      throw new Error(`Jasna /open failed: HTTP ${resp.status}`);
    }
  }

  async function requestJasnaStop() {
    try {
      await fetch(`${JASNA_URL}/stop`, { method: "POST" });
    } catch (err) {
      log(`WARNING: /stop failed: ${err.message}`);
    }
  }

  async function waitForJasnaReady() {
    const deadline = Date.now() + JASNA_READY_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (state.cancelSwitch) return false;
      try {
        const resp = await fetch(`${JASNA_URL}/stream.m3u8`, { cache: "no-store" });
        if (resp.ok) return true;
      } catch (err) {
        // transient network errors while Jasna is starting up are expected
      }
      await sleep(JASNA_POLL_INTERVAL_MS);
    }
    return false;
  }

  // A Stash build with its own Jasna streamer (the jasna-fork) lists
  // /scene/{id}/stream.jasna-{preset} routes in the source menu. Restoring
  // to one of those makes Stash spawn a second Jasna that fights this
  // plugin's over port 8765 (seen 2026-09-09), so never capture it: fall
  // back to the first non-Jasna source the player knows about.
  function isJasnaRouteSrc(src) {
    return typeof src === "string" && /\/stream\.jasna-/.test(src);
  }

  function captureStashSourceIfNeeded(player) {
    if (state.stashSrc) return;
    let src = player.currentSrc();
    let type = player.currentType ? player.currentType() : "video/mp4";
    if (isJasnaRouteSrc(src)) {
      const selector = typeof player.sourceSelector === "function" ? player.sourceSelector() : null;
      const known = (selector && Array.isArray(selector.sources) ? selector.sources : [])
        .concat(typeof player.currentSources === "function" ? player.currentSources() : []);
      const alt = known.find((s) => s && s.src && !isJasnaRouteSrc(s.src));
      if (alt) {
        log(`Current source is a Jasna route; will restore to ${alt.src.split("?")[0]} instead`);
        src = alt.src;
        type = alt.type || "video/mp4";
      } else {
        log("Current source is a Jasna route and no alternative is known; restoring to it anyway");
      }
    }
    state.stashSrc = src;
    state.stashType = type;
  }

  // hls.js instance for Jasna playback - videojs's own source-handler
  // negotiation doesn't recognize the HLS type on this Stash build, so
  // hls.js is attached directly to the raw <video> element instead,
  // bypassing videojs's src() entirely while Jasna is active.
  let hlsInstance = null;

  // video.js shows its error overlay (the big "X") if the raw <video> element
  // fires an 'error' during a source hand-off - even though hls.js (PC/MSE)
  // or native HLS (iOS Safari) recovers a moment later and frames play. The
  // sourceSelector guard stops the auto-advance; this stops the visible X and
  // any wedged error state that could break the next toggle. Clearing on the
  // microtask after the event ensures it runs AFTER video.js sets the error.
  function clearPlayerError(player) {
    try {
      if (player.error && player.error()) player.error(null);
    } catch (e) {
      /* player torn down */
    }
  }

  // On iOS Safari the switch uses native HLS (no MSE): assigning a new
  // video.src to a live element fires a real 'error', so video.js shows its
  // overlay for a frame before we can clear it. Clearing after the fact is
  // too late to stop the flash, so we also HIDE the overlay outright for the
  // bounded hand-off window. The desktop hls.js path fires no such error, so
  // there the class is a harmless no-op.
  let errorHideStyleInjected = false;
  function ensureErrorHideStyle() {
    if (errorHideStyleInjected) return;
    const st = document.createElement("style");
    st.textContent = ".jasna-suppress-error .vjs-error-display{display:none!important}";
    (document.head || document.documentElement).appendChild(st);
    errorHideStyleInjected = true;
  }

  // Stash's sourceSelector also raises MEDIA_ERR_SRC_NOT_SUPPORTED from its
  // own loadedmetadata handler when the element reports 0x0 and video.js
  // still believes the src is the (non-HLS) Stash URL - which is exactly the
  // state Safari's native HLS leaves us in, and it can fire after the first
  // frames. So while the plugin owns the element the video.js error state is
  // meaningless: clear it on the microtask after every event that can set it,
  // and keep the overlay hidden until the caller ends suppression.
  const SUPPRESS_EVENTS = ["error", "loadedmetadata", "loadeddata", "canplay", "playing"];
  function suppressSwitchError(player, video) {
    ensureErrorHideStyle();
    const root = player.el();
    root.classList.add("jasna-suppress-error");
    let active = true;
    const onEvt = () => {
      if (active) setTimeout(() => { if (active) clearPlayerError(player); }, 0);
    };
    for (const n of SUPPRESS_EVENTS) video.addEventListener(n, onEvt, true);
    return () => {
      active = false;
      for (const n of SUPPRESS_EVENTS) video.removeEventListener(n, onEvt, true);
      clearPlayerError(player);
      root.classList.remove("jasna-suppress-error");
    };
  }

  // Suppression that lasts for the whole Jasna session (ended on restore).
  let jasnaSuppressStop = null;
  function endJasnaSuppression() {
    if (jasnaSuppressStop) {
      try { jasnaSuppressStop(); } catch (e) { /* element already gone */ }
      jasnaSuppressStop = null;
    }
  }

  function destroyHls() {
    if (hlsInstance) {
      hlsInstance.destroy();
      hlsInstance = null;
    }
  }

  // Right after reload, the browser may only know a provisional (short)
  // duration/seekable range for a non-faststart progressive MP4, so a
  // seek attempted immediately on loadedmetadata can be silently ignored.
  // Retry until the target time is actually within the seekable range.
  function seekWhenSeekable(video, time, deadline) {
    const seekable = video.seekable;
    let covered = false;
    for (let i = 0; i < seekable.length; i++) {
      if (time >= seekable.start(i) && time <= seekable.end(i)) {
        covered = true;
        break;
      }
    }
    if (covered || Date.now() >= deadline) {
      video.currentTime = time;
      return;
    }
    setTimeout(() => seekWhenSeekable(video, time, deadline), 200);
  }

  // Stash's "sourceSelector" video.js plugin (see ScenePlayer chunk) reacts
  // to any MEDIA_ERR_SRC_NOT_SUPPORTED / MEDIA_ERR_DECODE on the player by
  // auto-advancing to the next sceneStreams entry via player.src(next) +
  // player.load() - unless the user picked a source manually. Its own
  // loadedmetadata handler also raises SRC_NOT_SUPPORTED if the element
  // reports 0x0 dimensions for a non-HLS/DASH src. While hls.js owns the raw
  // <video>, that fallback would rip the element away mid-stream and leave
  // the player stalled at readyState 0 (the same src()+load() race noted in
  // restoreStashSource). Flagging the source as manually selected disables
  // only the auto-advance; everything else about the menu keeps working.
  let savedManuallySelected = null;

  function setSourceSelectorGuard(player, active) {
    const ss = player.sourceSelector ? player.sourceSelector() : null;
    if (!ss) return;
    if (active) {
      if (savedManuallySelected === null) savedManuallySelected = ss.manuallySelected;
      ss.manuallySelected = true;
    } else if (savedManuallySelected !== null) {
      ss.manuallySelected = savedManuallySelected;
      savedManuallySelected = null;
    }
  }

  function restoreStashSource(player, time, wasPlaying) {
    log("Restoring Stash source");
    destroyHls();
    setSourceSelectorGuard(player, false);
    // Bypass player.src()/player.load(): verified 2026-09-08 that calling
    // both back-to-back races with videojs's own async src handling and
    // stalls at readyState 0. Setting the raw element directly, as already
    // done for the Jasna path, is what actually works reliably.
    const video = player.el().querySelector("video");
    endJasnaSuppression();
    const stopSuppress = suppressSwitchError(player, video);
    video.addEventListener(
      "loadedmetadata",
      () => {
        clearPlayerError(player);
        seekWhenSeekable(video, time, Date.now() + 5000);
        video.volume = state.volume;
        video.playbackRate = state.playbackRate;
        if (wasPlaying) video.play();
        setTimeout(stopSuppress, 1500);
      },
      { once: true }
    );
    video.src = state.stashSrc;
    video.load();
  }

  // Jasna always serves the manifest starting at segment 0, so the
  // player must seek to the target time itself; Jasna generates
  // whichever segment that seek lands on, on demand.
  function switchPlayerToJasna(player, time, wasPlaying, manifestUrl) {
    const video = player.el().querySelector("video");
    destroyHls();
    setSourceSelectorGuard(player, true);
    endJasnaSuppression();
    jasnaSuppressStop = suppressSwitchError(player, video); // until restoreStashSource

    const onReady = () => {
      clearPlayerError(player);
      video.currentTime = time;
      video.volume = state.volume;
      video.playbackRate = state.playbackRate;
      if (wasPlaying) video.play();
    };

    if (window.Hls && window.Hls.isSupported()) {
      // startPosition makes hls.js load the fragment covering the target
      // time first. Without it the first request is always seg_00000.ts,
      // which Jasna then renders for nothing (~2.5s measured 2026-09-08)
      // before the seek pulls the real segment.
      const hls = new window.Hls({ startPosition: time });
      hls.on(window.Hls.Events.MANIFEST_PARSED, onReady);
      hls.loadSource(manifestUrl);
      hls.attachMedia(video);
      hlsInstance = hls;
    } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = manifestUrl;
      video.addEventListener("loadedmetadata", onReady, { once: true });
    } else {
      throw new Error("HLS is not supported in this browser");
    }
  }

  // Phase 4: normal -> Jasna
  async function enableJasna(player, opts) {
    opts = opts || {};
    if (state.source !== "stash") return;
    if (!state.path) {
      log("Scene file unavailable; cannot enable Jasna");
      return;
    }

    captureStashSourceIfNeeded(player);

    const time = player.currentTime();
    const wasPlaying = !player.paused();
    state.currentTime = time;
    state.playing = wasPlaying;
    state.volume = player.volume();
    state.playbackRate = player.playbackRate();
    state.source = "switching";
    state.cancelSwitch = false;
    state.canTakeover = false;

    log(`Toggle ON at ${time.toFixed(3)}` + (bridgeMode() ? " (bridge)" : "") + (opts.force ? " (takeover)" : ""));
    player.pause();

    // Cold-start hint: while Jasna is not warm the first frame can take
    // several seconds (pipeline spin-up), so say so instead of a bare wait.
    const cold = bridgeMode() && !state.warm;
    const reqStart = performance.now();
    setButtonLabel(cold ? "JASNA: STARTING..." : "JASNA: PREPARING...");
    const preparingTimer = setInterval(() => {
      const secs = Math.floor((performance.now() - reqStart) / 1000);
      if (secs >= 2) setButtonLabel(`JASNA: ${cold ? "STARTING" : "PREPARING"}... ${secs}s`);
    }, 500);

    const cancelled = async () => {
      log("Switch cancelled by user; restoring Stash playback");
      if (bridgeMode()) endBridgeSession("cancelled");
      else await requestJasnaStop();
      restoreStashSource(player, state.currentTime, state.playing);
      state.source = "stash";
      setButtonLabel("JASNA: OFF");
    };

    try {
      let manifestUrl;
      if (bridgeMode()) {
        const session = await bridgeCreateSession(state.sceneId, time, { force: opts.force });
        state.sessionToken = session.token;
        state.warm = true; // a live stream is warm for the next toggle
        manifestUrl = `${BRIDGE_URL}${session.playlist_path}`;
        log(`Bridge session ready in ${session.ready_seconds}s` +
            (session.reused ? " (stream reused)" : session.cold ? " (cold start)" : session.switched ? " (file switch)" : " (warm)"));
        if (state.cancelSwitch) return await cancelled();
      } else {
        await requestJasnaOpen(state.path);
        const ready = await waitForJasnaReady();
        if (state.cancelSwitch) return await cancelled();
        if (!ready) throw new Error("stream did not become ready in time");
        manifestUrl = `${JASNA_URL}/stream.m3u8`;
      }

      const elapsed = ((performance.now() - reqStart) / 1000).toFixed(2);
      log(`Stream ready after ${elapsed} sec`);

      switchPlayerToJasna(player, time, wasPlaying, manifestUrl);
      state.source = "jasna";
      if (bridgeMode()) startHeartbeat(player);
      setButtonLabel("JASNA: ON");
      log("Playback started");
    } catch (err) {
      if (err instanceof BridgeBusyError) {
        const who = err.info.scene_id ? ` (scene ${err.info.scene_id}, idle ${Math.round(err.info.idle_seconds || 0)}s)` : "";
        if (state.sessionToken) endBridgeSession("error");
        restoreStashSource(player, state.currentTime, state.playing);
        state.source = "stash";
        if (err.info.takeover_available) {
          // The owner is idle; let the next click pre-empt it. Bound the
          // offer so a stale "TAKE OVER?" label can't sit there forever.
          log(`Jasna is busy${who}; owner is idle, take-over available`);
          state.canTakeover = true;
          setButtonLabel("JASNA: TAKE OVER?");
          setTimeout(() => {
            if (state.source === "stash" && state.canTakeover) {
              state.canTakeover = false;
              setButtonLabel("JASNA: OFF");
            }
          }, 8000);
        } else {
          log(`Jasna is busy${who}`);
          setButtonLabel("JASNA: BUSY");
          setTimeout(() => setButtonLabel("JASNA: OFF"), 3000);
        }
        return;
      }
      log(`ERROR: ${err.message}`);
      setButtonLabel(err instanceof BridgeNotRoutedError ? "JASNA: NO BRIDGE" : "JASNA: ERROR");
      if (state.sessionToken) endBridgeSession("error");
      restoreStashSource(player, state.currentTime, state.playing);
      state.source = "stash";
      setTimeout(() => setButtonLabel("JASNA: OFF"), 3000);
    } finally {
      clearInterval(preparingTimer);
    }
  }

  // Phase 5: Jasna -> normal
  function disableJasna(player) {
    if (state.source !== "jasna") return;

    const time = player.currentTime();
    const wasPlaying = !player.paused();
    log(`Toggle OFF at ${time.toFixed(3)}`);

    player.pause();
    if (bridgeMode()) endBridgeSession("toggle off");
    else requestJasnaStop();
    restoreStashSource(player, time, wasPlaying);
    state.source = "stash";
    setButtonLabel("JASNA: OFF");
  }

  // Phase 6: seeking while Jasna is enabled needs no plugin action - the
  // static VOD manifest lets the player's native HLS seek request whichever
  // segment covers the new position, and Jasna generates it on demand. This
  // listener exists purely to log the observed behavior for the PoC.
  function onSeeked(player) {
    if (state.source !== "jasna") return;
    log(`Seek observed at ${player.currentTime().toFixed(3)} (native HLS reposition, no restart needed)`);
  }

  function onToggleClick(player) {
    if (!player) {
      log("No video player available");
      return;
    }
    if (state.source === "stash") {
      const force = state.canTakeover;
      state.canTakeover = false;
      enableJasna(player, { force });
    } else if (state.source === "jasna") {
      disableJasna(player);
    } else if (state.source === "switching") {
      // Error handling requirement: cancel a pending switch immediately.
      log("Cancel requested while Jasna was preparing");
      state.cancelSwitch = true;
    }
  }

  // Attach the 'seeked' listener to a given player at most once.
  const seekListenerAttached = new WeakSet();
  function ensureSeekListener(player) {
    if (!player || seekListenerAttached.has(player)) return;
    player.on("seeked", () => onSeeked(player));
    seekListenerAttached.add(player);
  }

  // --- Plain-DOM button (Phase 2) ---
  // Anchored to the player observed on a live scene page:
  // .scene-player-container > .VideoPlayer > .video-wrapper > video-js > video
  // The button is placed as the immediate next sibling of .VideoPlayer, not
  // simply appended to the container: the container exists before React
  // renders .VideoPlayer into it, and a node appended too early ends up
  // ABOVE the player, hidden under Stash's fixed top navbar (seen
  // 2026-09-08). The observer re-checks ordering so a React re-render
  // that replaces .VideoPlayer can't leave the button stranded.
  // Populate / update the preset <select>. Shown only in bridge mode with
  // more than one preset; hidden otherwise (direct mode, or a single preset).
  function renderPresetSelect() {
    const sel = document.getElementById("jasna-preset-select");
    if (!sel) return;
    const show = bridgeMode() && state.presets.length > 1;
    sel.hidden = !show;
    if (!show) return;
    const want = state.presets.map((p) => p.name).join("|");
    if (sel.dataset.built !== want) {
      sel.innerHTML = "";
      for (const p of state.presets) {
        const opt = document.createElement("option");
        opt.value = p.name;
        opt.textContent = p.description ? `${p.name} — ${p.description}` : p.name;
        sel.appendChild(opt);
      }
      sel.dataset.built = want;
    }
    if (state.preset) sel.value = state.preset;
    // Cannot change preset mid-stream: a switch restarts Jasna.
    sel.disabled = state.source !== "stash";
  }

  function ensureButtonMounted() {
    const videoPlayer = document.querySelector(".scene-player-container .VideoPlayer");
    if (!videoPlayer) return;

    let controls = document.getElementById("jasna-controls");
    if (!controls) {
      controls = document.createElement("div");
      controls.id = "jasna-controls";
      controls.style.cssText = "display:flex;align-items:center;gap:8px;margin-top:8px;flex-wrap:wrap";

      const button = document.createElement("button");
      button.id = "jasna-toggle-button";
      button.textContent = "JASNA: OFF";
      button.disabled = !state.path;
      button.addEventListener("click", () => onToggleClick(getVideoJsPlayer()));
      uiButtonEl = button;

      const select = document.createElement("select");
      select.id = "jasna-preset-select";
      select.hidden = true;
      select.title = "Jasna preset (applied on the next toggle ON)";
      select.style.cssText = "padding:2px 4px";
      select.addEventListener("change", () => {
        state.preset = select.value;
        savePreset(select.value);
        log(`Preset -> ${select.value}`);
      });

      controls.appendChild(button);
      controls.appendChild(select);
    }

    if (controls.previousElementSibling !== videoPlayer) {
      videoPlayer.insertAdjacentElement("afterend", controls);
    }
    renderPresetSelect(); // repopulate once presets arrive (fetch resolves after first mount)
    ensureSeekListener(getVideoJsPlayer());
  }

  new MutationObserver(ensureButtonMounted).observe(document.body, { childList: true, subtree: true });

  PluginApi.Event.addEventListener("stash:location", (e) => {
    handleLocationChange(e.detail.data.location.pathname);
  });

  loadPluginSettings().then(() => {
    handleLocationChange(window.location.pathname);
    ensureButtonMounted();
    loadBridgePresets();
  });
})();
