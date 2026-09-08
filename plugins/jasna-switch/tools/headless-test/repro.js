// Headless-Chrome end-to-end test for the Jasna toggle on a live Stash scene
// page, driven over the Chrome DevTools Protocol (no puppeteer/playwright
// needed - only google-chrome and Node >= 20 with --experimental-websocket).
//
// usage:
//   node --experimental-websocket repro.js <sceneId> [pollSeconds] [fresh|play|play-pause] [seekTo] [cycle]
//
//   fresh       click the toggle on an untouched page (player at readyState 0)
//   play        play() first, wait for frames, then click
//   play-pause  same, but pause() right before clicking
//   seekTo      seconds to seek to before clicking (play modes only)
//   cycle       after ON succeeds: click OFF, verify position restore, click ON again
//
// Env: STASH_URL (default http://localhost:9999), JASNA_URL (host/origin
// of the Jasna server, used only to filter network log lines).
// Prereqs: Jasna running (`jasna --stream`), the plugin installed in Stash
// with its Jasna URL setting configured. POST <jasna>/stop first for a
// cold-start measurement.
// Instruments player.error()/src()/load() with stack traces and logs raw
// <video> events so a Stash-side source swap is visible if it happens.
const { spawn } = require("child_process");
const http = require("http");

const sceneId = process.argv[2] || "34503";
const pollSeconds = Number(process.argv[3] || 15);
const preMode = process.argv[4] || "fresh"; // fresh | play | play-pause
const seekTo = Number(process.argv[5] || 0);
// Stash base URL (scene pages are loaded from here) and the Jasna URL used
// only for filtering network log noise; the plugin itself reads its Jasna
// URL from the Stash plugin setting.
const STASH_URL = (process.env.STASH_URL || "http://localhost:9999").replace(/\/+$/, "");
const JASNA_HOST_PATTERN = new RegExp((process.env.JASNA_URL || "192.168.11.113").replace(/^https?:\/\//, "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
const PORT = 9333;
const profile = __dirname + "/chrome-profile-" + process.pid;

const chrome = spawn("google-chrome", [
  "--headless=new", "--no-sandbox", "--disable-gpu",
  "--ignore-certificate-errors", "--autoplay-policy=no-user-gesture-required",
  "--remote-debugging-port=" + PORT, "--user-data-dir=" + profile,
  "--window-size=1600,1000", "about:blank",
], { stdio: ["ignore", "ignore", "pipe"] });
chrome.stderr.on("data", (d) => { const s = d.toString(); if (/error|fatal/i.test(s) && !/dbus|gpu|vaapi|bluez/i.test(s)) process.stderr.write("[chrome] " + s); });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
function getJson(path) {
  return new Promise((resolve, reject) => {
    http.get({ host: "127.0.0.1", port: PORT, path }, (res) => {
      let b = ""; res.on("data", (c) => (b += c)); res.on("end", () => { try { resolve(JSON.parse(b)); } catch (e) { reject(e); } });
    }).on("error", reject);
  });
}

async function main() {
  let targets;
  for (let i = 0; i < 50; i++) { try { targets = await getJson("/json/list"); break; } catch { await sleep(200); } }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((r) => (ws.onopen = r));
  let id = 0; const pending = new Map(); const reqUrls = new Map();
  const t0 = Date.now(); const ts = () => ((Date.now() - t0) / 1000).toFixed(2).padStart(6);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); return; }
    if (m.method === "Runtime.consoleAPICalled") {
      const txt = m.params.args.map((a) => a.value !== undefined ? String(a.value) : (a.description || a.type)).join(" ");
      if (/jasna|probe|source|unsupported|playlist|hls|VIDEOJS|error/i.test(txt)) console.log(`${ts()} console.${m.params.type}: ${txt.slice(0, 600)}`);
    } else if (m.method === "Runtime.exceptionThrown") {
      console.log(`${ts()} EXCEPTION: ${m.params.exceptionDetails.text} ${(m.params.exceptionDetails.exception || {}).description || ""}`.slice(0, 500));
    } else if (m.method === "Network.requestWillBeSent") {
      reqUrls.set(m.params.requestId, m.params.request.url);
      const u = m.params.request.url; if (JASNA_HOST_PATTERN.test(u)) console.log(`${ts()} net.request ${u.replace(/\?.*/, "")}`);
    } else if (m.method === "Network.loadingFailed") {
      const u = reqUrls.get(m.params.requestId) || "?"; console.log(`${ts()} net.FAILED ${m.params.errorText} ${u.replace(/apikey=[^&]+/, "apikey=..").slice(0, 160)}`);
    } else if (m.method === "Log.entryAdded") {
      const e = m.params.entry; if (/error|warn/i.test(e.level) && !/favicon|Autoplay/.test(e.text)) console.log(`${ts()} log.${e.level}: ${e.text.slice(0, 300)}`);
    }
  };
  const send = (method, params = {}) => new Promise((resolve) => { const i = ++id; pending.set(i, resolve); ws.send(JSON.stringify({ id: i, method, params })); });
  const evaluate = async (expression, awaitPromise = false) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error("evaluate failed: " + JSON.stringify(r.result.exceptionDetails).slice(0, 400));
    return r.result.result.value;
  };

  for (const d of ["Runtime", "Log", "Network", "Page"]) await send(d + ".enable");
  console.log(`${ts()} navigating to scene ${sceneId}`);
  await send("Page.navigate", { url: `${STASH_URL}/scenes/${sceneId}` });

  // wait for plugin button to be mounted and enabled
  let ready = false;
  for (let i = 0; i < 100; i++) {
    ready = await evaluate(`(function(){var b=document.getElementById('jasna-toggle-button');return !!(b&&!b.disabled)})()`);
    if (ready) break; await sleep(250);
  }
  if (!ready) { console.log("button never became ready"); await shutdown(); return; }
  console.log(`${ts()} toggle button ready`);
  await sleep(1500); // let the player settle

  // instrument player.error / player.src / native video events
  await evaluate(`(function(){
    var p = window.PluginApi.utils.InteractiveUtils.getPlayer();
    window.__p = p;
    var oe = p.error; p.error = function(e){ if (e !== undefined) console.log('[probe] player.error(' + JSON.stringify(e) + ') videoWidth=' + p.videoWidth() + ' currentSrc=' + String(p.currentSrc()).replace(/apikey=[^&]+/, 'apikey=..') + '\\n' + new Error().stack.split('\\n').slice(2,7).join('\\n')); return oe.apply(this, arguments); };
    var os = p.src; p.src = function(s){ if (s !== undefined) console.log('[probe] player.src(' + JSON.stringify(s).replace(/apikey=[^&]+/, 'apikey=..').slice(0,200) + ')\\n' + new Error().stack.split('\\n').slice(2,7).join('\\n')); return os.apply(this, arguments); };
    var ol = p.load; p.load = function(){ console.log('[probe] player.load()\\n' + new Error().stack.split('\\n').slice(2,6).join('\\n')); return ol.apply(this, arguments); };
    var v = p.el().querySelector('video');
    ['loadstart','loadedmetadata','emptied','abort','error','canplay','seeking','seeked','stalled'].forEach(function(n){ v.addEventListener(n, function(){ console.log('[probe] video event ' + n + ' readyState=' + v.readyState + ' w=' + v.videoWidth + ' h=' + v.videoHeight + ' err=' + (v.error && v.error.code) + ' src=' + String(v.currentSrc).replace(/apikey=[^&]+/, 'apikey=..').slice(0,90)); }); });
    return 'instrumented';
  })()`);

  const snap = () => evaluate(`(function(){var p=window.__p,v=p.el().querySelector('video'),ss=p.sourceSelector();var b=document.getElementById('jasna-toggle-button');return JSON.stringify({btn:b.textContent,rs:v.readyState,t:+v.currentTime.toFixed(2),dur:+(v.duration||0).toFixed(1),w:v.videoWidth,verr:v.error&&v.error.code,perr:p.error()&&p.error().code,sel:ss.selectedIndex,selLabel:(ss.sources[ss.selectedIndex]||{}).label,paused:v.paused,src:String(v.currentSrc).replace(/apikey=[^&]+/,'apikey=..').slice(0,80)})})()`);
  if (preMode !== "fresh") {
    await evaluate(`window.__p.play()`);
    for (let i = 0; i < 60; i++) { const ok = await evaluate(`(function(){var v=window.__p.el().querySelector('video');return v.readyState>=3&&v.currentTime>1})()`); if (ok) break; await sleep(250); }
    console.log(`${ts()} playing: ${await snap()}`);
    if (seekTo > 0) {
      await evaluate(`window.__p.currentTime(${seekTo})`);
      for (let i = 0; i < 60; i++) { const ok = await evaluate(`(function(){var v=window.__p.el().querySelector('video');return v.readyState>=3&&!v.seeking&&v.currentTime>=${seekTo}})()`); if (ok) break; await sleep(250); }
      await sleep(1000);
      console.log(`${ts()} after seek: ${await snap()}`);
    }
    if (preMode === "play-pause") { await evaluate(`window.__p.pause()`); await sleep(300); }
  }
  console.log(`${ts()} before: ${await snap()}`);
  await evaluate(`document.getElementById('jasna-toggle-button').click()`);
  console.log(`${ts()} clicked toggle`);
  const pollUntil = async (label, cond, maxSec) => {
    let last = "";
    for (let i = 0; i < maxSec * 2; i++) {
      await sleep(500);
      const s = await snap(); if (s !== last) { console.log(`${ts()} ${label}: ${s}`); last = s; }
      if (await evaluate(cond)) return true;
    }
    return false;
  };
  const readyWith = (label) => `(function(){var v=window.__p.el().querySelector('video');return document.getElementById('jasna-toggle-button').textContent==='${label}'&&v.readyState>=3&&!v.seeking})()`;
  let ok = await pollUntil("state", readyWith("JASNA: ON"), pollSeconds);
  console.log(`${ts()} ON phase ${ok ? "OK" : "TIMED OUT"}`);
  if (process.argv[6] === "cycle") {
    await sleep(3000);
    const tOff = await evaluate(`window.__p.el().querySelector('video').currentTime`);
    await evaluate(`document.getElementById('jasna-toggle-button').click()`);
    console.log(`${ts()} clicked OFF at ${tOff.toFixed(2)}`);
    ok = await pollUntil("off-state", readyWith("JASNA: OFF"), pollSeconds);
    const tBack = await evaluate(`window.__p.el().querySelector('video').currentTime`);
    console.log(`${ts()} OFF phase ${ok ? "OK" : "TIMED OUT"}; position ${tOff.toFixed(2)} -> ${tBack.toFixed(2)} (delta ${(tBack - tOff).toFixed(2)})`);
    await sleep(3000);
    const tOn2 = await evaluate(`window.__p.el().querySelector('video').currentTime`);
    await evaluate(`document.getElementById('jasna-toggle-button').click()`);
    console.log(`${ts()} clicked ON again at ${tOn2.toFixed(2)}`);
    ok = await pollUntil("on2-state", readyWith("JASNA: ON"), pollSeconds);
    const tOn2b = await evaluate(`window.__p.el().querySelector('video').currentTime`);
    console.log(`${ts()} ON#2 phase ${ok ? "OK" : "TIMED OUT"}; position ${tOn2.toFixed(2)} -> ${tOn2b.toFixed(2)}`);
  }
  await shutdown();
}
async function shutdown() { chrome.kill("SIGKILL"); await sleep(300); require("fs").rmSync(profile, { recursive: true, force: true }); process.exit(0); }
main().catch(async (e) => { console.error("FATAL", e); await shutdown(); });
