// ==StashScript==
// name Entity Dashboard
// version 1.0
// description Generic mode-bar (native tab strip -> jl-modes pill row)
//             treatment for every "entity with a tabbed relations list"
//             detail page - performer/studio/group/tag. Consolidates what
//             were becoming near-identical per-page files
//             (performer-dashboard.js, studio-dashboard.js, and two more
//             about to be copy-pasted for group/tag) into one config-
//             driven engine now that a fourth copy made the duplication
//             the actual problem. detail-item-title/-value and the
//             <entity>-name spans get the same label-sans/value-mono/
//             lilac-name treatment via plain CSS in entity-dashboard.css
//             - no JS needed for that part, same as before.
// ==/StashScript==
;(() => {
  'use strict';

  console.log('[EntityDashboard] v1.0 loaded');

  /* ============================
   *  ENTITIES
   *  One entry per detail page this treatment applies to. Each entity's
   *  own `.nav-tabs` was confirmed live to use the identical
   *  Tab.Container/data-rb-event-key mechanism before being folded into
   *  this shared engine - not assumed from the class names alone. Only
   *  one entity's tabsRoot can ever match a given page (they're different
   *  routes), so `run()` below stops at the first match.
   * ============================ */
  const ENTITIES = [
    { id: 'performer', tabsRoot: '.performer-tabs', gateAttr: 'jlPerfReady' },
    { id: 'studio',    tabsRoot: '.studio-tabs',    gateAttr: 'jlStudioReady' },
    { id: 'group',     tabsRoot: '.group-tabs',     gateAttr: 'jlGroupReady' },
    { id: 'tag',       tabsRoot: '.tag-tabs',       gateAttr: 'jlTagReady' },
  ];

  /* ============================
   *  MODE BAR
   *  Driven by data-rb-event-key exactly like the scene page's own
   *  .nav-tabs - same click-proxying architecture: buttons drive stash's
   *  real nav links rather than touching React state, so Tab.Container
   *  stays the single source of truth for which pane is active.
   * ============================ */
  function navLinkFor(nav, key) {
    return nav.querySelector(`[data-rb-event-key="${key}"]`);
  }

  // Count badges are separate <span class="badge ..."> children, absent
  // entirely when a tab has no count (e.g. 0 galleries) - read the
  // badge's own text rather than parsing it out of the link's combined
  // textContent.
  function readTabs(nav) {
    return [...nav.querySelectorAll(':scope > a[data-rb-event-key]')].map(a => {
      const badge = a.querySelector('.badge');
      const clone = a.cloneNode(true);
      const badgeInClone = clone.querySelector('.badge');
      if (badgeInClone) badgeInClone.remove();
      return {
        key: a.dataset.rbEventKey,
        label: clone.textContent.trim(),
        count: badge ? badge.textContent.trim() : null,
      };
    });
  }

  function currentKey(nav) {
    const active = nav.querySelector('a.nav-link.active');
    return active ? active.dataset.rbEventKey : null;
  }

  function buildModeBar(nav) {
    if (!nav || !nav.parentElement) return false;
    const tabs = readTabs(nav);
    if (!tabs.length) return false;

    const signature = tabs.map(t => t.key + ':' + (t.count ?? '')).join('|');
    const existing = nav.parentElement.querySelector(':scope > .jl-modes-row');
    if (existing && existing.dataset.jlSignature === signature) return true;
    if (existing) existing.remove();

    const row = document.createElement('div');
    row.className = 'jl-modes-row';
    row.dataset.jlSignature = signature;

    const bar = document.createElement('div');
    bar.className = 'jl-modes';
    bar.setAttribute('role', 'tablist');

    for (const tab of tabs) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'jl-mode';
      btn.dataset.jlModeId = tab.key;
      btn.setAttribute('role', 'tab');

      const label = document.createElement('span');
      label.textContent = tab.label;
      btn.appendChild(label);

      if (tab.count !== null) {
        const count = document.createElement('span');
        count.className = 'jl-mode-count';
        count.textContent = tab.count;
        btn.appendChild(count);
      }

      btn.addEventListener('click', () => {
        const link = navLinkFor(nav, tab.key);
        if (link) link.click();
        requestAnimationFrame(() => syncModeBar(nav));
      });
      bar.appendChild(btn);
    }

    row.appendChild(bar);
    nav.parentElement.insertBefore(row, nav);
    return true;
  }

  function syncModeBar(nav) {
    const key = currentKey(nav);
    if (!key) return;
    for (const btn of nav.parentElement.querySelectorAll('.jl-mode')) {
      btn.setAttribute('aria-selected', String(btn.dataset.jlModeId === key));
    }
  }

  /* Switching tabs only toggles the `active` class - an attribute
   * mutation, which the childList observer below never sees. A WeakSet
   * (not a single module-level "current" observer, as the old per-page
   * files each had) tracks which nav elements already have their own
   * observer: simpler than manual disconnect/reconnect bookkeeping, and
   * correct for the same reason - a SPA navigation to a *different*
   * entity of the *same* type (e.g. tag A to tag B) produces a new `nav`
   * element, which is simply not yet in the set, so it gets its own
   * observer for free; the old element's observer has nothing left to
   * observe and is garbage-collected along with it. */
  const watchedNavs = new WeakSet();
  function watchNav(nav) {
    if (watchedNavs.has(nav)) return;
    watchedNavs.add(nav);
    /* childList + characterData as well as the class attribute (2026-09-04):
       SPA navigation to another entity of the same type REUSES this nav
       element — confirmed live, performer 8 → performer 476 — and React
       updates the count badges by rewriting their text nodes in place,
       which a childList-only observer never sees. Before the body observer
       below was filtered, unrelated grid mutations happened to re-run
       buildModeBar() and pick the new counts up; now this observer has to
       see them itself. buildModeBar() is signature-guarded, so a class
       flip that changes no count costs one string compare, and it writes
       only outside the nav (the row is the nav's sibling), so it cannot
       re-trigger itself. */
    new MutationObserver(() => {
      if (buildModeBar(nav)) syncModeBar(nav);
    }).observe(nav, {
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
      childList: true,
      characterData: true,
    });
  }

  /* ============================
   *  MAIN
   * ============================ */
  function run() {
    for (const entity of ENTITIES) {
      const nav = document.querySelector(`${entity.tabsRoot} .nav-tabs`);
      if (!nav) continue;
      if (!buildModeBar(nav)) continue;
      // Failsafe gate (constraint 6, CLAUDE.md): only hide the native nav
      // once the replacement pill bar has actually been built. If this
      // script throws before reaching here, stash renders exactly as it
      // does today. Each entity's own gate attribute name (never
      // `[data-jl-mode]`, the scene page's own) so none of them are ever
      // confused, even though only one is ever set on a given page.
      if (nav.dataset[entity.gateAttr] !== 'true') nav.dataset[entity.gateAttr] = 'true';
      watchNav(nav);
      syncModeBar(nav);
      return; // only one entity can ever match a given page
    }
  }

  /* Relevance filter (2026-09-04 review). run() only reads the entity
     page's own .nav-tabs (keys, labels, count badges — the active class is
     the nav observer's job), so a batch matters only on an entity route,
     and only if a record's target is inside that nav, or it adds a node
     that is or contains a .nav-tabs (page mount, or SPA navigation to
     another entity of the same type, which produces a fresh nav), or no
     nav exists yet. Everything else — the relations grid loading and
     scrolling under the tabs, which used to re-run readTabs()'s per-tab
     cloneNode on every frame — is dropped. */
  const ENTITY_ROUTE_RE = /^\/(performers|studios|groups|tags)\/\d+/;
  const NAV_SELECTOR = ENTITIES.map(e => `${e.tabsRoot} .nav-tabs`).join(', ');
  let lastPathname = location.pathname;
  function isRelevant(muts) {
    if (!ENTITY_ROUTE_RE.test(location.pathname)) return false;
    // A URL change is always worth one run: same-type SPA navigation keeps
    // the nav element (its in-place count updates are the per-nav
    // observer's job, see watchNav), but a fresh run here is the cheap
    // way to be sure nothing about the new page is missed.
    if (location.pathname !== lastPathname) { lastPathname = location.pathname; return true; }
    const nav = document.querySelector(NAV_SELECTOR);
    if (!nav) return true;
    for (const m of muts) {
      if (nav.contains(m.target)) return true;
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.contains(nav) || n.matches('.nav-tabs') || n.querySelector('.nav-tabs')) return true;
      }
    }
    return false;
  }
  let queued = false;
  const observer = new MutationObserver(muts => {
    if (queued) return;
    if (!isRelevant(muts)) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      try { run(); } catch (e) { console.error('[EntityDashboard]', e); }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
  run();
})();
