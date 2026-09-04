// ==StashScript==
// name JAV Layout — Theme
// version 1.0
// description Applies the plugin's `theme` setting as data-jl-theme on
//             <html> (themes.css keys every color token off it) and
//             replaces the setting's raw text field in Settings › Plugins
//             with a dropdown of the themes themes.css actually defines.
// ==/StashScript==
;(() => {
  'use strict';

  const PLUGIN_ID = 'jav-layout';
  const SETTING_KEY = 'theme';
  const SETTING_ID = `plugin-${PLUGIN_ID}-${SETTING_KEY}`;
  const STORAGE_KEY = 'jl.theme';
  const DEFAULT_THEME = 'dracula';

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
    { id: 'night-owl',        label: 'Night Owl' },
    { id: 'aura-dark',        label: 'Aura Dark' },
    { id: 'andromeda',        label: 'Andromeda' },
    { id: 'horizon-dark',     label: 'Horizon Dark' },
    { id: 'sonokai',          label: 'Sonokai' },
    { id: 'poimandres',       label: 'Poimandres' },
  ];

  const isKnown = id => THEMES.some(t => t.id === id);
  const normalize = id => (isKnown(id) ? id : DEFAULT_THEME);

  /* The default theme is the bare `:root` block in themes.css, so it is
   * expressed as NO attribute rather than data-jl-theme="dracula" — an
   * unknown or missing value degrades to Dracula for free, which is the
   * failsafe this plugin wants (same spirit as constraint 6 in CLAUDE.md:
   * if this script never runs, the page is Dracula). Writes are
   * equality-guarded like every other per-run DOM write in this plugin. */
  function apply(id) {
    id = normalize(id);
    const html = document.documentElement;
    if (id === DEFAULT_THEME) {
      if ('jlTheme' in html.dataset) delete html.dataset.jlTheme;
    } else if (html.dataset.jlTheme !== id) {
      html.dataset.jlTheme = id;
    }
    try { localStorage.setItem(STORAGE_KEY, id); } catch (e) { /* private mode */ }
    return id;
  }

  /* 1. Synchronous, from the cache: plugin JS loads after stash's own
   *    bundle, so there is always some Dracula-colored paint before the
   *    settings round trip below resolves. The cached value closes that
   *    gap on every load after the first. */
  let current = DEFAULT_THEME;
  try { current = apply(localStorage.getItem(STORAGE_KEY) || DEFAULT_THEME); } catch (e) { /* ignore */ }

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

  async function readSetting() {
    const data = await gql('{ configuration { plugins } }');
    return data?.configuration?.plugins?.[PLUGIN_ID]?.[SETTING_KEY];
  }

  /* `configurePlugin` REPLACES the plugin's whole settings map, so the
   * current map is read first and the new value merged into it — the same
   * config-clobbering landmine collection-colors hit and documented in its
   * own source (writing one key alone wiped the others). jav-layout has
   * only this one setting today, but the merge costs nothing and keeps
   * that from becoming a bug the day a second setting is added. */
  async function writeSetting(id) {
    const data = await gql('{ configuration { plugins } }');
    const existing = data?.configuration?.plugins?.[PLUGIN_ID] || {};
    await gql(
      `mutation($input: Map!) { configurePlugin(plugin_id: "${PLUGIN_ID}", input: $input) }`,
      { input: { ...existing, [SETTING_KEY]: id } },
    );
  }

  /* 2. Authoritative, from the plugin setting. Wins over the cache: the
   *    cache only exists to hide the first-paint gap, and a setting
   *    changed from another browser must still take effect here. */
  readSetting()
    .then(v => { current = apply(v || DEFAULT_THEME); syncSelect(); })
    .catch(e => console.warn('[JavLayout theme] could not read plugin settings, using cached theme', e));

  /* ============================
   *  SETTINGS PANEL (Settings › Plugins › JAV Layout)
   *  Stash renders each declared setting as a `.setting` row with id
   *  `plugin-<pluginId>-<settingKey>` (confirmed live: the row holds the
   *  h3 / current-value / description block and an Edit button that opens
   *  a free-text modal). A free-text field is the wrong control for a
   *  closed list of themes, so that native row is hidden and a sibling
   *  row with a <select> takes its place — inserted, never moving or
   *  re-parenting anything React owns (CLAUDE.md constraint 1). The
   *  native row stays in the DOM as the escape hatch.
   * ============================ */
  let selectEl = null;

  function syncSelect() {
    if (selectEl && selectEl.value !== current) selectEl.value = current;
  }

  function buildRow() {
    const row = document.createElement('div');
    row.className = 'setting jl-theme-setting';

    const text = document.createElement('div');
    const h3 = document.createElement('h3');
    h3.textContent = 'Color theme';
    const sub = document.createElement('div');
    sub.className = 'sub-heading';
    sub.textContent = 'Palette for the whole app — stash’s own pages and everything this plugin draws. Applies immediately and is saved to this plugin’s settings.';
    text.appendChild(h3);
    text.appendChild(sub);

    const control = document.createElement('div');
    const select = document.createElement('select');
    select.className = 'form-control input-control jl-theme-select';
    select.setAttribute('aria-label', 'Color theme');
    for (const t of THEMES) {
      const opt = document.createElement('option');
      opt.value = t.id;
      opt.textContent = t.label;
      select.appendChild(opt);
    }
    select.value = current;
    const status = document.createElement('div');
    status.className = 'sub-heading jl-theme-status';
    status.setAttribute('aria-live', 'polite');

    select.addEventListener('change', () => {
      const previous = current;
      current = apply(select.value);
      status.textContent = 'Saving…';
      writeSetting(current)
        .then(() => { status.textContent = 'Saved'; setTimeout(() => { if (status.textContent === 'Saved') status.textContent = ''; }, 1500); })
        .catch(e => {
          console.error('[JavLayout theme] save failed', e);
          // Keep the page and the dropdown honest about what is actually
          // persisted: revert both to the last value known to be saved.
          current = apply(previous);
          select.value = previous;
          status.textContent = 'Could not save the theme — see the browser console.';
        });
    });

    control.appendChild(select);
    control.appendChild(status);
    row.appendChild(text);
    row.appendChild(control);
    selectEl = select;
    return row;
  }

  function trySetupSettingsPanel() {
    // Cheap route gate first: this runs from a body-wide observer (below),
    // and every other page can bail before touching the DOM at all.
    if (!location.pathname.startsWith('/settings')) return;
    const native = document.getElementById(SETTING_ID);
    if (!native) return;
    const container = native.closest('.plugin-settings');
    if (!container) return;
    if (container.querySelector(':scope > .jl-theme-setting')) return;
    if (native.style.display !== 'none') native.style.display = 'none';
    container.insertBefore(buildRow(), native);
  }

  /* Body-wide childList observer, same shape as the other files' — but
   * its callback is a pathname check and one getElementById on every
   * page except Settings, so it costs nothing measurable elsewhere (the
   * 2026-09-02 profile is the reference for what "measurable" means
   * here — see CLAUDE.md's Testing section). */
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

  // For other plugins / the console: read or switch the theme without
  // going through Settings (switching here does NOT persist).
  window.JLTheme = { list: () => THEMES.map(t => ({ ...t })), current: () => current, preview: id => { current = apply(id); syncSelect(); return current; } };
})();
