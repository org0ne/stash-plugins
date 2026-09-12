// ==StashScript==
// name JAV Layout — Theme
// version 1.1
// description Applies the plugin's `theme` and `headerBackdrop` settings
//             as data-jl-theme / data-jl-backdrop on <html> (themes.css
//             and scene-dashboard.css key off them) and replaces each
//             setting's raw text field in Settings › Plugins with a
//             dropdown of the values the stylesheets actually define.
// ==/StashScript==
;(() => {
  'use strict';

  const PLUGIN_ID = 'jav-layout';

  // One entry per `html[data-jl-theme="…"]` block in themes.css. The id is
  // the value stored in the plugin setting and the attribute; the label is
  // what the dropdown shows. Add a theme in themes.css first, then here.
  const THEMES = [
    { id: 'dracula',          label: 'Dracula (default)' },
    { id: 'catppuccin-mocha', label: 'Catppuccin Mocha' },
    { id: 'rose-pine-moon',   label: 'Rosé Pine Moon' },
    { id: 'kanagawa-wave',    label: 'Kanagawa Wave' },
    { id: 'tokyo-night',      label: 'Tokyo Night' },
    { id: 'moonlight',        label: 'Moonlight' },
    { id: 'synthwave-84',     label: "Synthwave '84" },
    { id: 'aura-dark',        label: 'Aura Dark' },
    { id: 'horizon-dark',     label: 'Horizon Dark' },
    { id: 'sonokai',          label: 'Sonokai' },
    { id: 'monokai',          label: 'Monokai' },
    // Original palettes (2026-09-11) — drawn for this plugin, not from an
    // upstream project; see the ORIGINAL PALETTES section of themes.css.
    { id: 'tropical-punch',   label: 'Tropical Punch' },
    { id: 'ube-mango',        label: 'Ube & Mango' },
    { id: 'blood-orange',     label: 'Blood Orange' },
    { id: 'matcha',           label: 'Matcha' },
    { id: 'brass-ink',        label: 'Brass & Ink' },
    { id: 'abyss',            label: 'Abyss' },
  ];

  // One entry per `html[data-jl-backdrop="…"]` variant in
  // scene-dashboard.css (Header backdrop section), plus "none".
  const BACKDROPS = [
    { id: 'none',      label: 'None (default)' },
    { id: 'floor',     label: 'Floor — header recedes onto the deep surface' },
    { id: 'signature', label: 'Signature line — accent-to-link hairline under the player' },
  ];

  /* Every closed-list setting this plugin owns, driven by one table so a
   * third one is a row here plus its stylesheet rules, nothing else.
   *   key      the plugin setting key (manifest) and the native row's id suffix
   *   storage  localStorage key for the first-paint cache
   *   attr     dataset property on <html>; absent when the value is the default
   *   dflt     the value that means "no attribute"
   *   event    optional DOM event fired after a change, for stylesheets
   *            whose consumers need to re-measure (scene-dashboard.js) */
  const SETTINGS = [
    {
      key: 'theme', storage: 'jl.theme', attr: 'jlTheme', dflt: 'dracula',
      label: 'Color theme',
      help: 'Palette for the whole app — stash’s own pages and everything this plugin draws. Applies immediately and is saved to this plugin’s settings.',
      options: THEMES, noun: 'theme',
    },
    {
      key: 'headerBackdrop', storage: 'jl.backdrop', attr: 'jlBackdrop', dflt: 'none',
      label: 'Scene header backdrop',
      help: 'An optional gradient behind the scene page’s identity header (studio logo, code/date bar, title, toolbar). Applies immediately and is saved to this plugin’s settings.',
      options: BACKDROPS, noun: 'backdrop', event: 'jl-backdrop-change',
    },
  ];
  for (const s of SETTINGS) {
    s.rowId = `plugin-${PLUGIN_ID}-${s.key}`;
    s.current = s.dflt;
    s.selectEl = null;
  }

  const normalize = (s, id) => (s.options.some(o => o.id === id) ? id : s.dflt);

  /* The default value is expressed as NO attribute rather than, say,
   * data-jl-theme="dracula": themes.css's bare `:root` block is Dracula,
   * and scene-dashboard.css's backdrop rules only match when the
   * attribute exists — so an unknown or missing value degrades to the
   * default for free, which is the failsafe this plugin wants (same
   * spirit as constraint 6 in CLAUDE.md: if this script never runs, the
   * page is Dracula with no backdrop). Writes are equality-guarded like
   * every other per-run DOM write in this plugin. */
  function apply(s, id) {
    id = normalize(s, id);
    const html = document.documentElement;
    const before = html.dataset[s.attr];
    if (id === s.dflt) {
      if (s.attr in html.dataset) delete html.dataset[s.attr];
    } else if (html.dataset[s.attr] !== id) {
      html.dataset[s.attr] = id;
    }
    try { localStorage.setItem(s.storage, id); } catch (e) { /* private mode */ }
    if (s.event && html.dataset[s.attr] !== before) document.dispatchEvent(new Event(s.event));
    return id;
  }

  /* 1. Synchronous, from the cache: plugin JS loads after stash's own
   *    bundle, so there is always some Dracula-colored paint before the
   *    settings round trip below resolves. The cached value closes that
   *    gap on every load after the first. */
  for (const s of SETTINGS) {
    try { s.current = apply(s, localStorage.getItem(s.storage) || s.dflt); } catch (e) { /* ignore */ }
  }

  async function gql(query, variables) {
    const res = await fetch('/graphql', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, variables }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    if (json.errors) throw new Error(json.errors.map(e => e.message).join('; '));
    return json.data;
  }

  async function readSettings() {
    const data = await gql('{ configuration { plugins } }');
    return data?.configuration?.plugins?.[PLUGIN_ID] || {};
  }

  /* `configurePlugin` REPLACES the plugin's whole settings map, so the
   * current map is read first and the new value merged into it — the same
   * config-clobbering landmine collection-colors hit and documented in its
   * own source (writing one key alone wiped the others). With two settings
   * here the merge is what keeps changing the backdrop from wiping the
   * theme, and vice versa. */
  async function writeSetting(key, value) {
    const existing = await readSettings();
    await gql(
      `mutation($input: Map!) { configurePlugin(plugin_id: "${PLUGIN_ID}", input: $input) }`,
      { input: { ...existing, [key]: value } },
    );
  }

  /* 2. Authoritative, from the plugin settings. Wins over the cache: the
   *    cache only exists to hide the first-paint gap, and a setting
   *    changed from another browser must still take effect here. */
  readSettings()
    .then(map => { for (const s of SETTINGS) { s.current = apply(s, map[s.key] || s.dflt); syncSelect(s); } })
    .catch(e => console.warn('[JavLayout theme] could not read plugin settings, using cached values', e));

  /* ============================
   *  SETTINGS PANEL (Settings › Plugins › JAV Layout)
   *  Stash renders each declared setting as a `.setting` row with id
   *  `plugin-<pluginId>-<settingKey>` (confirmed live: the row holds the
   *  h3 / current-value / description block and an Edit button that opens
   *  a free-text modal). A free-text field is the wrong control for a
   *  closed list, so each native row is hidden and a sibling row with a
   *  <select> takes its place — inserted, never moving or re-parenting
   *  anything React owns (CLAUDE.md constraint 1). The native rows stay
   *  in the DOM as the escape hatch.
   * ============================ */
  function syncSelect(s) {
    if (s.selectEl && s.selectEl.value !== s.current) s.selectEl.value = s.current;
  }

  function buildRow(s) {
    const row = document.createElement('div');
    // .jl-theme-setting is kept on every row for the stylesheet hooks and
    // for anything else that looked for it; data-jl-key says which one.
    row.className = 'setting jl-theme-setting';
    row.dataset.jlKey = s.key;

    const text = document.createElement('div');
    const h3 = document.createElement('h3');
    h3.textContent = s.label;
    const sub = document.createElement('div');
    sub.className = 'sub-heading';
    sub.textContent = s.help;
    text.appendChild(h3);
    text.appendChild(sub);

    const control = document.createElement('div');
    const select = document.createElement('select');
    select.className = 'form-control input-control jl-theme-select';
    select.setAttribute('aria-label', s.label);
    for (const o of s.options) {
      const opt = document.createElement('option');
      opt.value = o.id;
      opt.textContent = o.label;
      select.appendChild(opt);
    }
    select.value = s.current;
    const status = document.createElement('div');
    status.className = 'sub-heading jl-theme-status';
    status.setAttribute('aria-live', 'polite');

    select.addEventListener('change', () => {
      const previous = s.current;
      s.current = apply(s, select.value);
      status.textContent = 'Saving…';
      writeSetting(s.key, s.current)
        .then(() => { status.textContent = 'Saved'; setTimeout(() => { if (status.textContent === 'Saved') status.textContent = ''; }, 1500); })
        .catch(e => {
          console.error('[JavLayout theme] save failed', e);
          // Keep the page and the dropdown honest about what is actually
          // persisted: revert both to the last value known to be saved.
          s.current = apply(s, previous);
          select.value = previous;
          status.textContent = `Could not save the ${s.noun} — see the browser console.`;
        });
    });

    control.appendChild(select);
    control.appendChild(status);
    row.appendChild(text);
    row.appendChild(control);
    s.selectEl = select;
    return row;
  }

  function trySetupSettingsPanel() {
    // Cheap route gate first: this runs from a body-wide observer (below),
    // and every other page can bail before touching the DOM at all.
    if (!location.pathname.startsWith('/settings')) return;
    for (const s of SETTINGS) {
      const native = document.getElementById(s.rowId);
      if (!native) continue;
      const container = native.closest('.plugin-settings');
      if (!container) continue;
      if (container.querySelector(`:scope > .jl-theme-setting[data-jl-key="${s.key}"]`)) continue;
      if (native.style.display !== 'none') native.style.display = 'none';
      container.insertBefore(buildRow(s), native);
    }
  }

  /* Body-wide childList observer, same shape as the other files' — but
   * its callback is a pathname check and one getElementById per setting
   * on every page except Settings, so it costs nothing measurable
   * elsewhere (the 2026-09-02 profile is the reference for what
   * "measurable" means here — see CLAUDE.md's Testing section). */
  let queued = false;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      try { trySetupSettingsPanel(); } catch (e) { console.error('[JavLayout theme]', e); }
    });
  }).observe(document.body, { childList: true, subtree: true });
  trySetupSettingsPanel();

  // For other plugins / the console: read or switch a setting without
  // going through Settings (switching here does NOT persist). The theme
  // keeps its original top-level shape; the backdrop hangs off .backdrop.
  const api = s => ({
    list: () => s.options.map(o => ({ ...o })),
    current: () => s.current,
    preview: id => { s.current = apply(s, id); syncSelect(s); return s.current; },
  });
  window.JLTheme = { ...api(SETTINGS[0]), backdrop: api(SETTINGS[1]) };
})();
