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

  /* Balanced rows when the bar wraps (phones — entity-dashboard.css makes
     the tabs grow to fill their row below 576px). Flexbox breaks lines
     greedily, so five tabs at 390 points went 4 + 1, the lone fifth tab
     stretched across a whole row; reported from an iPhone 2026-09-06.
     Measured, not assumed: the break items are removed, the natural row
     count is read from the tabs' tops (one forced layout), and if it is
     more than one, zero-height full-width `.jl-break` items are inserted
     so the tabs split as evenly as the natural row count allows — 5 → 3+2,
     6 → 3+3, 7 → 4+3, 7 in three rows → 3+2+2. A row that still cannot
     hold its share (very long labels) simply wraps once more. Desktop
     bars fit one row, so nothing is inserted there. Runs after a build
     and on resize; never from the per-nav observer's class flips alone,
     where buildModeBar() is signature-guarded and returns quickly. */
  function balanceWrap(box) {
    if (!box) return;
    box.querySelectorAll(':scope > .jl-break').forEach(b => b.remove());
    const items = [...box.children].filter(el => el.getBoundingClientRect().width > 0);
    if (items.length < 3) return;
    const rows = new Set(items.map(t => Math.round(t.getBoundingClientRect().top))).size;
    if (rows < 2) return;
    const base = Math.floor(items.length / rows);
    const extra = items.length % rows;
    let idx = 0;
    for (let r = 0; r < rows - 1; r++) {
      idx += base + (r < extra ? 1 : 0);
      const brk = document.createElement('span');
      brk.className = 'jl-break';
      brk.setAttribute('aria-hidden', 'true');
      box.insertBefore(brk, items[idx]);
    }
  }
  function balanceRows(nav) {
    balanceWrap(nav.parentElement.querySelector(':scope > .jl-modes-row > .jl-modes'));
    balanceViewingPill();
  }

  /* The entity pages' viewing pill (Edit / Auto tag… / Merge… / Submit /
     Delete) is React's own flex container and gets the same balancing:
     at 430 points it broke 4 + 1 with Delete alone across the second
     row. Break items are foreign nodes React leaves alone, but React can
     re-render the pill (entering edit mode turns it into the fixed
     .col-xl-9 bar and back), which would strand breaks inside the bar's
     flex layout — so every pass first strips breaks from ANY .details-edit
     and only re-balances the viewing form. watchPill() re-runs it on the
     header's own childList changes, which is where those re-renders
     land; the body observer above is nav-filtered and never sees them. */
  function balanceViewingPill() {
    document.querySelectorAll('.details-edit > .jl-break').forEach(b => b.remove());
    balanceWrap(document.querySelector('.details-edit:not(.col-xl-9)'));
  }
  /* stash's phone footer pager (`.pagination-footer-container`, position:
     sticky; bottom: 48.75px) clamps to the TOP of its pane for as long as
     the pane's top edge is below the sticky line — the ~100px of scroll
     in which an entity page's list first rises into view from below. The
     top pager now sits on that same edge (buttons.css §4c-m moved it up
     beside the filter toggle), so for that stretch the two drew on top of
     each other — seen in a headless capture; the reporting iPhone
     screenshot was taken past it. While the top pager is on screen the
     footer copy is redundant anyway, so it is hidden for exactly that
     span. List pages proper never hit this (their pane starts at the top
     of the page), so this is wired up here, for entity pages only. */
  const watchedPagers = new WeakSet();
  function setFooterHidden(indexContainer, hidden) {
    const pane = indexContainer.closest('.sidebar-pane-content');
    const footer = pane && pane.querySelector(':scope > .pagination-footer-container');
    if (footer) footer.classList.toggle('jl-footer-pager-hidden', hidden);
  }
  const pagerIO = typeof IntersectionObserver === 'undefined' ? null : new IntersectionObserver(entries => {
    for (const e of entries) setFooterHidden(e.target, e.isIntersecting);
  });
  function watchPagers() {
    if (!pagerIO) return;
    document.querySelectorAll('.sidebar-pane-content > .pagination-index-container').forEach(el => {
      if (!watchedPagers.has(el)) { watchedPagers.add(el); pagerIO.observe(el); }
      // Re-apply the current state every scan: a tab switch can keep this
      // element (React reuses it) while replacing the footer beside it, and
      // an observer only speaks when the intersection CHANGES — measured
      // live, the fresh footer stayed visible under an on-screen top pager.
      const r = el.getBoundingClientRect();
      setFooterHidden(el, r.bottom > 0 && r.top < window.innerHeight);
    });
  }
  /* Switching tabs swaps the pane (new pager containers) after the nav's
     class flip, once data arrives — so the tabs root is watched for
     childList changes and re-runs the cheap watchPagers() per frame. */
  const watchedRoots = new WeakSet();
  let paneQueued = false;
  function watchPane(root) {
    if (!root || watchedRoots.has(root)) return;
    watchedRoots.add(root);
    new MutationObserver(() => {
      if (paneQueued) return;
      paneQueued = true;
      requestAnimationFrame(() => { paneQueued = false; try { watchPagers(); } catch (e) { console.error('[EntityDashboard]', e); } });
    }).observe(root, { childList: true, subtree: true });
  }

  const watchedHeaders = new WeakSet();
  let pillQueued = false;
  function watchPill() {
    const header = document.querySelector('.detail-header');
    if (!header || watchedHeaders.has(header)) return;
    watchedHeaders.add(header);
    new MutationObserver(muts => {
      if (pillQueued) return;
      // Our own break insertions are childList mutations too; ignore batches that are only those.
      if (!muts.some(m => [...m.addedNodes, ...m.removedNodes].some(n => !(n.classList && n.classList.contains('jl-break'))))) return;
      pillQueued = true;
      requestAnimationFrame(() => { pillQueued = false; try { balanceViewingPill(); } catch (e) { console.error('[EntityDashboard]', e); } });
    }).observe(header, { childList: true, subtree: true });
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
      if (buildModeBar(nav)) { syncModeBar(nav); balanceRows(nav); }
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
      watchPill();
      watchPane(nav.closest(entity.tabsRoot));
      watchPagers();
      syncModeBar(nav);
      balanceRows(nav);
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

  // Row balance depends on width alone, which no DOM mutation announces.
  let resizeQueued = false;
  window.addEventListener('resize', () => {
    if (resizeQueued) return;
    resizeQueued = true;
    requestAnimationFrame(() => {
      resizeQueued = false;
      const nav = document.querySelector(NAV_SELECTOR);
      if (nav) { try { balanceRows(nav); } catch (e) { console.error('[EntityDashboard]', e); } }
    });
  });

  run();
})();
