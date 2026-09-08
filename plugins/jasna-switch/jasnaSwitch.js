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
// This is PoC code: hard-coded endpoint, single-file-per-scene
// assumption, no settings UI, no auth. See "Explicit Non-Goals" in
// stash-jasna-poc.md.

(function () {
  const PluginApi = window.PluginApi;

  // --- Configuration ---
  // The Jasna endpoint comes from the plugin setting "Jasna URL"
  // (Settings > Plugins > Jasna Switch), read via GraphQL at load time.
  // DEFAULT_JASNA_URL is the fallback when the setting is empty.
  //
  // If Stash is served over HTTPS, this must be an HTTPS origin too:
  // browsers block fetch()/HLS requests from an HTTPS page to plain HTTP
  // as mixed content (see tools/https-proxy/ for the PoC workaround).
  // Plain-HTTP Stash can point straight at Jasna's own port. Whatever the
  // origin is, it must also be listed under ui.csp.connect-src in
  // jasnaSwitch.yml.
  const DEFAULT_JASNA_URL = "https://192.168.11.113:8766";
  const PLUGIN_ID = "jasnaSwitch"; // derived by Stash from the .yml filename
  const JASNA_READY_TIMEOUT_MS = 5000;
  const JASNA_POLL_INTERVAL_MS = 250;
  let JASNA_URL = DEFAULT_JASNA_URL;

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
      const url = cfg && cfg[PLUGIN_ID] && cfg[PLUGIN_ID].jasnaUrl;
      if (typeof url === "string" && url.trim()) {
        JASNA_URL = url.trim().replace(/\/+$/, "");
      }
    } catch (err) {
      console.log("[Jasna] WARNING: could not read plugin settings, using default URL: " + err.message);
    }
    console.log("[Jasna] Endpoint: " + JASNA_URL);
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
  }

  function setButtonDisabled(disabled) {
    if (uiButtonEl) uiButtonEl.disabled = disabled;
  }

  function getVideoJsPlayer() {
    const iu = PluginApi.utils && PluginApi.utils.InteractiveUtils;
    return iu ? iu.getPlayer() : null;
  }

  // --- Phase 3: scene/file discovery via GraphQL ---

  async function fetchScene(sceneId) {
    const query = `
      query FindSceneJasna($id: ID!) {
        findScene(id: $id) {
          id
          files {
            path
            duration
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

    // New scene: reset everything, including any captured Stash source.
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

  function captureStashSourceIfNeeded(player) {
    if (state.stashSrc) return;
    state.stashSrc = player.currentSrc();
    state.stashType = player.currentType ? player.currentType() : "video/mp4";
  }

  // hls.js instance for Jasna playback - videojs's own source-handler
  // negotiation doesn't recognize the HLS type on this Stash build, so
  // hls.js is attached directly to the raw <video> element instead,
  // bypassing videojs's src() entirely while Jasna is active.
  let hlsInstance = null;

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
    video.addEventListener(
      "loadedmetadata",
      () => {
        seekWhenSeekable(video, time, Date.now() + 5000);
        video.volume = state.volume;
        video.playbackRate = state.playbackRate;
        if (wasPlaying) video.play();
      },
      { once: true }
    );
    video.src = state.stashSrc;
    video.load();
  }

  // Jasna always serves the manifest starting at segment 0, so the
  // player must seek to the target time itself; Jasna generates
  // whichever segment that seek lands on, on demand.
  function switchPlayerToJasna(player, time, wasPlaying) {
    const video = player.el().querySelector("video");
    const manifestUrl = `${JASNA_URL}/stream.m3u8`;
    destroyHls();
    setSourceSelectorGuard(player, true);

    const onReady = () => {
      video.currentTime = time;
      video.volume = state.volume;
      video.playbackRate = state.playbackRate;
      if (wasPlaying) video.play();
    };

    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls();
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
  async function enableJasna(player) {
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

    log(`Toggle ON at ${time.toFixed(3)}`);
    setButtonLabel("JASNA: PREPARING...");
    player.pause();

    const reqStart = performance.now();
    try {
      await requestJasnaOpen(state.path);
      const ready = await waitForJasnaReady();

      if (state.cancelSwitch) {
        log("Switch cancelled by user; restoring Stash playback");
        await requestJasnaStop();
        restoreStashSource(player, state.currentTime, state.playing);
        state.source = "stash";
        setButtonLabel("JASNA: OFF");
        return;
      }

      if (!ready) throw new Error("stream did not become ready in time");

      const elapsed = ((performance.now() - reqStart) / 1000).toFixed(2);
      log(`Stream ready after ${elapsed} sec`);

      switchPlayerToJasna(player, time, wasPlaying);
      state.source = "jasna";
      setButtonLabel("JASNA: ON");
      log("Playback started");
    } catch (err) {
      log(`ERROR: ${err.message}`);
      setButtonLabel("JASNA: ERROR");
      restoreStashSource(player, state.currentTime, state.playing);
      state.source = "stash";
      setTimeout(() => setButtonLabel("JASNA: OFF"), 2000);
    }
  }

  // Phase 5: Jasna -> normal
  function disableJasna(player) {
    if (state.source !== "jasna") return;

    const time = player.currentTime();
    const wasPlaying = !player.paused();
    log(`Toggle OFF at ${time.toFixed(3)}`);

    player.pause();
    requestJasnaStop();
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
      enableJasna(player);
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
  function ensureButtonMounted() {
    const videoPlayer = document.querySelector(".scene-player-container .VideoPlayer");
    if (!videoPlayer) return;

    let button = document.getElementById("jasna-toggle-button");
    if (!button) {
      button = document.createElement("button");
      button.id = "jasna-toggle-button";
      button.textContent = "JASNA: OFF";
      button.disabled = !state.path;
      button.style.display = "block";
      button.style.marginTop = "8px";
      button.addEventListener("click", () => onToggleClick(getVideoJsPlayer()));
      uiButtonEl = button;
    }

    if (button.previousElementSibling !== videoPlayer) {
      videoPlayer.insertAdjacentElement("afterend", button);
    }
    ensureSeekListener(getVideoJsPlayer());
  }

  new MutationObserver(ensureButtonMounted).observe(document.body, { childList: true, subtree: true });

  PluginApi.Event.addEventListener("stash:location", (e) => {
    handleLocationChange(e.detail.data.location.pathname);
  });

  loadPluginSettings().then(() => {
    handleLocationChange(window.location.pathname);
    ensureButtonMounted();
  });
})();
