// ==StashScript==
// name Collection Colors
// version 1.0.0
// description Colors scene cards by which configured Library path they
//             live under, auto-discovered from Settings > Library — no
//             hardcoded paths. Per-folder colors are editable via a
//             native color picker injected into this plugin's own
//             Settings > Plugins section.
// ==/StashScript==
;(() => {
  'use strict';

  const PLUGIN_ID = 'collection-colors';
  console.log('[CollectionColors] v1.0.0 loaded');

  /* ============================
   *  GRAPHQL
   * ============================ */
  async function gql(query, variables) {
    const res = await fetch('/graphql', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, variables }),
    });
    const json = await res.json();
    if (json.errors) throw new Error(json.errors.map(e => e.message).join('; '));
    return json.data;
  }

  /* ============================
   *  COLLECTIONS
   *  One "collection" per configured Library path (Settings > Library,
   *  `configuration.general.stashes`) — the live, authoritative source
   *  this plugin exists to replace hardcoding with. Colors come from
   *  this plugin's own saved setting when present, otherwise a
   *  deterministic default so a freshly-discovered folder still gets a
   *  distinct, stable color before anyone visits the settings panel.
   * ============================ */

  // Configured stash paths can carry a stray leading double-slash or
  // trailing slash (confirmed live on this server: one configured path is
  // literally `//storage/.../Uncensored/`) — normalize so path-matching
  // and the saved-color lookup both key off the same string regardless.
  function normalizePath(path) {
    return path.replace(/^\/+/, '/').replace(/\/+$/, '');
  }

  function basename(path) {
    return path.split('/').filter(Boolean).pop() || path;
  }

  const DEFAULT_PALETTE = [
    '#38bdf8', '#818cf8', '#f472b6', '#fb923c', '#34d399',
    '#facc15', '#de72f4', '#2dd4bf', '#94a3b8', '#f87171',
  ];
  function hashString(s) {
    let h = 0;
    for (let i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function defaultColorFor(path) {
    return DEFAULT_PALETTE[hashString(path) % DEFAULT_PALETTE.length];
  }

  function readSavedColors(pluginsConfig) {
    const raw = pluginsConfig?.[PLUGIN_ID]?.collectionColors;
    if (!raw) return {};
    try { return JSON.parse(raw) || {}; } catch (e) { return {}; }
  }

  let collectionsPromise = null;
  async function loadCollections(force) {
    if (collectionsPromise && !force) return collectionsPromise;
    collectionsPromise = (async () => {
      const data = await gql(`{
        configuration {
          general { stashes { path } }
          plugins
        }
      }`);
      const saved = readSavedColors(data.configuration.plugins);
      return data.configuration.general.stashes.map(s => {
        const path = normalizePath(s.path);
        return { path, label: basename(path), color: saved[path] || defaultColorFor(path) };
      });
    })();
    return collectionsPromise;
  }

  async function saveColors(collections) {
    const colors = {};
    for (const c of collections) colors[c.path] = c.color;
    await gql(
      `mutation($input: Map!) { configurePlugin(plugin_id: "${PLUGIN_ID}", input: $input) }`,
      { input: { collectionColors: JSON.stringify(colors) } }
    );
  }

  function collectionFilterURL(path) {
    const regex = '^' + path + '/.*';
    const criterion = '("type":"path","modifier":"MATCHES_REGEX","value":"' + regex + '")';
    return '/scenes?c=' + encodeURIComponent(criterion) + '&sortby=path&perPage=25';
  }

  // Longest-match-first so a path nested under more than one configured
  // stash (shouldn't normally happen, but Library config isn't guaranteed
  // non-overlapping) picks the more specific one, not just whichever
  // happens to be first in the list.
  function deriveCollection(collections, filePath) {
    if (!filePath) return null;
    let best = null;
    for (const c of collections) {
      if (filePath.startsWith(c.path + '/') && (!best || c.path.length > best.path.length)) best = c;
    }
    return best;
  }

  /* ============================
   *  SCENE PATH LOOKUP
   *  Own lightweight batched-query mechanism, independent of any other
   *  plugin — only needs `files { path }`, not the full card-enhancement
   *  data clean-cards.js's own batcher fetches.
   *
   *  Briefly extended to also carry `director` (for the header pill's
   *  Director row) — moved back out once that row itself moved to
   *  dracula-layout, which already had the value on hand natively and
   *  doesn't need a GraphQL round trip to get it. Back to path-only.
   * ============================ */
  const pathCache = new Map();
  const waiting = new Map();
  const highPriority = new Set();
  let batchTimeout = null;
  const BATCH_SIZE = 20;

  function requestScenePath(sceneId, callback) {
    if (pathCache.has(sceneId)) { callback(pathCache.get(sceneId)); return; }
    if (!waiting.has(sceneId)) waiting.set(sceneId, []);
    waiting.get(sceneId).push(callback);
    highPriority.add(sceneId);
    if (!batchTimeout) batchTimeout = setTimeout(runBatchQuery, 30);
  }

  async function runBatchQuery() {
    batchTimeout = null;
    const batch = [];
    for (const id of highPriority) { if (batch.length >= BATCH_SIZE) break; batch.push(id); highPriority.delete(id); }
    if (!batch.length) return;
    try {
      const data = await gql(`{ ${batch.map(id => `s${id}: findScene(id: ${id}) { id files { path } }`).join('\n')} }`);
      for (const key in data) {
        const scene = data[key];
        if (!scene) continue;
        const id = String(scene.id);
        const path = scene.files?.[0]?.path || null;
        pathCache.set(id, path);
        if (waiting.has(id)) { waiting.get(id).forEach(cb => cb(path)); waiting.delete(id); }
      }
    } catch (e) {
      console.error('[CollectionColors] batch query failed', e);
    }
    if (highPriority.size) batchTimeout = setTimeout(runBatchQuery, 30);
  }

  function buildPill(collection, extraClass) {
    const pill = document.createElement('span');
    pill.className = extraClass ? `stash-collection-pill ${extraClass}` : 'stash-collection-pill';
    pill.textContent = collection.label;
    pill.style.color = collection.color;
    pill.style.backgroundColor = collection.color + '22';
    pill.style.border = '1px solid ' + collection.color + '55';
    pill.title = 'Filter: ' + collection.label;
    pill.addEventListener('click', e => {
      e.stopPropagation();
      e.preventDefault();
      window.location.href = collectionFilterURL(collection.path);
    });
    return pill;
  }

  /* ============================
   *  CARD DECORATION
   *  Same insertion point/UX as the pill this replaces in clean-cards.js
   *  (dracula-layout): a clickable pill in `.card-popovers.btn-group`,
   *  colored per collection, filtering to that folder on click.
   * ============================ */
  const cardObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      cardObserver.unobserve(entry.target);
      decorateCard(entry.target);
    }
  }, { threshold: 0.1 });

  async function decorateCard(card) {
    if (card.dataset.collectionColors) return;
    const link = card.querySelector('a[href^="/scenes/"]');
    const bar = card.querySelector('.card-popovers.btn-group');
    if (!link || !bar) return;
    const sceneId = link.href.match(/\/scenes\/(\d+)/)?.[1];
    if (!sceneId) return;
    card.dataset.collectionColors = 'pending';

    const collections = await loadCollections();
    requestScenePath(sceneId, filePath => {
      if (!document.contains(card)) return;
      const collection = deriveCollection(collections, filePath || '');
      card.dataset.collectionColors = collection ? collection.label : 'none';
      if (!collection) return;

      // Bare pill, no wrapper — a wrapper (flex-basis: 100%, forcing the
      // pill onto its own guaranteed line) was tried here first, to fix a
      // real wrap/alignment bug on a second stash instance whose
      // `.card-popovers` allows flex-wrap. Reverted: the actual fix
      // belongs one layer up, in whichever plugin owns that bar's layout
      // (dracula-layout, if installed) — its own popover priority/
      // overflow-hide system now treats this pill as one more prioritized
      // item (same row as every native icon, lowest-priority items hidden
      // instead of wrapped when a card is too narrow), which is what
      // actually keeps everything vertically aligned. A bare pill is what
      // that system expects to find and anchor/reorder.
      bar.insertBefore(buildPill(collection), bar.firstChild);
    });
  }

  function scanCards() {
    document.querySelectorAll('.scene-card').forEach(card => {
      if (card.dataset.collectionColors) return;
      cardObserver.observe(card);
    });
  }

  // Disabled 2026-08-30: pill moved to between Original Title and the
  // toolbar instead. Left commented (not deleted) in case this placement
  // is restored later — see trySetupMetadataPill() below.
//   /* ============================
//    *  METADATA PILL
//    *  Reaches into dracula-layout's Metadata card, if present, adding a
//    *  "Collection:" row of its own alongside Resolution/Framerate/
//    *  Director/Created/Updated. Entirely no-op — nothing to find — when
//    *  dracula-layout isn't installed, or the current page isn't a single
//    *  scene's detail page.
//    *
//    *  Reuses the exact `data-dl-item="scenedetails"` value the native rows
//    *  use, so it gets that card's row styling (grid layout, colors, font)
//    *  and Browse-only visibility for free from dracula-layout's own CSS —
//    *  no rule of this plugin's own needs to duplicate either. `order: 69`
//    *  keeps it before Resolution (order 70, the lowest native value) —
//    *  deliberately always FIRST, never last: dracula-layout's own
//    *  data-dl-last-in-group pass (which marks whichever native row is
//    *  visually last for its rounded bottom border) has no idea this row
//    *  exists, so if this row ever rendered after all native ones, that
//    *  pass would keep bordering the wrong (now not-actually-last) row.
//    *  Staying first sidesteps that entirely.
//    *
//    *  `.scene-details` is a real React-owned node (constraint 1: append
//    *  only, never move/remove it) but is the same, stable, reused element
//    *  across dracula-layout's own re-renders — safe to anchor state on via
//    *  a dataset flag, same pattern as the settings panel/card decoration.
//    * ============================ */
//   function trySetupMetadataPill() {
//     const match = location.pathname.match(/^\/scenes\/(\d+)/);
//     const sceneId = match ? match[1] : null;
//     if (!sceneId) return;
//
//     const sceneDetails = document.querySelector('.scene-details');
//     if (!sceneDetails) return;
//
//     if (sceneDetails.dataset.collectionColorsScene === sceneId) return;
//     sceneDetails.dataset.collectionColorsScene = sceneId;
//     const stale = sceneDetails.querySelector(':scope > [data-dl-collection-row]');
//     if (stale) stale.remove();
//
//     loadCollections().then(collections => {
//       requestScenePath(sceneId, filePath => {
//         const liveSceneDetails = document.querySelector('.scene-details');
//         if (!liveSceneDetails || liveSceneDetails.dataset.collectionColorsScene !== sceneId) return;
//         if (liveSceneDetails.querySelector(':scope > [data-dl-collection-row]')) return;
//         const collection = deriveCollection(collections, filePath || '');
//         if (!collection) return;
//
//         const row = document.createElement('h6');
//         row.setAttribute('data-dl-item', 'scenedetails');
//         row.setAttribute('data-dl-label', 'Collection');
//         row.setAttribute('data-dl-collection-row', 'true');
//         row.style.order = '69';
//         row.appendChild(buildPill(collection, 'dl-scene-collection-pill'));
//         liveSceneDetails.appendChild(row);
//       });
//     }).catch(e => console.error('[CollectionColors] metadata pill', e));
//   }

  /* ============================
   *  HEADER PILL
   *  Reaches into dracula-layout's persistent identity header, if
   *  present — specifically `.dl-scene-badge-slot`, a stable, empty
   *  element dracula-layout documents and reserves exactly for this
   *  (see buildSceneBadgeRow() in its scene-dashboard.js) — and drops
   *  the pill into it. No-op when the slot doesn't exist (dracula-layout
   *  isn't installed, or is an old version that predates it).
   *
   *  This used to build the whole row itself (pill, an fps/resolution
   *  readout, a Director line) directly inside `.scene-details`, with
   *  its own order/flex placement logic and a second GraphQL fetch for
   *  `director`. Moved: none of that surrounding content is collection
   *  data, dracula-layout already had it on hand natively for its own
   *  Metadata mirrors, and re-deriving it here duplicated that work and
   *  made this plugin depend on knowing dracula-layout's exact header
   *  layout (Original Title/Toolbar order values) instead of just one
   *  documented slot. Only the pill itself — genuinely collection data —
   *  stays this plugin's responsibility.
   * ============================ */
  function trySetupHeaderPill() {
    const match = location.pathname.match(/^\/scenes\/(\d+)/);
    const sceneId = match ? match[1] : null;
    if (!sceneId) return;

    const slot = document.querySelector('.dl-scene-badge-slot');
    if (!slot) return;

    if (slot.dataset.collectionColorsScene === sceneId) return;
    slot.dataset.collectionColorsScene = sceneId;
    slot.replaceChildren();

    loadCollections().then(collections => {
      requestScenePath(sceneId, filePath => {
        const liveSlot = document.querySelector('.dl-scene-badge-slot');
        if (!liveSlot || liveSlot.dataset.collectionColorsScene !== sceneId) return;
        if (liveSlot.firstChild) return;
        const collection = deriveCollection(collections, filePath || '');
        if (!collection) return;
        liveSlot.appendChild(buildPill(collection, 'dl-scene-collection-pill'));
      });
    }).catch(e => console.error('[CollectionColors] header pill', e));
  }

  /* ============================
   *  SETTINGS PANEL (Settings > Plugins)
   *  Stash renders each declared setting at a predictable id,
   *  `plugin-<pluginId>-<settingKey>` — confirmed live, not assumed —
   *  so this can target its own raw-JSON field directly rather than
   *  fuzzy-matching on text. That native field stays in the DOM (hidden)
   *  as a fallback/escape hatch; the picker grid is inserted as a
   *  sibling within the same `.plugin-settings` container.
   * ============================ */
  const SETTING_ID = `plugin-${PLUGIN_ID}-collectionColors`;
  const HEX_RE = /^#[0-9a-f]{6}$/i;

  // navigator.clipboard needs a secure context — true for http://localhost,
  // but stash is often also reached over plain http via a LAN IP (no TLS),
  // where it's simply absent. Fall back to the classic hidden-textarea +
  // execCommand('copy') trick so Copy still works from a LAN client.
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
  }

  // Small Feather-style icon builder — createElementNS to match this
  // project's "build DOM, don't innerHTML" convention (see clean-cards.js).
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function svgIcon(className, children) {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('width', '14');
    svg.setAttribute('height', '14');
    svg.setAttribute('fill', 'none');
    svg.setAttribute('stroke', 'currentColor');
    svg.setAttribute('stroke-width', '2');
    svg.setAttribute('stroke-linecap', 'round');
    svg.setAttribute('stroke-linejoin', 'round');
    svg.classList.add(className);
    for (const [tag, attrs] of children) {
      const el = document.createElementNS(SVG_NS, tag);
      for (const k in attrs) el.setAttribute(k, attrs[k]);
      svg.appendChild(el);
    }
    return svg;
  }
  function copyIcon() {
    return svgIcon('icon-copy', [
      ['rect', { x: 9, y: 9, width: 13, height: 13, rx: 2, ry: 2 }],
      ['path', { d: 'M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1' }],
    ]);
  }
  function checkIcon() {
    return svgIcon('icon-check', [['polyline', { points: '20 6 9 17 4 12' }]]);
  }

  function buildRow(collection, onSave) {
    const row = document.createElement('div');
    row.className = 'collection-colors-row';

    const swatch = document.createElement('input');
    swatch.type = 'color';
    swatch.value = collection.color;
    swatch.className = 'collection-colors-swatch';

    const hexInput = document.createElement('input');
    hexInput.type = 'text';
    hexInput.className = 'collection-colors-hex';
    hexInput.spellcheck = false;
    hexInput.maxLength = 7;
    hexInput.value = collection.color;
    hexInput.setAttribute('aria-label', `Hex color for ${collection.label}`);

    // Single path for "this is now the color", whether it came from the
    // native picker or a typed/pasted hex value — keeps swatch, hex text
    // and the saved collection.color from ever disagreeing.
    function applyColor(value) {
      collection.color = value;
      swatch.value = value;
      hexInput.value = value;
      hexInput.classList.remove('is-invalid');
      onSave();
    }

    swatch.addEventListener('input', () => {
      // Live preview while dragging in the native picker, before 'change'
      // (and therefore the save) fires.
      hexInput.value = swatch.value;
    });
    swatch.addEventListener('change', () => applyColor(swatch.value));

    hexInput.addEventListener('input', () => {
      const v = hexInput.value.trim();
      const normalized = v.startsWith('#') ? v : '#' + v;
      if (HEX_RE.test(normalized)) applyColor(normalized.toLowerCase());
      else hexInput.classList.add('is-invalid');
    });
    hexInput.addEventListener('blur', () => {
      // Leaving the field on an incomplete/invalid value reverts it rather
      // than saving garbage or leaving the swatch and text mismatched.
      if (!HEX_RE.test(hexInput.value.trim())) hexInput.value = collection.color;
      hexInput.classList.remove('is-invalid');
    });
    hexInput.addEventListener('keydown', e => {
      if (e.key === 'Enter') hexInput.blur();
    });

    const copyBtn = document.createElement('button');
    copyBtn.type = 'button';
    copyBtn.className = 'collection-colors-copy';
    copyBtn.title = 'Copy hex value';
    copyBtn.setAttribute('aria-label', 'Copy hex value');
    copyBtn.appendChild(copyIcon());
    copyBtn.appendChild(checkIcon());
    copyBtn.addEventListener('click', async () => {
      try {
        await copyText(collection.color);
        copyBtn.classList.add('is-copied');
        clearTimeout(copyBtn._copiedTimeout);
        copyBtn._copiedTimeout = setTimeout(() => copyBtn.classList.remove('is-copied'), 1200);
      } catch (e) {
        console.error('[CollectionColors] copy failed', e);
      }
    });

    const control = document.createElement('div');
    control.className = 'collection-colors-control';
    control.appendChild(swatch);
    control.appendChild(hexInput);
    control.appendChild(copyBtn);

    const label = document.createElement('span');
    label.className = 'collection-colors-label';
    label.textContent = collection.label;

    const path = document.createElement('span');
    path.className = 'collection-colors-path';
    path.textContent = collection.path;

    row.appendChild(control);
    row.appendChild(label);
    row.appendChild(path);
    return row;
  }

  async function buildSettingsPanel(rawSetting, container) {
    // Claim synchronously before the first `await` below — checking
    // `container.querySelector('.collection-colors-panel')` alone was a
    // TOCTOU race: trySetupSettingsPanel() re-fires on every body-wide
    // MutationObserver tick while Settings > Plugins is open, and the
    // panel isn't actually inserted until after `loadCollections(true)`'s
    // GraphQL round trip resolves. Two ticks landing before that first
    // fetch completes both passed the old check (neither had built a
    // panel yet) and each built and inserted its own — the intermittent
    // "two dropdowns" bug, timing-dependent and hard to reproduce on
    // demand for exactly that reason. Same fix as everywhere else in this
    // file that starts async work from an observer callback (see
    // decorateCard()/trySetupHeaderPill()): set a flag *before* the
    // await, so a second call arriving during the gap sees the claim
    // immediately and bails, regardless of which async stage the first
    // call is in.
    if (container.dataset.collectionColorsPanel) return;
    container.dataset.collectionColorsPanel = 'pending';
    rawSetting.style.display = 'none';

    const collections = await loadCollections(true);

    // <details>/<summary> rather than a hand-rolled toggle: this whole tree
    // is plugin-injected, not React-owned (unlike the dashboard's
    // ensureGroupHead()/collapse machinery in dracula-layout), so there's no
    // reason not to use the native collapsible element — free keyboard/
    // a11y support, no click-handler/state bookkeeping needed. Closed by
    // default so the settings page stays compact until opened.
    const panel = document.createElement('details');
    panel.className = 'setting collection-colors-panel';

    const summary = document.createElement('summary');
    const h3 = document.createElement('h3');
    h3.textContent = `Collection colors (${collections.length})`;
    summary.appendChild(h3);
    panel.appendChild(summary);

    const sub = document.createElement('div');
    sub.className = 'sub-heading';
    sub.textContent = 'One row per configured Library path (Settings › Library). Click a swatch to change its color.';
    panel.appendChild(sub);

    const grid = document.createElement('div');
    grid.className = 'collection-colors-grid';

    let saveTimeout = null;
    const scheduleSave = () => {
      clearTimeout(saveTimeout);
      saveTimeout = setTimeout(() => { saveColors(collections).catch(e => console.error('[CollectionColors] save failed', e)); }, 200);
    };

    for (const c of collections) grid.appendChild(buildRow(c, scheduleSave));
    panel.appendChild(grid);

    container.insertBefore(panel, rawSetting);
    container.dataset.collectionColorsPanel = 'built';
  }

  function trySetupSettingsPanel() {
    const rawSetting = document.getElementById(SETTING_ID);
    if (!rawSetting) return;
    const container = rawSetting.closest('.plugin-settings');
    if (!container) return;
    buildSettingsPanel(rawSetting, container).catch(e => {
      console.error('[CollectionColors] settings panel', e);
      // Clear the claim on failure (e.g. loadCollections' GraphQL call
      // rejects) so a later observer tick gets a real retry instead of
      // finding 'pending' forever and silently never trying again.
      delete container.dataset.collectionColorsPanel;
    });
  }

  /* ============================
   *  MAIN
   * ============================ */
  let queued = false;
  const observer = new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      try {
        scanCards();
        trySetupSettingsPanel();
        // trySetupMetadataPill(); // disabled — see note above its definition
        trySetupHeaderPill();
      } catch (e) {
        console.error('[CollectionColors]', e);
      }
    });
  });
  observer.observe(document.body, { childList: true, subtree: true });
  scanCards();
  trySetupSettingsPanel();
  // trySetupMetadataPill(); // disabled — see note above its definition
  trySetupHeaderPill();
})();
