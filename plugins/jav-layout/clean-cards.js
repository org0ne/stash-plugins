// ==StashScript==
// name Clean Cards – Dracula + Popover Reorder
// version 5.5
// description Pink code, cyan performers, dynamic title scaling, 3-line clamp, batched GraphQL, popover reorder, watched badge, performer hover popup
// match *://*/scenes*
// run-at document-idle
// ==/StashScript==
;(() => {
  'use strict';

  console.log('[CleanCards] v5.5 loaded — performer hover popup active');

  /* ============================
   *  CSS
   * ============================ */
  /* CSS now lives in clean-cards.css, loaded by the plugin manifest. */

  /* ============================
   *  Popover Reordering
   * ============================ */

  const POPOVER_ORDER_PARSED = [
    ["performer-count"],
    ["count-button", "increment-only"],
    ["marker-count"],
    ["tag-count"],
    ["group-count", "tag-tooltip"],
    ["organized"],
    ["other-copies", "extra-scene-info"]
  ];

  function matchesClassSpec(el, classes) {
    if (classes.length === 1) return el.classList.contains(classes[0]);
    return classes.every(cls => el.classList.contains(cls));
  }

  /* Remove any anchor/overflow state a bar may have picked up and stop
   * watching it. Used for .performer-card bars, which must never get the
   * scene-card-only anchoring/clipping treatment below — see clearAnchorState
   * call sites in reorderPopoverBar. */
  function clearAnchorState(bar) {
    if (bar._stashRO) { bar._stashRO.disconnect(); bar._stashRO = null; }
    Array.from(bar.children).forEach(el => {
      el.style.removeProperty('margin-left');
      el.style.removeProperty('visibility');
    });
  }

  /* Push native buttons right, then hide any that overflow the bar.
   * Uses a single persistent ResizeObserver per bar so getBoundingClientRect()
   * is always post-layout, AND keeps tracking the bar across future resizes
   * (window resize, sidebar toggle, etc.) instead of measuring once and going stale.
   *
   * SCENE-CARD ONLY. Performer-card popover bars use CSS
   * `justify-content: stretch` + `flex: 1 1 0` so their buttons split the bar
   * evenly on their own — this function's `margin-left: auto` and
   * overflow-driven `visibility: hidden` actively fight that layout (breaks
   * centering, and can hide buttons during transient reflows like the
   * scene-tabs/scene-player draw or a window resize). Callers must route
   * performer-card bars to clearAnchorState() instead. */
  function anchorButtonsRight(bar) {
    const natives = Array.from(bar.children).filter(el => !el.classList.contains('stash-collection-pill'));

    // Reset all state from any prior call
    natives.forEach(el => {
      el.style.removeProperty('margin-left');
      el.style.removeProperty('visibility');
    });
    if (!natives[0]) return;
    natives[0].style.setProperty('margin-left', 'auto', 'important');

    const recompute = () => {
      const barRight = bar.getBoundingClientRect().right;
      let clipping = false;
      for (const el of natives) {
        if (clipping) {
          el.style.setProperty('visibility', 'hidden', 'important');
        } else {
          const elRight = el.getBoundingClientRect().right;
          if (elRight > barRight - 1) {
            clipping = true;
            el.style.setProperty('visibility', 'hidden', 'important');
          }
        }
      }
    };

    // Reuse a single long-lived RO per bar instead of creating/disconnecting
    // a new one on every call. The callback recomputes on every firing
    // (not just the first), so resizes that happen after the initial
    // layout (window resize, sidebar collapse, etc.) keep overflow state correct.
    if (!bar._stashRO) {
      bar._stashRO = new ResizeObserver(() => recompute());
      bar._stashRO.observe(bar);
    } else {
      // Bar is already being watched — just recompute against current layout.
      recompute();
    }
  }

  function reorderPopoverBar(bar) {
    if (!bar) return;

    // SCENE CARDS ONLY. Every other card type — performer, studio, tag,
    // gallery, image, group — keeps its native popover row untouched.
    // Reordering by POPOVER_ORDER_PARSED is a scene-card concept (those
    // class names only exist there), and the overflow clipping in
    // anchorButtonsRight() is tuned for a scene card's wide icon set:
    // on a studio card's short row it measured wrong and hid the whole
    // row (reported live 2026-09-04, "suppressing the default popover
    // buttons on studios"). The original exclusion covered performer
    // cards only, because those were the only other cards anyone had
    // looked at; the honest rule is the positive one. clearAnchorState()
    // still runs so a bar this code touched under an earlier version is
    // put back exactly as native left it.
    if (!bar.closest('.scene-card')) { clearAnchorState(bar); return; }

    const kids = Array.from(bar.children);
    const reorderKids = kids.filter(el => !el.classList.contains('stash-collection-pill'));
    if (!reorderKids.length) return;

    reorderKids.forEach((el, i) => {
      if (!el.dataset._stashPid) el.dataset._stashPid = String(i);
    });
    const sig = reorderKids.map(el => el.dataset._stashPid).join('|');
    if (bar.dataset._stashPopoverSig === sig) return;

    const used = new Set();
    const out = [];

    for (const classes of POPOVER_ORDER_PARSED) {
      const found = reorderKids.find(el => !used.has(el) && matchesClassSpec(el, classes));
      if (found) { used.add(found); out.push(found); }
    }
    for (const el of reorderKids) if (!used.has(el)) out.push(el);

    const changed = out.length === reorderKids.length && out.some((el, i) => el !== reorderKids[i]);
    if (!changed) {
      bar.dataset._stashPopoverSig = sig;
      anchorButtonsRight(bar);
      return;
    }

    const frag = document.createDocumentFragment();
    out.forEach(el => frag.appendChild(el));
    bar.appendChild(frag);

    bar.dataset._stashPopoverSig = out.map(el => el.dataset._stashPid).join('|');
    anchorButtonsRight(bar);
  }

  function reorderPopoversInCard(card) {
    const bar = card?.querySelector?.('.card-popovers.btn-group');
    if (!bar) return;
    requestAnimationFrame(() => reorderPopoverBar(bar));
  }

  const popoverObserver = new MutationObserver(muts => {
    const bars = new Set();
    for (const m of muts) {
      const t = m.target;
      if (!(t instanceof Element)) continue;
      const bar = t.matches('.card-popovers.btn-group') ? t : t.closest?.('.card-popovers.btn-group');
      if (bar) bars.add(bar);
    }
    if (!bars.size) return;
    requestAnimationFrame(() => bars.forEach(reorderPopoverBar));
  });

  let bootPending = [];
  let bootScheduled = false;
  const popoverBootObserver = new MutationObserver(muts => {
    for (const m of muts)
      for (const node of m.addedNodes)
        if (node instanceof Element) bootPending.push(node);

    // Schedule exactly one microtask per batch of pushed nodes, regardless of
    // how many nodes arrived in this callback invocation. The previous
    // `bootPending.length === 1` check only fired when exactly one node was
    // added in a single MutationObserver callback — but React commonly mounts
    // several `.card-popovers.btn-group` elements at once (e.g. a scene's
    // performer list), so that batch landed with length > 1 and the check
    // silently failed, leaving those bars un-observed and un-reordered forever.
    if (bootPending.length && !bootScheduled) {
      bootScheduled = true;
      queueMicrotask(() => {
        bootScheduled = false;
        const nodes = bootPending.splice(0);
        for (const node of nodes) {
          // Same scene-card-only scope as reorderPopoverBar(): bars on
          // other card types are never registered with the live observer,
          // so they cost nothing and can never be touched by a later pass.
          if (node.matches?.('.scene-card .card-popovers.btn-group')) {
            popoverObserver.observe(node, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
            reorderPopoverBar(node);
          } else {
            node.querySelectorAll?.('.scene-card .card-popovers.btn-group').forEach(bar => {
              popoverObserver.observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
              reorderPopoverBar(bar);
            });
          }
        }
      });
    }
  });

  popoverBootObserver.observe(document.body, { childList: true, subtree: true });

  document.querySelectorAll('.scene-card .card-popovers.btn-group').forEach(bar => {
    popoverObserver.observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
    reorderPopoverBar(bar);
  });

  /* ============================
   *  STUDIO CODE → SUBHEADER (Scene Detail Page)
   * ============================ */

  function relocateStudioCode() {
    const detailHeadings = document.querySelectorAll('.scene-details h6');
    let codeValue = null;
    let codeNode = null;

    for (const h6 of detailHeadings) {
      const text = h6.textContent.trim();
      if (text.startsWith('Studio Code:')) {
        codeValue = text.slice('Studio Code:'.length).trim();
        codeNode = h6;
        break;
      }
    }
    if (!codeValue) return; // nothing new to move (or already moved)

    const headerContainer = document.querySelector('.scene-header-container');
    const sceneHeader = headerContainer?.querySelector('.scene-header');
    if (!headerContainer || !sceneHeader) return;

    codeNode.remove();

    // clear any stale copy from a previous scene
    headerContainer.querySelectorAll('.studio-code').forEach(el => el.remove());

    const codeEl = document.createElement('div');
    codeEl.className = 'studio-code';

    const codeText = document.createElement('span');
    codeText.className = 'studio-code-text';
    codeText.textContent = codeValue;
    codeEl.appendChild(codeText);

    // Copy button: owned entirely by the separate copy-buttons plugin,
    // which appends its own button after .studio-code-text when present —
    // this file only builds the relocated text element, deliberately
    // independent of that plugin (see the collection-pill comment in
    // applySceneDataToCard() below for the same pattern).
    headerContainer.insertBefore(codeEl, sceneHeader);
  }


  function relocateOriginalTitle() {
    const detailsContainer = document.querySelector('.scene-details');
    if (!detailsContainer) return;

    // Already injected for this scene render — nothing to do.
    if (detailsContainer.querySelector('.stash-original-title')) return;

    const valueEl = document.querySelector('.detail-item.custom-field-original-title .detail-item-value');
    if (!valueEl) return;

    const text = valueEl.textContent.trim();
    if (!text) return;

    const h6 = document.createElement('h6');
    h6.className = 'stash-original-title';
    h6.textContent = `Original Title: ${text}`;

    detailsContainer.insertBefore(h6, detailsContainer.firstChild);
  }

  function reorderSceneDetailFields() {
    const container = document.querySelector('.scene-details');
    if (!container) return;

    const items = Array.from(container.children).filter(el => el.tagName === 'H6');
    if (items.length < 2) return;

    function priority(el) {
      if (el.classList.contains('stash-original-title')) return 0;
      const text = el.textContent.trim();
      if (text.startsWith('Director:')) return 1;
      if (text.startsWith('Created At:')) return 2;
      if (text.startsWith('Updated At:')) return 3;
      return 4;
    }

    const sorted = [...items].sort((a, b) => priority(a) - priority(b));
    const changed = sorted.some((el, i) => el !== items[i]);
    if (!changed) return;

    const frag = document.createDocumentFragment();
    sorted.forEach(el => frag.appendChild(el));
    container.appendChild(frag);
  }

  function reformatDetailPageDate() {
    const dateEl = document.querySelector('.scene-subheader .date');
    if (!dateEl) return;

    const iso = dateEl.getAttribute('data-value');
    if (!iso) return;

    if (dateEl.textContent.trim() !== iso) dateEl.textContent = iso;
  }      


    // Reentrancy-guarded like scene-dashboard.js's own body observer: without
    // `scheduled`, a burst of several body mutations (React commonly mounts
    // more than one node per batch) queued one requestAnimationFrame call
    // *per mutation callback invocation*, each running this same four-function
    // pass redundantly in the same frame. Every one of those four functions is
    // already individually idempotent (each checks whether its own work is
    // still needed before touching the DOM — see their own comments), so this
    // was wasted reads, not a correctness bug, but the guard collapses a
    // burst to one pass per frame for free, the same win it gives
    // scene-dashboard.js.
    let studioCodeScheduled = false;
    const studioCodeObserver = new MutationObserver(() => {
        if (studioCodeScheduled) return;
        studioCodeScheduled = true;
        requestAnimationFrame(() => {
          studioCodeScheduled = false;
          relocateStudioCode();
          relocateOriginalTitle();
          reorderSceneDetailFields();
          reformatDetailPageDate();
        });
      });
      studioCodeObserver.observe(document.body, { childList: true, subtree: true });
      relocateStudioCode();
      relocateOriginalTitle();
      reorderSceneDetailFields();
      reformatDetailPageDate();


  /* ============================
   *  BATCHED GRAPHQL INFRA
   * ============================ */

  // Bounded LRU, not an unbounded cache — every scene ever seen in a
  // session used to stay cached forever (performers, paths, all of it),
  // which is fine for a normal browsing session but grows without limit
  // over a long one on a large library. A plain Map already preserves
  // insertion order, which is the standard trick for LRU-on-a-Map: `get`
  // deletes-then-re-sets the hit key so it moves to the end (most
  // recently used), and `set` evicts from the front (`keys().next()`,
  // the oldest/least-recently-used entry) once the cap is exceeded.
  // 4000 is arbitrary but generous — thousands of scenes' worth of
  // performer/path data, comfortably covering a long single-session
  // browse without growing unbounded; bump it if a library's own paging
  // routinely blows past it.
  const SCENE_CACHE_LIMIT = 4000;
  const sceneCache = new Map();

  function sceneCacheGet(id) {
    if (!sceneCache.has(id)) return undefined;
    const value = sceneCache.get(id);
    sceneCache.delete(id);
    sceneCache.set(id, value);
    return value;
  }

  function sceneCacheSet(id, scene) {
    sceneCache.delete(id);
    sceneCache.set(id, scene);
    if (sceneCache.size > SCENE_CACHE_LIMIT) {
      sceneCache.delete(sceneCache.keys().next().value);
    }
  }

  let highPriorityQueue = new Set();
  let lowPriorityQueue = new Set();
  const waitingCards = new Map();
  const BATCH_SIZE = 100;
  let batchTimeout = null;

  function requestSceneData(sceneId, callback, isVisible = false) {
    if (!sceneId) return;
    if (sceneCache.has(sceneId)) { callback(sceneCacheGet(sceneId)); return; }
    if (!waitingCards.has(sceneId)) waitingCards.set(sceneId, []);
    waitingCards.get(sceneId).push(callback);
    (isVisible ? highPriorityQueue : lowPriorityQueue).add(sceneId);
    if (!batchTimeout) batchTimeout = setTimeout(runBatchQuery, 50);
  }

  async function runBatchQuery(retryIds = null) {
    if (!retryIds) batchTimeout = null;
    const batch = retryIds || [];

    if (!retryIds) {
      for (const id of highPriorityQueue) { if (batch.length >= BATCH_SIZE) break; batch.push(id); highPriorityQueue.delete(id); }
      for (const id of lowPriorityQueue)  { if (batch.length >= BATCH_SIZE) break; batch.push(id); lowPriorityQueue.delete(id); }
    }

    if (batch.length === 0) return;

    const query = `{
      ${batch.map(id => `
        s${id}: findScene(id: ${id}) {
          id code date
          performers { id name disambiguation image_path }
          play_count play_duration resume_time
          files { path }
        }
      `).join('\n')}
    }`;

    try {
      const res = await fetch('/graphql', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query })
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      const data = json?.data || {};

      for (const key in data) {
        const scene = data[key];
        if (!scene) continue;
        const id = String(scene.id);
        sceneCacheSet(id, scene);
        if (waitingCards.has(id)) { for (const cb of waitingCards.get(id)) cb(scene); waitingCards.delete(id); }
      }
      for (const id of batch) {
        if (!sceneCache.has(id) && waitingCards.has(id)) { waitingCards.get(id).forEach(cb => cb(null)); waitingCards.delete(id); }
      }
    } catch (err) {
      console.error("CleanCards Batch Error:", err);
      if (!retryIds) {
        console.warn(`CleanCards: retrying ${batch.length} scenes in 2s`);
        setTimeout(() => runBatchQuery(batch), 2000);
      } else {
        for (const id of batch) {
          if (waitingCards.has(id)) { waitingCards.get(id).forEach(cb => cb(null)); waitingCards.delete(id); }
        }
      }
    }

    if (!retryIds && (highPriorityQueue.size || lowPriorityQueue.size))
      batchTimeout = setTimeout(runBatchQuery, 50);
  }

  // Performer name/disambiguation copy buttons: owned entirely by the
  // separate copy-buttons plugin now — this file no longer builds or
  // reorders anything on .performer-card/.performer-head beyond what's
  // already elsewhere in this file (watched badge, etc.).

  /* ============================
   *  VISIBILITY PRIORITY
   * ============================ */

  const visibilityObserver = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const card = entry.target;
      const sceneId = card.dataset.sceneId;
      if (!sceneId) continue;
      if (entry.isIntersecting) {
        card.dataset.visible = "true";
        if (lowPriorityQueue.has(sceneId)) {
          lowPriorityQueue.delete(sceneId);
          highPriorityQueue.add(sceneId);
          if (!batchTimeout) batchTimeout = setTimeout(runBatchQuery, 20);
        }
      } else {
        card.dataset.visible = "false";
      }
    }
  }, { threshold: 0.1 });

  /* ============================
   *  CARD ENHANCEMENT
   * ============================ */

  const FLAG_PARTIAL_WATCH = false;

  /* ── Performer name hover popup ──────────────────────────────
   * A single reusable popup element (appended to <body> so it can
   * escape the scene-card's overflow:hidden and stacking context).
   * Shown/positioned relative to whichever performer <a> is hovered. */

  let perfPopupEl = null;
  let perfPopupHideTimer = null;
  let perfPopupCurrentAnchor = null;

  function getPerfPopupEl() {
    if (perfPopupEl && document.body.contains(perfPopupEl)) return perfPopupEl;
    perfPopupEl = document.createElement('div');
    perfPopupEl.className = 'stash-performer-popup';

    const arrow = document.createElement('div');
    arrow.className = 'stash-performer-popup-arrow';

    const img = document.createElement('img');
    img.alt = '';

    const name = document.createElement('div');
    name.className = 'stash-performer-popup-name';
    const nameText = document.createElement('span');
    nameText.className = 'stash-performer-popup-name-text';
    const disambig = document.createElement('span');
    disambig.className = 'stash-performer-popup-disambig';
    name.appendChild(nameText);
    name.appendChild(disambig);

    perfPopupEl.appendChild(arrow);
    perfPopupEl.appendChild(img);
    perfPopupEl.appendChild(name);
    document.body.appendChild(perfPopupEl);
    return perfPopupEl;
  }

  function positionPerfPopup(popup, anchor) {
    const margin = 8;
    const arrowGap = 7;
    const rect = anchor.getBoundingClientRect();
    const popRect = popup.getBoundingClientRect();

    // Always open below the name, matching Stash's native popover.
    const top = rect.bottom + arrowGap;

    // Center horizontally on the anchor, clamped to the viewport.
    let left = rect.left + (rect.width / 2) - (popRect.width / 2);
    left = Math.max(margin, Math.min(left, window.innerWidth - popRect.width - margin));

    popup.style.top = `${Math.round(top)}px`;
    popup.style.left = `${Math.round(left)}px`;

    // Keep the arrow pointing at the anchor's center even when the popup
    // itself has been shifted to stay on-screen.
    const anchorCenterX = rect.left + rect.width / 2;
    const maxOffset = (popRect.width / 2) - 14;
    const offset = Math.max(-maxOffset, Math.min(maxOffset, anchorCenterX - (left + popRect.width / 2)));
    const arrow = popup.querySelector('.stash-performer-popup-arrow');
    if (arrow) arrow.style.left = `${Math.round(offset)}px`;
  }

  function showPerformerPopup(anchor, performer) {
    if (!performer) return;
    // Touch-only devices (iOS included) get no popup at all. iOS Safari has
    // no real hover: a tap on a hover-reactive link fires an *emulated*
    // mouseenter (showing the popup) and then, because that handler visibly
    // mutated the DOM, withholds the click — while mouseleave only ever
    // fires if a later tap happens to land on another hoverable element,
    // and frequently never fires at all. Net effect, reported live from a
    // real iPhone (2026-09-02): first tap summons the popup, and no
    // interaction can ever dismiss it. mouseleave being the popup's only
    // dismiss path is fine on devices with a real cursor and wrong on ones
    // without, so gate on the (hover: hover) media query — checked at call
    // time, not cached, since convertibles (iPad gaining a trackpad,
    // laptops docking) can change it mid-session. On hover-less devices a
    // performer tap then just navigates on the first tap, stock behavior.
    if (window.matchMedia && !window.matchMedia('(hover: hover)').matches) return;
    clearTimeout(perfPopupHideTimer);
    perfPopupCurrentAnchor = anchor;

    const popup = getPerfPopupEl();
    const img = popup.querySelector('img');
    const nameText = popup.querySelector('.stash-performer-popup-name-text');
    const disambig = popup.querySelector('.stash-performer-popup-disambig');

    if (performer.image_path) {
      img.src = performer.image_path;
      img.style.display = '';
    } else {
      img.removeAttribute('src');
      img.style.display = 'none';
    }
    nameText.textContent = performer.name || '';
    disambig.textContent = performer.disambiguation ? `(${performer.disambiguation})` : '';

    popup.classList.remove('visible');
    // Position before making visible (using previous size) then reposition
    // on next frame once layout (e.g. image aspect) has settled.
    positionPerfPopup(popup, anchor);
    requestAnimationFrame(() => {
      if (perfPopupCurrentAnchor !== anchor) return;
      positionPerfPopup(popup, anchor);
      popup.classList.add('visible');
    });
  }

  function hidePerformerPopup(anchor) {
    if (perfPopupCurrentAnchor !== anchor) return;
    clearTimeout(perfPopupHideTimer);
    perfPopupHideTimer = setTimeout(() => {
      if (perfPopupEl) perfPopupEl.classList.remove('visible');
      perfPopupCurrentAnchor = null;
    }, 60);
  }

  // Unconditional, immediate variant — no anchor equality check, no 60ms
  // debounce. The anchor-gated hide above exists for the mouseenter/
  // mouseleave dance between adjacent names; this one is for "whatever is
  // showing, kill it now" dismissal paths that don't belong to any anchor.
  function hidePerformerPopupNow() {
    clearTimeout(perfPopupHideTimer);
    if (perfPopupEl) perfPopupEl.classList.remove('visible');
    perfPopupCurrentAnchor = null;
  }

  // Hybrid devices (a touchscreen laptop, an iPad with a trackpad) report
  // (hover: hover) — their *primary* pointer hovers — so they pass the gate
  // in showPerformerPopup, but a finger tapped on the screen still fires
  // the same emulated mouseenter with the same never-a-mouseleave problem
  // the gate exists to prevent. Any touch that isn't on the popup's own
  // anchor dismisses it immediately; a touch on the anchor is left alone so
  // tap-to-navigate isn't preceded by a flicker. Capture phase so no
  // stopPropagation() anywhere in the tree can starve it; passive since it
  // never needs preventDefault. On pure-touch devices this is dead code
  // (the gate means nothing ever shows) — it's here for the hybrids only.
  document.addEventListener('touchstart', e => {
    if (!perfPopupCurrentAnchor) return;
    if (perfPopupCurrentAnchor.contains(e.target)) return;
    hidePerformerPopupNow();
  }, { passive: true, capture: true });

  // Exposed so scene-dashboard.js's sidebar performer list (a different
  // file, loaded after this one — see dracula-layout.yml) can reuse this
  // exact popup instead of duplicating it. One shared <body>-level popup
  // element either way; only the trigger differs (scene-card grid tile vs.
  // sidebar name link). hideNow is exposed for the same reason, for any
  // consumer needing the unconditional immediate dismiss.
  window.JLPerformerPopup = { show: showPerformerPopup, hide: hidePerformerPopup, hideNow: hidePerformerPopupNow };

  function applySceneDataToCard(card, scene) {
    if (!document.contains(card)) return;
    if (!scene) return;

    const codeSpan = card._stashCodeSpan;
    const dateSpan = card._stashDateSpan;
    const perf     = card._stashPerf;

    if (codeSpan) codeSpan.textContent = (scene.code || "—").trim();
    if (dateSpan) dateSpan.textContent = scene.date ? scene.date.slice(0, 10) : "—";

    if (perf) {
      perf.textContent = "";
      if (scene.performers?.length) {
        const frag = document.createDocumentFragment();
        scene.performers.forEach((p, i) => {
          const a = document.createElement("a");
          a.href = `/performers/${p.id}`;
          a.textContent = p.name;
          a.addEventListener("click", e => e.stopPropagation());
          a.addEventListener("mouseenter", () => showPerformerPopup(a, p));
          a.addEventListener("mouseleave", () => hidePerformerPopup(a));
          frag.appendChild(a);
          if (i < scene.performers.length - 1) {
            const dot = document.createElement("span");
            dot.textContent = "·";
            dot.style.opacity = "0.4";
            dot.style.margin = "0 6px";
            frag.appendChild(dot);
          }
        });
        perf.appendChild(frag);
      } else {
        perf.textContent = "—";
      }
    }

    // Collection pill: owned entirely by the separate collection-colors
    // plugin (auto-discovers Library paths instead of a hardcoded map),
    // including its own popover-priority/overflow-hide handling — this
    // plugin is deliberately independent of that one and knows nothing
    // about it beyond excluding its pill by class name (see
    // anchorButtonsRight()/reorderPopoverBar() above) so this file's own
    // native-button reordering doesn't touch or reorder it.

    // ── Watched flag ─────────────────────────────────────────────
    // Until 2026-09-04 this also drew a `.watched-badge` check icon over
    // the thumbnail (`.video-section`), positioned by the user's own
    // Custom CSS. Removed on request in favour of an explicit "✔ Watched"
    // text badge — currently rendered in the scene page's sidebar by
    // scene-dashboard.js via window.JLSceneData below; the card's own
    // placement is still an open question (see CLAUDE.md). The class and
    // data flag stay so CSS can still key off a watched card.
    if (!card.dataset.stashWatched && isSceneWatched(scene)) {
      card.classList.add('watched');
      card.dataset.stashWatched = "true";
      // The card's own indicator (chosen 2026-09-04, "option 2"): a small
      // check glyph in the code/date bar, immediately before the date. A
      // glyph rather than the sidebar's "✔ Watched" text because the bar
      // has no spare width on a narrow card, and unlike the popover row
      // nothing here is ever hidden by the overflow-priority logic, so it
      // is always visible. Same green as the sidebar badge (--jl-ok).
      // WATCHED_CHECK_HOST picks which half of the bar it lives in: 'code'
      // appends it after the studio code (and its copy button), 'date'
      // puts it immediately before the date. Trying 'code' 2026-09-04.
      const group = WATCHED_CHECK_HOST === 'code' ? card._stashCodeGroup : card._stashDateGroup;
      if (group && !group.querySelector('.stash-watched-check')) {
        const check = document.createElement('span');
        check.className = 'stash-watched-check';
        check.setAttribute('title', 'Watched');
        check.setAttribute('aria-label', 'Watched');
        check.appendChild(makeWatchedCheckIcon());
        if (WATCHED_CHECK_HOST === 'code') group.appendChild(check);
        else group.insertBefore(check, card._stashDateSpan);
      }
    }
  }

  /* The check itself is an inline SVG, not the ✔ glyph: the glyph's weight
   * is whatever the fallback symbol font happens to draw, and at 12px it
   * read as a speck (reported live, 2026-09-04, "easy to miss"). A stroked
   * path has a weight that is actually ours to set — 3 units on a 16-unit
   * box — and scales with the surrounding font-size via 1em sizing. Shared
   * with the sidebar badge through window.JLSceneData so both checks are
   * the same mark. */
  const WATCHED_CHECK_HOST = 'code'; // 'code' | 'date' — see applySceneDataToCard
  const SVG_NS = 'http://www.w3.org/2000/svg';
  function makeWatchedCheckIcon() {
    const svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 16 16');
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    const path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', 'M2.5 8.5 L6 12 L13.5 4');
    path.setAttribute('fill', 'none');
    path.setAttribute('stroke', 'currentColor');
    path.setAttribute('stroke-width', '3');
    path.setAttribute('stroke-linecap', 'round');
    path.setAttribute('stroke-linejoin', 'round');
    svg.appendChild(path);
    return svg;
  }

  /* One definition of "watched", shared with the sidebar badge: played at
   * least once, playback not left mid-way (resume_time 0 — FLAG_PARTIAL_WATCH
   * would count partial plays too), and at least 10s of play time so a
   * mis-click doesn't count. */
  function isSceneWatched(scene) {
    return !!scene &&
      scene.play_count > 0 &&
      (scene.resume_time === 0 || (FLAG_PARTIAL_WATCH && scene.resume_time > 0)) &&
      scene.play_duration >= 10;
  }

  /* Fresh single-scene fetch of just the watched-state fields, for the scene
   * page's sidebar badge. Deliberately NOT routed through the card grid's
   * batched sceneCache: that cache is session-long, and the one place a
   * stale watched flag is actually visible is the scene you just finished
   * watching. One small query per scene-page load is the honest cost. */
  async function fetchSceneWatchData(sceneId) {
    const res = await fetch('/graphql', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      // Aliased on purpose, like the card grid's batched query: another
      // installed plugin (stashUserscriptLibrary) hooks every GraphQL
      // response and walks `data.findScene.performers`, so an un-aliased
      // findScene that doesn't select performers throws inside THEIR code
      // on every scene page (seen live, 2026-09-04). Under an alias the
      // hook never sees a `findScene` key and leaves the response alone.
      body: JSON.stringify({ query: `{ s: findScene(id: ${Number(sceneId)}) { id play_count play_duration resume_time } }` }),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    return json?.data?.s || null;
  }

  // Exposed for scene-dashboard.js (loads after this file), same pattern as
  // window.JLPerformerPopup: one definition of watched-ness, one fetch.
  window.JLSceneData = { isWatched: isSceneWatched, fetch: fetchSceneWatchData, checkIcon: makeWatchedCheckIcon };

  function enhanceCard(card) {
    if (card.dataset.stashClean) return;

    const link = card.querySelector('a[href^="/scenes/"]');
    const titleContainer = card.querySelector(".card-section-title");
    if (!link || !titleContainer) return;

    const sceneId = link.href.match(/\/scenes\/(\d+)/)?.[1];
    if (!sceneId) return;

    card.dataset.sceneId = sceneId;

    const titleEl = titleContainer.querySelector('.TruncatedText:not(.scene-card__description)');
    if (titleEl) titleEl.style.removeProperty('-webkit-line-clamp');

    const bar = document.createElement("div");
    bar.className = "stash-code-date";

    const codeGroup = document.createElement("span");
    codeGroup.className = "code-group";

    const codeSpan = document.createElement("span");
    codeSpan.className = "code";
    codeSpan.textContent = "…";
    codeGroup.appendChild(codeSpan);

    const copyBtn = document.createElement("button");
    copyBtn.type = "button";
    copyBtn.className = "stash-code-copy";
    copyBtn.title = "Copy studio code";
    copyBtn.setAttribute("aria-label", "Copy studio code");
    const copyIcon = document.createElement("i");
    copyIcon.className = "fa-solid fa-copy";
    copyBtn.appendChild(copyIcon);

    copyBtn.addEventListener("click", e => {
      e.stopPropagation();
      e.preventDefault();
      const value = codeSpan.textContent.trim();
      if (!value || value === "—" || value === "…") return;
      navigator.clipboard.writeText(value).then(() => {
        copyBtn.classList.add("copied");
        copyIcon.className = "fa-solid fa-check";
        setTimeout(() => {
          copyBtn.classList.remove("copied");
          copyIcon.className = "fa-solid fa-copy";
        }, 1200);
      }).catch(err => console.error('CleanCards: copy failed', err));
    });

    codeGroup.appendChild(copyBtn);

    const dateSpan = document.createElement("span");
    dateSpan.className = "date";
    dateSpan.textContent = "…";

    // Date sits in its own group so the watched check (added later by
    // applySceneDataToCard, only for watched scenes) can be inserted
    // right before it while the bar stays a two-item space-between row.
    const dateGroup = document.createElement("span");
    dateGroup.className = "date-group";
    dateGroup.appendChild(dateSpan);

    bar.appendChild(codeGroup);
    bar.appendChild(dateGroup);
    // Inserted as a sibling of the title's own <a> (one level up, inside
    // .card-section directly) rather than inside it alongside title/
    // performers — .card-section > a has overflow:hidden with zero slack
    // beyond its own content width (confirmed live: its box exactly
    // matches title's width, no margin/padding of its own), so this
    // bar's deliberate `width: calc(100% + 10px)` overshoot (see
    // clean-cards.css) was getting clipped flush square, corners and
    // all, when it lived in there. .card-section itself has real room
    // to spare (14px padding, confirmed live) and its own overflow
    // boundary sits well outside the bar's new position. perf/title stay
    // exactly where they were, inside the <a> — only the bar moved.
    titleContainer.closest('a').before(bar);

    const perf = document.createElement("div");
    perf.className = "stash-performers";
    perf.textContent = "…";
    titleContainer.before(perf);

    card._stashCodeSpan = codeSpan;
    card._stashDateSpan = dateSpan;
    card._stashDateGroup = dateGroup;
    card._stashCodeGroup = codeGroup;
    card._stashPerf     = perf;

    visibilityObserver.observe(card);
    requestSceneData(sceneId, scene => applySceneDataToCard(card, scene), false);
    reorderPopoversInCard(card);

    card.dataset.stashClean = "true";
  }

  /* ============================
   *  MUTATION OBSERVER
   * ============================ */

  const observer = new MutationObserver(mutations => {
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        if (node.matches?.(".scene-card")) enhanceCard(node);
        else node.querySelectorAll?.(".scene-card").forEach(enhanceCard);
      }
    }
  });

  observer.observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll(".scene-card").forEach(enhanceCard);

})();