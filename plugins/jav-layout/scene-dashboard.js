// ==StashScript==
// name Scene Dashboard (Option C)
// version 2.0.1
// description Reorders the scene page sidebar into one persistent-context
//             column (identity, description, tags) with a mode switcher
//             below it, then Performers/File/History swapping in per mode.
// ==/StashScript==
;(() => {
  'use strict';

  console.log('[SceneDashboard] v2.0.1 loaded');

  /* ============================
   *  CONFIG
   *  Card titles are hard-coded rather than read from stash's locale
   *  bundle, so change them here if you run a non-English UI.
   * ============================ */
  const CARD_TITLES = {
    fileinfo:    'File',
    history:     'History',
    groups:      'Groups',
    galleries:   'Galleries',
    metadata:    'Metadata',
    description: 'Details',
    tags:        'Tags',
    customfields: 'Custom Fields',
  };

  const MODES = [
    { id: 'browse',  label: 'Browse',  key: 'scene-details-panel'      },
    { id: 'queue',   label: 'Queue',   key: 'scene-queue-panel'        },
    { id: 'markers', label: 'Markers', key: 'scene-markers-panel'      },
    { id: 'filters', label: 'Filters', key: 'scene-video-filter-panel' },
    { id: 'edit',    label: 'Edit',    key: 'scene-edit-panel'         },
  ];

  // Panes that appear together under Browse. Order here is documentation;
  // the actual reading order lives in scene-dashboard.css.
  const BROWSE_PANES = ['details', 'fileinfo', 'history', 'groups', 'galleries'];

  const STORAGE_KEY = 'jl.collapsedCards';

  /* ============================
   *  GUARDED DOM WRITERS
   *
   *  Every attribute/style/class write this file makes on every
   *  tagPanes() run goes through one of these, and they only touch the
   *  DOM when the value actually changes. Two reasons, both measured
   *  (CDP profile, 2026-09-02):
   *
   *  1. An attribute set to the value it already holds still queues a
   *     MutationObserver record and still invalidates style for every
   *     selector that keys off that attribute — which is most of
   *     scene-dashboard.css. During video playback the player's own
   *     ~70 mutations/s were being amplified into ~1,500 attribute
   *     rewrites/s inside .scene-tabs by unguarded dataset/style/aria
   *     writes, and ~50 extra style recalcs/s along with them.
   *
   *  2. Forced synchronous layout only costs anything when layout is
   *     dirty. If a run writes nothing, its getBoundingClientRect() reads
   *     (sizeTagsBackdrop/markTagRows/sizeCodeDateBackdrop) either hit a
   *     clean layout for free or trigger the one layout the browser owed
   *     the page anyway — instead of the ~3 forced layouts per run the
   *     unguarded write→read→write pattern used to cost.
   *
   *  `value == null` on setData means "remove the attribute", so callers
   *  can express a boolean flag as setData(el, key, on ? 'true' : null).
   * ============================ */
  // Returns true if it actually wrote — markTagRows() uses that to know
  // whether its layout inputs may have moved and a re-measure is due.
  function setData(el, key, value) {
    if (value == null) {
      if (!(key in el.dataset)) return false;
      delete el.dataset[key];
      return true;
    }
    if (el.dataset[key] === value) return false;
    el.dataset[key] = value;
    return true;
  }
  function setAttr(el, name, value) {
    if (el.getAttribute(name) !== value) el.setAttribute(name, value);
  }
  function setStyle(el, prop, value) {
    if (el.style[prop] !== value) el.style[prop] = value;
  }
  function addClass(el, cls) {
    if (!el.classList.contains(cls)) el.classList.add(cls);
  }
  function toggleClass(el, cls, on) {
    if (el.classList.contains(cls) !== on) el.classList.toggle(cls, on);
  }

  /* ============================
   *  PANE IDENTIFICATION
   *
   *  react-bootstrap strips eventKey before rendering Tab.Pane, and stash's
   *  Tab.Container has no id, so the panes carry no key we can read. Nav
   *  links do — AbstractNavItem writes data-rb-event-key — but panes have to
   *  be fingerprinted by the markup their panel component emits.
   *
   *  Checked in order; first match wins. Everything is verified against
   *  ui/v2.5/src/components/Scenes/SceneDetails on develop.
   * ============================ */
  const SIGNATURES = [
    ['fileinfo',  p => p.classList.contains('file-info-panel') || !!p.querySelector('.scene-file-info')],
    ['details',   p => !!p.querySelector('.scene-details')],
    ['history',   p => !!p.querySelector('.play-history')],
    ['markers',   p => !!p.querySelector('.scene-markers-panel')],
    ['galleries', p => !!p.querySelector('.scene-galleries')],
    ['filters',   p => !!p.querySelector('.scene-video-filter')],
    ['queue',     p => !!p.querySelector('.queue-controls')],
    ['edit',      p => !!p.querySelector('.edit-buttons-container')],
    // SceneGroupPanel renders a bare `div.row.justify-content-center` with no
    // distinguishing class, so it is identified last, by shape.
    ['groups',    p => {
      const only = p.children.length === 1 ? p.firstElementChild : null;
      return !!only && only.classList.contains('row') && only.classList.contains('justify-content-center');
    }],
  ];

  function identifyPane(pane) {
    for (const [slug, test] of SIGNATURES) {
      try { if (test(pane)) return slug; } catch (e) { /* markup moved on */ }
    }
    return null;
  }

  /* ============================
   *  COLLAPSE STATE
   * ============================ */
  function loadCollapsed() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveCollapsed(set) {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify([...set])); } catch (e) { /* private mode */ }
  }

  let collapsed = loadCollapsed();

  /* ============================
   *  CARD HEADERS
   * ============================ */
  function applyCollapsed(host, cardId) {
    const isOn = collapsed.has(cardId);
    toggleClass(host, 'jl-collapsed', isOn);
    const head = host.querySelector(':scope > .jl-head');
    if (head) setAttr(head, 'aria-expanded', String(!isOn));
  }

  /* Appends a header to `host`. Append rather than prepend: React never sees
   * this node, so it will never try to remove it, and CSS `order: -1` keeps it
   * visually first no matter where stash's own re-renders leave it. */
  function ensureHead(host, cardId, title) {
    if (!host || host.querySelector(':scope > .jl-head')) return;

    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'jl-head';
    head.dataset.jlCard = cardId;

    const label = document.createElement('span');
    label.textContent = title;
    head.appendChild(label);

    const chev = document.createElement('span');
    chev.className = 'jl-chevron';
    chev.textContent = '▼';
    chev.setAttribute('aria-hidden', 'true');
    head.appendChild(chev);

    head.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      if (collapsed.has(cardId)) collapsed.delete(cardId); else collapsed.add(cardId);
      saveCollapsed(collapsed);
      applyCollapsed(host, cardId);
    });

    host.appendChild(head);
    applyCollapsed(host, cardId);
  }

  /* Mirrors the count badge from a hidden nav tab (`.badge` inside the
   * link with the given data-rb-event-key) into a card head as
   * `.jl-head-count`, inserted between the label and the chevron. Read
   * from the nav's own DOM every run — the count is React-owned and can
   * change (files added/removed) — but every write is equality-guarded:
   * this runs from inside the body-wide observer (see the guarded-
   * writers note near the top of this file). No badge → no count span;
   * a stale span is removed rather than emptied so CSS never has to
   * special-case an empty pill. */
  function syncHeadCount(root, host, eventKey) {
    const head = host.querySelector(':scope > .jl-head');
    if (!head) return;
    const badge = root.querySelector(`.nav-tabs [data-rb-event-key="${eventKey}"] .badge`);
    const value = badge ? badge.textContent.trim() : '';
    let count = head.querySelector(':scope > .jl-head-count');
    if (!value) {
      if (count) count.remove();
      return;
    }
    if (!count) {
      count = document.createElement('span');
      count.className = 'jl-head-count';
      const chev = head.querySelector(':scope > .jl-chevron');
      head.insertBefore(count, chev || null);
    }
    if (count.textContent !== value) count.textContent = value;
  }

  /* File/History/Groups/Galleries are each one real element, so `ensureHead`
   * can toggle a class on that one host to hide its children. Metadata,
   * Details and Tags aren't: their "card" is several scattered siblings
   * (a native <h6>/<span> React renders, plus values this script injects)
   * that share a parent with OTHER content that must stay visible (Original
   * Title lives in the same parent as the Metadata fields but must not be
   * part of that card; Performers/Custom Fields share a parent with Details
   * and Tags). There's no single host to toggle, so the collapsed flag lives
   * on `.scene-tabs` itself as `data-jl-collapsed-<groupId>`, and every
   * member of the group is addressed by its own existing selector in CSS
   * rather than a `:not(.jl-head)` child rule. */
  function applyGroupCollapsed(root, groupId, head) {
    const isOn = collapsed.has(groupId);
    setAttr(root, `data-jl-collapsed-${groupId}`, String(isOn));
    if (head) setAttr(head, 'aria-expanded', String(!isOn));
  }

  function ensureGroupHead(root, parent, groupId, title) {
    if (!parent || parent.querySelector(`:scope > .jl-head[data-jl-card="${groupId}"]`)) {
      applyGroupCollapsed(root, groupId, parent && parent.querySelector(`:scope > .jl-head[data-jl-card="${groupId}"]`));
      return;
    }

    const head = document.createElement('button');
    head.type = 'button';
    head.className = 'jl-head jl-head-group';
    head.dataset.jlCard = groupId;

    const label = document.createElement('span');
    label.textContent = title;
    head.appendChild(label);

    const chev = document.createElement('span');
    chev.className = 'jl-chevron';
    chev.textContent = '▼';
    chev.setAttribute('aria-hidden', 'true');
    head.appendChild(chev);

    head.addEventListener('click', e => {
      e.preventDefault();
      e.stopPropagation();
      if (collapsed.has(groupId)) collapsed.delete(groupId); else collapsed.add(groupId);
      saveCollapsed(collapsed);
      applyGroupCollapsed(root, groupId, head);
      // Tags is the one group whose members' own layout (the backdrop's
      // height, which chips count as "first row") depends on whether they're
      // actually visible when measured. sizeTagsBackdrop() (and the row
      // marking inside it) already runs once during tagPanes(), but if that
      // run landed while collapsed — page loaded with Tags collapsed, chips
      // all `display:none` — every chip measured to the same zeroed rect,
      // so every one of them looks like "row 1" and the backdrop measured a
      // near-zero cloud height. Toggling the attribute alone doesn't fix
      // stale measurements taken under the wrong visibility, so re-measure
      // now that the click has changed it.
      if (groupId === 'tags') sizeTagsBackdrop(parent);
    });

    parent.appendChild(head);
    applyGroupCollapsed(root, groupId, head);
  }

  /* Tags is the one scattered-group card whose members (the chips) must NOT
   * get the shared `background: var(--jl-well)` treatment — that would
   * either paint over their native pill colors (if applied directly to
   * `.tag-item`) or, applied to some wrapping wrapper, hit the exact same
   * unsolvable problem as everywhere else in this card: there's no single
   * element to hang one background on, and per-chip backgrounds leave
   * visible seams at the gaps between chips and between wrapped lines
   * (worse than Metadata's version of this problem, since chips wrap
   * irregularly rather than each claiming a full line).
   *
   * The fix is a real element after all — `.jl-tags-backdrop`, appended
   * once and reused, sized in JS every run to exactly the chip cloud's
   * current bounding box. It's a normal in-flow flex sibling (order
   * between the head and the chips, `flex-basis: 100%`) so it doesn't need
   * position:absolute/z-index games: it's given an explicit height for one
   * layout pass so the flex algorithm accounts for it, then an equal and
   * opposite negative margin-bottom that hands that vertical space straight
   * back — net zero effect on where the chips that follow end up, but the
   * backdrop itself still paints at its full height. Because it's earlier
   * in DOM order than the chips, normal same-stacking-context paint order
   * (later DOM siblings paint over earlier ones) puts the chips on top of
   * it with no z-index needed. */
  /* Flexbox has no ":first-of-line" or ":last-of-line" selector, so CSS
   * alone can't tell a chip that starts or ends a wrapped row apart from
   * one sandwiched between neighbours on the same line — needed for two
   * unrelated reasons: row 1 must cancel the container's row-gap above it
   * (later rows must let it through, see .tag-item's own comment) and every
   * row's outermost chips need a bigger edge margin than the gap between
   * two ordinary chips, to line up with the 14px inset every other card's
   * content uses (see the margin comment on .tag-item below). Only
   * measuring actual layout can answer either question, so this walks the
   * chips in DOM order (== visual left-to-right, top-to-bottom order for a
   * wrapped flex row) comparing each one's measured top to its neighbours':
   * a top that differs from the previous chip's starts a new row, one that
   * differs from the next chip's ends one. Row membership is a horizontal
   * (main-axis) decision the flex algorithm makes independent of the
   * vertical/horizontal margins these attributes drive, so it's safe to
   * measure before either margin is corrected below. */
  function markTagRows(tagItems) {
    if (!tagItems.length) return;
    // The flags this writes change the chips' own margins (row-start/end
    // chips get 14px instead of 8px on their outer side — see
    // scene-dashboard.css), and a wider margin on the last chip of a line
    // can push it onto the next line — which moves the row boundaries the
    // flags were computed from. So this is a fixed-point iteration, not a
    // single pass: measure, flag, and if any flag changed, measure again
    // against the layout those flags produced. Converges in two passes in
    // practice (a chip that wraps takes its wider margin with it and
    // nothing else moves); capped at three so a pathological cloud can't
    // loop. Before the 2026-09-02 observer filter this convergence
    // happened by accident, one pass per unrelated re-run — a fresh load
    // then occasionally froze one row's start chip at the narrow margin,
    // a real (if subtle) visual bug the constant re-runs had been hiding.
    for (let pass = 0; pass < 3; pass++) {
      const tops = [...tagItems].map(item => item.getBoundingClientRect().top);
      let changed = false;
      tagItems.forEach((item, i) => {
        const isFirstRow = Math.abs(tops[i] - tops[0]) < 2;
        const isRowStart = i === 0 || Math.abs(tops[i] - tops[i - 1]) >= 2;
        const isRowEnd = i === tagItems.length - 1 || Math.abs(tops[i + 1] - tops[i]) >= 2;
        // Guarded writes (see setData): in steady state every chip already
        // carries the right flags, so this loop writes nothing — which is
        // what keeps the reads above from forcing a fresh layout on every
        // run once the cloud has settled.
        changed = setData(item, 'jlTagFirstRow', isFirstRow ? 'true' : null) || changed;
        changed = setData(item, 'jlTagRowStart', isRowStart ? 'true' : null) || changed;
        changed = setData(item, 'jlTagRowEnd', isRowEnd ? 'true' : null) || changed;
      });
      if (!changed) break;
    }
  }

  /* One seamless background behind Studio Code and Subheader/Date, which
   * share a line (see scene-dashboard.css) but — being two separate native
   * React elements with no shared parent — have no single element to paint
   * a joined fill on. Same underlying problem and same fix as
   * sizeTagsBackdrop() below: an appended backdrop, reserved via a height
   * plus equal-and-opposite negative margin-bottom (net zero flow
   * contribution), so Code and Date's own `margin-top: -10px` (canceling
   * the row-gap above them, see their CSS) pulls them up to sit exactly on
   * top of it. Height is measured, not assumed — Code's pill and Date's
   * pill can render at very slightly different heights depending on
   * content/font metrics, and using the taller of the two avoids a sliver
   * of the shorter one poking out past the backdrop's edge. */
  function sizeCodeDateBackdrop(root) {
    const wrapper = root.querySelector(':scope > div:first-child');
    if (!wrapper) return;
    const code = wrapper.querySelector('.studio-code');
    const subheader = wrapper.querySelector('.scene-subheader');
    let backdrop = wrapper.querySelector(':scope > .jl-codedate-backdrop');
    if (!code || !subheader) {
      if (backdrop) setStyle(backdrop, 'display', 'none');
      return;
    }
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'jl-codedate-backdrop';
      wrapper.appendChild(backdrop);
    }
    setStyle(backdrop, 'display', '');
    // No reset-to-zero before measuring (an earlier version did one):
    // the two heights read here are Code's and Subheader's own intrinsic
    // box heights, which don't depend on the backdrop's current height
    // at all — and the reset itself was a style write that dirtied layout
    // right before the reads, forcing a synchronous reflow on every
    // single run. Reads first, then guarded writes, so a run whose inputs
    // haven't changed touches nothing.
    const height = Math.round(Math.max(
      code.getBoundingClientRect().height,
      subheader.getBoundingClientRect().height,
    ));
    // Both zero means the sidebar itself isn't laid out right now (e.g.
    // the scene-divider collapsed it) — keep the last good value rather
    // than writing a 0px bar that stays wrong until something re-measures.
    if (height === 0) return;
    setStyle(backdrop, 'height', `${height}px`);
    setStyle(backdrop, 'marginBottom', `-${height}px`);
  }

  /* Badge block: one combined "Resolution | fps | Director" line, above a
   * second line holding just the collection-colors badge slot — both in
   * Metadata's column at order 40, the position the collection-colors
   * plugin's own pill used to occupy on its own, back when that plugin
   * built this content itself. Moved here because none of it is actually
   * collection data — it's scene metadata dracula-layout already extracts
   * anyway (for the Framerate/Resolution mirrors and the native Director
   * row just above in tagPanes()), so re-deriving it a second time via a
   * separate plugin's own GraphQL fetch was duplicated, fragile work.
   *
   * `.jl-scene-badge-slot` is deliberately left empty here — a stable,
   * documented insertion point external plugins (collection-colors)
   * can find and drop a badge/pill into, without dracula-layout needing
   * to know anything about what such a plugin does. Collapses to nothing
   * (`:empty { display: none }` in CSS) when unused, so this whole row
   * costs zero layout space if no such plugin is installed.
   *
   * Resolution/fps/Director are joined as "segments" with a `|` divider
   * placed only *between* two segments that both exist — not a fixed
   * template string — so a scene missing Director (the only routinely-
   * absent one of the three) never leaves a dangling separator with
   * nothing after it. Director's segment is the real native `<a>` cloned
   * from Metadata's own Director row (cloneNode(true)), not rebuilt from
   * text — that link already carries the correct director-filter href,
   * and cloning a real DOM node adds one React has never seen (constraint
   * 1: safe — nothing is moved, the original stays exactly where
   * Metadata put it). A plain rebuilt `<a href="...">` would need to
   * reconstruct that filter URL by hand and would drift the moment
   * stash's own URL format changes; cloning can't drift because it's
   * stash's own link.
   *
   * The whole row is rebuilt from scratch on every call (simplest way to
   * keep text-segment/separator/link count in sync when Director
   * appears or disappears) — guarded behind a signature comparison
   * (`jlInfoSig`) so a run whose inputs haven't actually changed skips
   * the rebuild entirely. Without this, `replaceChildren()` on every
   * tagPanes() call would be an unconditional childList mutation the
   * body-wide MutationObserver watches for, on every run *including*
   * ones that write triggered — the exact permanent-busy-loop landmine
   * documented on the Framerate/Resolution mirrors above.
   *
   * Reuses `subheader`'s already-queried `.frame-rate`/`.resolution` and
   * `metaCol`'s already-labeled Director `<h6>` — both already computed
   * by the caller a few lines up — rather than re-querying independently. */
  function buildSceneBadgeRow(metaCol, subheader) {
    let block = metaCol.querySelector(':scope > .jl-scene-badges');
    if (!block) {
      block = document.createElement('div');
      block.className = 'jl-scene-badges';
      metaCol.appendChild(block);
    }

    const resolution = subheader?.querySelector('.resolution')?.textContent.trim();
    const frameRate = subheader?.querySelector('.frame-rate')?.textContent.trim();
    const directorSource = metaCol.querySelector(':scope > h6[data-jl-label="Director"] a');

    let infoRow = block.querySelector(':scope > .jl-scene-info-row');
    if (!infoRow) {
      infoRow = document.createElement('div');
      infoRow.className = 'jl-scene-info-row';
      block.insertBefore(infoRow, block.firstChild);
    }
    const sig = JSON.stringify([resolution || '', frameRate || '', directorSource ? directorSource.href + '|' + directorSource.textContent : '']);
    if (infoRow.dataset.jlInfoSig !== sig) {
      infoRow.dataset.jlInfoSig = sig;
      infoRow.replaceChildren();
      const segments = [];
      if (resolution) segments.push(document.createTextNode(resolution));
      if (frameRate) segments.push(document.createTextNode(frameRate));
      if (directorSource) {
        // Label + link travel together as one segment (one DocumentFragment),
        // not two — the "|" separator logic below only runs *between*
        // segments, so this keeps "Director: Name" from ever getting its
        // own dividing pipe wedged inside it.
        const frag = document.createDocumentFragment();
        const label = document.createElement('span');
        label.className = 'jl-scene-director-label';
        label.textContent = 'Director: ';
        frag.appendChild(label);
        const link = directorSource.cloneNode(true);
        link.classList.add('jl-scene-director-value');
        frag.appendChild(link);
        segments.push(frag);
      }
      segments.forEach((seg, i) => {
        if (i > 0) {
          const sep = document.createElement('span');
          sep.className = 'jl-scene-info-sep';
          sep.textContent = '|';
          infoRow.appendChild(sep);
        }
        infoRow.appendChild(seg);
      });
    }

    let row = block.querySelector(':scope > .jl-scene-badges-row');
    if (!row) {
      row = document.createElement('div');
      row.className = 'jl-scene-badges-row';
      const slot = document.createElement('div');
      slot.className = 'jl-scene-badge-slot';
      row.appendChild(slot);
      block.appendChild(row);
    }
  }

  /* "✔ Watched" text badge on the collection pill's line (`.jl-scene-
   * badges-row`, built by buildSceneBadgeRow above), right-justified via
   * CSS. Requested 2026-09-04 as the explicit, durable replacement for the
   * check icon clean-cards.js used to draw over the card thumbnail.
   * Watched-ness and the fetch both come from window.JLSceneData
   * (clean-cards.js) so there is exactly one definition of "watched".
   * Fetched once per scene id — the row remembers which scene it asked
   * about — because this runs on every tagPanes() pass; the async result
   * lands later and is written equality-guarded, and a scene that isn't
   * watched gets no element at all rather than an empty one. */
  function syncWatchedBadge(metaCol) {
    const row = metaCol.querySelector(':scope > .jl-scene-badges > .jl-scene-badges-row');
    if (!row || !window.JLSceneData) return;
    const match = location.pathname.match(/^\/scenes\/(\d+)/);
    const sceneId = match ? match[1] : null;
    if (!sceneId || row.dataset.jlWatchedScene === sceneId) return;
    row.dataset.jlWatchedScene = sceneId;
    window.JLSceneData.fetch(sceneId).then(scene => {
      const live = metaCol.querySelector(':scope > .jl-scene-badges > .jl-scene-badges-row');
      if (!live || live.dataset.jlWatchedScene !== sceneId) return;   // navigated away meanwhile
      const watched = window.JLSceneData.isWatched(scene);
      let badge = live.querySelector(':scope > .jl-watched-badge');
      if (!watched) { if (badge) badge.remove(); return; }
      if (!badge) {
        badge = document.createElement('span');
        badge.className = 'jl-watched-badge';
        badge.setAttribute('title', 'Watched');
        const check = document.createElement('span');
        check.className = 'jl-watched-check';
        check.setAttribute('aria-hidden', 'true');
        // Same stroked SVG mark as the card's code/date bar (clean-cards.js
        // makeWatchedCheckIcon), so the two checks are one mark, not a glyph
        // whose weight depends on the fallback symbol font.
        check.appendChild(window.JLSceneData.checkIcon());
        const label = document.createElement('span');
        label.textContent = 'Watched';
        badge.appendChild(check);
        badge.appendChild(label);
        live.appendChild(badge);
      }
    }).catch(e => console.warn('[SceneDashboard] watched badge', e));
  }

  function sizeTagsBackdrop(contentCol) {
    let backdrop = contentCol.querySelector(':scope > .jl-tags-backdrop');
    if (!backdrop) {
      backdrop = document.createElement('div');
      backdrop.className = 'jl-tags-backdrop';
      contentCol.appendChild(backdrop);
    }

    // No reset-to-zero before measuring (an earlier version zeroed
    // height/margin-bottom first): the backdrop's height and its equal-
    // and-opposite negative margin-bottom net to zero flow contribution
    // by construction, so neither its own `top` nor the last chip's
    // `bottom` below depends on whatever height the previous run left
    // behind. The reset was a pure write→read→write pattern costing a
    // forced synchronous layout per run for nothing — see the guarded-
    // writers comment near the top of this file for the measurements.
    const tagItems = contentCol.querySelectorAll(':scope > .tag-item');
    if (!tagItems.length) {
      setStyle(backdrop, 'display', 'none');
      return;
    }
    // Chips that are display:none (Tags collapsed, or any non-Browse
    // mode — both hide .tag-item and the backdrop via CSS) all measure to
    // an empty rect, and flagging every one of them as "row 1" / sizing
    // the backdrop off that would be garbage. Leave the last good
    // measurement in place instead; CSS already hides the backdrop in
    // both of those states, and the next run in a visible state (the
    // Tags head's own click handler, or syncModeBar's mode-change
    // re-measure) replaces it. An earlier version hid the backdrop
    // inline here — no longer needed, and it would be one more write
    // per run.
    const firstRect = tagItems[0].getBoundingClientRect();
    if (firstRect.width === 0 && firstRect.height === 0) return;

    setStyle(backdrop, 'display', '');
    markTagRows(tagItems);

    const top = backdrop.getBoundingClientRect().top;
    const bottom = tagItems[tagItems.length - 1].getBoundingClientRect().bottom;
    // +10 gives the cloud breathing room below the last chip before the
    // backdrop's own bottom border, matching the padding-bottom every other
    // card gets from its own CSS (this one's is baked into the measurement
    // instead, since the backdrop has no content of its own to pad around).
    const height = Math.max(0, Math.round(bottom - top) + 10);
    // Collapsed tags (or, in principle, anything else that zeroes every
    // chip's rect) lands here at height 0 — a 0-height div would still
    // render its own 1px border-bottom as a stray floating line, so hide it
    // outright instead of trusting height alone to make it disappear.
    if (height === 0) {
      setStyle(backdrop, 'display', 'none');
      return;
    }
    setStyle(backdrop, 'height', `${height}px`);
    setStyle(backdrop, 'marginBottom', `-${height}px`);
  }

  /* Strips a literal leading label off an element's text, e.g. turning
   * "Original Title: foo" into "foo" so CSS can style the value without the
   * redundant label. Mutates the existing Text node's `.nodeValue` in place
   * rather than reassigning `.textContent` (which would destroy that Text
   * node and create a new one) — React holds a reference to the original
   * Text node and calls `.nodeValue = ...` on it directly when the
   * underlying value changes, the same operation this does, so the
   * reference stays valid and a later React update still lands correctly.
   * Idempotent: a re-run after stripping is a harmless no-op. */

  /* Builds a compact performer name list — .stash-performers, the same
   * class the scene-card grid tiles use (clean-cards.css/js): cyan links,
   * dot-separated, single nowrap+ellipsis line, hover popup with photo/
   * name/disambiguation. Placed in the persistent header, between the
   * Code/Date bar and Title (order: 15 in scene-dashboard.css), matching
   * the scene-card grid tile's own DOM order there. Requested live
   * 2026-09-01, in two steps: first as a straight replacement for the
   * native Performer section's own big-card grid ("match the scene-card
   * performers list, same functionality, look, and feel"), then corrected
   * ("keep the native performer display in the scene sidebar and move the
   * performer names between the code-date bar and the title") — so this is
   * purely additive now. The native grid (.scene-performers, further down
   * in tagPanes(), still Browse-only) is untouched; this list is a second,
   * always-visible view built from that same native markup's own data.
   *
   * No extra GraphQL fetch needed (unlike the scene-card grid's own
   * version, which fetches because its card doesn't render full performer
   * data): the native .performer-card markup already carries id (its own
   * link href), name, disambiguation and image — this just reads it back
   * out. The hover popup itself is reused via window.JLPerformerPopup
   * (exposed by clean-cards.js) rather than duplicated here.
   *
   * Rebuild is fingerprinted on the id list, not run unconditionally —
   * same self-triggering-mutation-loop risk documented on the Framerate/
   * Resolution mirrors above: replacing the list's children is a childList
   * mutation the body-wide MutationObserver watches, so an unconditional
   * rebuild on every tagPanes() run would loop at 60fps once the observer
   * saw its own write. */
  function buildSidebarPerformerList(contentCol, scenePerformers) {
    const performers = Array.from(scenePerformers.querySelectorAll(':scope > .performer-card')).map(card => {
      const link = card.querySelector('.thumbnail-section a[href^="/performers/"]');
      const nameEl = card.querySelector('.performer-name');
      if (!link || !nameEl) return null;
      const disambigEl = card.querySelector('.performer-disambiguation');
      // Native text is "(foo)" (with a leading space from the surrounding
      // markup, trimmed below) — stripped here so showPerformerPopup's own
      // `(${performer.disambiguation})` formatting doesn't double the
      // parens.
      const disambigRaw = disambigEl ? disambigEl.textContent.trim() : '';
      const img = card.querySelector('.performer-card-image');
      return {
        id: link.getAttribute('href').split('/').pop(),
        name: nameEl.textContent.trim(),
        disambiguation: disambigRaw.replace(/^\(|\)$/g, ''),
        image_path: img ? img.src : null,
      };
    }).filter(Boolean);

    let list = contentCol.querySelector(':scope > .jl-sidebar-performers');
    if (!list) {
      list = document.createElement('div');
      list.className = 'stash-performers jl-sidebar-performers';
      contentCol.appendChild(list);
    }

    const fingerprint = performers.map(p => p.id).join(',');
    if (list.dataset.jlFingerprint === fingerprint) return;
    list.dataset.jlFingerprint = fingerprint;

    list.textContent = '';
    if (!performers.length) {
      list.textContent = '—';
      return;
    }
    const frag = document.createDocumentFragment();
    performers.forEach((p, i) => {
      const a = document.createElement('a');
      a.href = `/performers/${p.id}`;
      a.textContent = p.name;
      a.addEventListener('mouseenter', () => window.JLPerformerPopup?.show(a, p));
      a.addEventListener('mouseleave', () => window.JLPerformerPopup?.hide(a));
      frag.appendChild(a);
      if (i < performers.length - 1) {
        const dot = document.createElement('span');
        dot.textContent = '·';
        dot.style.opacity = '0.4';
        dot.style.margin = '0 6px';
        frag.appendChild(dot);
      }
    });
    list.appendChild(frag);
  }

  function stripLeadingLabel(el, label) {
    const node = el.firstChild;
    if (node && node.nodeType === Node.TEXT_NODE && node.nodeValue.startsWith(label)) {
      node.nodeValue = node.nodeValue.slice(label.length);
    }
  }

  /* Splits a "Label: value" h6 into a `data-jl-label` attribute (for CSS to
   * render via `::before`, styled like File/History's <dt>) plus the bare
   * value left behind (styled like their <dd>).
   *
   * Not as simple as reading `firstChild`: React renders `{label}:{' '}
   * {value}` as *separate* text nodes rather than one merged string — for
   * "Created At: December 10, 2022 9:39 PM" that's five nodes: "Created At",
   * ":", " ", the date, and a trailing " ". So this does a read-only scan
   * across the leading text nodes first to find which one actually contains
   * the colon, and only mutates once that's confirmed — bailing untouched if
   * no colon turns up, or if a non-text child (Director's value is a link)
   * is reached first.
   *
   * Every node this empties is strictly *before* the colon, i.e. part of the
   * label, e.g. "Created At" / "Director" — always a hardcoded string in
   * stash's JSX, never derived from scene data. React therefore never
   * revisits it after mount (its diff sees the same literal on every
   * re-render and skips the DOM write), so emptying it is permanent and
   * safe. Everything from the colon onward, value included, is left
   * exactly as React rendered it. */
  function splitLabelValue(h6) {
    const nodes = [...h6.childNodes];
    let acc = '';
    let boundaryNode = null;
    let boundaryIdx = -1;
    for (const node of nodes) {
      if (node.nodeType !== Node.TEXT_NODE) return;
      const idx = node.nodeValue.indexOf(':');
      if (idx === -1) { acc += node.nodeValue; continue; }
      boundaryNode = node;
      boundaryIdx = idx;
      break;
    }
    if (!boundaryNode) return;
    const label = acc + boundaryNode.nodeValue.slice(0, boundaryIdx);
    for (const node of nodes) {
      if (node === boundaryNode) break;
      node.nodeValue = '';
    }
    boundaryNode.nodeValue = boundaryNode.nodeValue.slice(boundaryIdx + 1).replace(/^\s+/, '');
    if (h6.dataset.jlLabel !== label) h6.dataset.jlLabel = label;
  }

  const MONTH_NUMBERS = {
    january: 1, february: 2, march: 3, april: 4, may: 5, june: 6,
    july: 7, august: 8, september: 9, october: 10, november: 11, december: 12,
  };

  /* Reformats Created At / Updated At's value from stash's native
   * "December 10, 2022 9:39 PM" down to "2022-12-10" — mutating the value
   * Text node's `.nodeValue` in place, the same technique splitLabelValue
   * and stripLeadingLabel use, and for the same reason: this node is
   * React-owned (constraint 1), so reassigning `.textContent` would
   * destroy it and desync React's reference.
   *
   * Self-guarding, not flag-guarded: the regex only matches stash's native
   * "Month D, YYYY ..." shape, so a second call after a successful reformat
   * (now "2022-12-10", no leading month name) simply fails to match and
   * no-ops — no `data-jl-*` flag needed, unlike splitLabelValue (which
   * guards explicitly because a *value* containing its own colon, e.g. the
   * "9:39" this strips away, would otherwise be misread as a label on a
   * second pass). That self-guarding is also what makes this safe to call
   * on every tagPanes() run: if React later overwrites this same node with
   * a new native-format timestamp (e.g. after an edit bumps Updated At),
   * the regex matches again and the next run reformats it again. */
  function formatDateValue(h6) {
    const node = [...h6.childNodes].find(n => n.nodeType === Node.TEXT_NODE && n.nodeValue.trim());
    if (!node) return;
    const m = node.nodeValue.match(/^\s*([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})\b/);
    if (!m) return;
    const month = MONTH_NUMBERS[m[1].toLowerCase()];
    if (!month) return;
    const mm = String(month).padStart(2, '0');
    const dd = String(m[2]).padStart(2, '0');
    node.nodeValue = `${m[3]}-${mm}-${dd}`;
  }

  /* ============================
   *  TAGGING
   * ============================ */
  function tagPanes(root) {
    const content = root.querySelector(':scope > .tab-content');
    if (!content) return null;

    const seen = {};
    for (const pane of content.querySelectorAll(':scope > .tab-pane')) {
      let slug = pane.dataset.jlPane;
      if (!slug) {
        slug = identifyPane(pane);
        if (!slug) continue;
        pane.dataset.jlPane = slug;
      }
      seen[slug] = pane;

      if (BROWSE_PANES.includes(slug)) {
        setData(pane, 'jlBrowse', 'true');
        // Details is flattened (display:contents in CSS), not boxed, so it
        // never gets the card chrome the other Browse panes get.
        if (slug !== 'details') addClass(pane, 'jl-card');
      }
    }

    // Details is flattened rather than boxed: its pieces get spread across
    // the page next to header items and the mode switcher (see
    // scene-dashboard.css), so each piece needs its own data-jl-item tag
    // rather than sharing one card wrapper. Original Title has a stable
    // class already (`.stash-original-title`); the rest are bare <h6>
    // headings distinguished only by their text, matched the same way
    // CARD_TITLES accepts hard-coded English — revisit if the UI language
    // changes.
    const details = seen.details;
    if (details) {
      const metaCol = details.querySelector('.scene-details');
      if (metaCol) {
        const scenedetails = [];
        for (const h6 of metaCol.querySelectorAll(':scope > h6')) {
          if (h6.classList.contains('stash-original-title')) {
            stripLeadingLabel(h6, 'Original Title: ');
            continue;
          }
          // Skip our own injected Framerate/Resolution mirrors (below) —
          // this loop runs on every tagPanes() call and would otherwise
          // find them here too on the second run onward and try to treat
          // them as native fields (splitLabelValue on an already-plain
          // "Framerate"/"Resolution" label finds no colon and silently
          // no-ops, but formatDateValue's date-field check doesn't match
          // them either way, so the real risk is just re-adding this same
          // h6 to `scenedetails` twice — once here, once below).
          if (h6.dataset.jlMirror === 'true') continue;
          setData(h6, 'jlItem', 'scenedetails');
          // Guard: this h6's text is never reset between runs, so a second
          // call here would re-scan the *value* left over from the first
          // split — and a value like "December 10, 2022 9:39 PM" contains
          // its own colon, which would get misread as a second label.
          if (!h6.dataset.jlLabel) {
            splitLabelValue(h6);
            // "Created At" / "Updated At" read as noise next to a bare date
            // (no time-of-day left to justify "At" once formatDateValue
            // below drops it) — shortened once, right where the label is
            // first captured, same idempotency guard as splitLabelValue
            // itself. The date-field check below matches the shortened
            // form for exactly that reason: this branch only ever runs
            // once per h6, so every later run sees "Created"/"Updated"
            // already, never the native "... At" text.
            if (h6.dataset.jlLabel === 'Created At') h6.dataset.jlLabel = 'Created';
            else if (h6.dataset.jlLabel === 'Updated At') h6.dataset.jlLabel = 'Updated';
          }
          if (h6.dataset.jlLabel === 'Created' || h6.dataset.jlLabel === 'Updated') {
            formatDateValue(h6);
          }
          scenedetails.push(h6);
        }

        // Framerate/Resolution are mirrored in from .scene-subheader
        // (hidden there in CSS, now that Studio Code has moved onto its
        // line — see scene-dashboard.css) rather than moved: there's only
        // one .scene-subheader and it must stay where it natively renders
        // for constraint 1, so this copies its values into new h6s instead.
        // Unlike the native fields above, these are entirely our own
        // elements — no splitLabelValue() needed, since there's no native
        // "Label: value" text to strip; the label is set directly and the
        // value is checked every run to catch upstream changes (a scene's
        // resolution/framerate can change if its source file is replaced).
        //
        // PERFORMANCE LANDMINE: `h6.textContent = value` unconditionally,
        // even when value hasn't changed, silently created a permanent
        // 60fps busy-loop. `.textContent =` always removes+reinserts a
        // child text node (per spec, regardless of whether the new value
        // equals the old one) — a childList mutation, which the body-wide
        // observer at the bottom of this file watches for. So every
        // tagPanes() run (even one triggered by this exact write) queued
        // another requestAnimationFrame → another run() → another
        // unconditional write → another mutation, forever, confirmed via
        // CDP (180 childList mutations on this exact node over 3 idle
        // seconds — 60/sec, matching rAF). The guarded write below breaks
        // the cycle: once the mirrored value stabilizes, no further writes
        // happen, so no further self-triggered mutations do either.
        const subheader = root.querySelector(':scope > div:first-child > .scene-subheader');
        if (subheader) {
          const mirrors = [
            ['framerate', 'Framerate', subheader.querySelector('.frame-rate')],
            ['resolution', 'Resolution', subheader.querySelector('.resolution')],
          ];
          for (const [key, label, source] of mirrors) {
            if (!source) continue;
            let h6 = metaCol.querySelector(`:scope > h6[data-jl-mirror-key="${key}"]`);
            if (!h6) {
              h6 = document.createElement('h6');
              h6.dataset.jlMirror = 'true';
              h6.dataset.jlMirrorKey = key;
              h6.dataset.jlItem = 'scenedetails';
              h6.dataset.jlLabel = label;
              metaCol.appendChild(h6);
            }
            const value = source.textContent.trim();
            if (h6.textContent !== value) h6.textContent = value;
            scenedetails.push(h6);
          }
        }

        // Whichever row is genuinely last gets the card's bottom
        // corner/border — flagged explicitly, since every row shares the
        // same data-jl-item value ('scenedetails') and CSS has no "last of
        // these" selector to reach for otherwise. "Last" means last in
        // *display* order, which is governed entirely by the per-label
        // `order` values in scene-dashboard.css (Resolution, Framerate,
        // Director, Created, Updated) — NOT push/DOM order, which no
        // longer matches display order now that the mirrors (pushed last,
        // below) display first. Keep this list in sync with those `order`
        // declarations if that visual sequence ever changes.
        const METADATA_ROW_ORDER = ['Resolution', 'Framerate', 'Director', 'Created', 'Updated'];
        let lastRow = null;
        for (const h6 of scenedetails) {
          if (!lastRow || METADATA_ROW_ORDER.indexOf(h6.dataset.jlLabel) > METADATA_ROW_ORDER.indexOf(lastRow.dataset.jlLabel)) {
            lastRow = h6;
          }
        }
        for (const h6 of scenedetails) {
          setData(h6, 'jlLastInGroup', h6 === lastRow ? 'true' : null);
        }

        ensureGroupHead(root, metaCol, 'metadata', CARD_TITLES.metadata);
        buildSceneBadgeRow(metaCol, subheader);
        syncWatchedBadge(metaCol);
      }

      const contentRow = details.querySelector(':scope > .row:nth-child(2)');
      const contentCol = contentRow && contentRow.firstElementChild;
      if (contentCol) {
        for (const h6 of contentCol.querySelectorAll(':scope > h6')) {
          const text = h6.textContent.trim();
          if (text.startsWith('Details')) setData(h6, 'jlItem', 'description-heading');
          else if (text.startsWith('Tags')) setData(h6, 'jlItem', 'tags-heading');
          else if (text.startsWith('Performer')) setData(h6, 'jlItem', 'performers-heading');
        }

        ensureGroupHead(root, contentCol, 'description', CARD_TITLES.description);
        ensureGroupHead(root, contentCol, 'tags', CARD_TITLES.tags);
        sizeTagsBackdrop(contentCol);

        const scenePerformers = contentCol.querySelector(':scope > .scene-performers');
        if (scenePerformers) buildSidebarPerformerList(contentCol, scenePerformers);

        const customFields = contentCol.querySelector(':scope > .custom-fields');
        if (customFields) {
          addClass(customFields, 'jl-card');
          ensureHead(customFields, 'customfields', CARD_TITLES.customfields);

          // Custom Fields has its own native collapse (`.collapse-header` +
          // `.collapse`), independent of ours, defaulting to CLOSED
          // (`.collapse` with no `.show`). Our jl-head now does that job for
          // the whole card, so the native one is hidden in CSS — but hiding
          // it without also opening it would permanently strand the content
          // behind a control nobody can reach. `.click()` fires the same
          // trusted event React's own onClick listens for (the mode bar
          // already relies on this to drive stash's nav), so this is a
          // real user-equivalent interaction, not a DOM hack. Guarded so it
          // only fires once, on the run that finds it still closed.
          const nativeToggle = customFields.querySelector(':scope .collapse-header .collapse-button');
          const nativeBody = customFields.querySelector(':scope .collapse');
          if (nativeToggle && nativeBody && !nativeBody.classList.contains('show')) {
            nativeToggle.click();
          }
        }
      }
    }

    if (seen.fileinfo) {
      ensureHead(seen.fileinfo, 'fileinfo', CARD_TITLES.fileinfo);
      // Stash puts a count badge on the File Info tab when a scene has
      // more than one file (`<span class="badge badge-pill">2</span>`
      // inside the nav link). That tab is hidden behind the mode bar,
      // and File lives under Browse rather than being a mode of its own,
      // so the count moves to where the tab's content went: the File
      // card's head, styled like the mode bar's own count badges.
      syncHeadCount(root, seen.fileinfo, 'scene-file-info-panel');
    }
    if (seen.history)   ensureHead(seen.history,   'history',   CARD_TITLES.history);
    if (seen.groups)    ensureHead(seen.groups,    'groups',    CARD_TITLES.groups);
    if (seen.galleries) ensureHead(seen.galleries, 'galleries', CARD_TITLES.galleries);

    // Hide Groups / Galleries cards with nothing in them.
    for (const slug of ['groups', 'galleries']) {
      const pane = seen[slug];
      if (!pane) continue;
      const body = pane.querySelector(':scope > .row, :scope > .container');
      const empty = !body || body.children.length === 0;
      const next = empty ? 'true' : 'false';
      if (pane.dataset.jlEmpty !== next) pane.dataset.jlEmpty = next;
    }

    sizeCodeDateBackdrop(root);

    return seen;
  }

  /* Panes we reveal still carry aria-hidden="true" from react-bootstrap.
   * Safe to correct: the nav observer filters on class, and the body observer
   * watches childList, so writing this attribute cannot re-trigger either. */
  function unhideFromScreenReaders(root, mode) {
    // Details holds the persistent identity/description/tags block, so it
    // stays screen-reader-visible in every mode, not just Browse.
    const details = root.querySelector('[data-jl-pane="details"]');
    if (details && details.getAttribute('aria-hidden') === 'true') {
      details.setAttribute('aria-hidden', 'false');
    }
    if (mode !== 'browse') return;
    for (const pane of root.querySelectorAll('.tab-pane[data-jl-browse]')) {
      if (pane.getAttribute('aria-hidden') === 'true') pane.setAttribute('aria-hidden', 'false');
    }
  }

  /* ============================
   *  MODE BAR
   * ============================ */
  function navLinkFor(root, eventKey) {
    return root.querySelector(`.nav-tabs [data-rb-event-key="${eventKey}"]`);
  }

  function currentMode(root) {
    const active = root.querySelector('.nav-tabs .nav-link.active');
    const key = active && active.getAttribute('data-rb-event-key');
    if (!key) return null;
    const owned = MODES.find(m => m.key === key);
    if (owned) return owned.id;
    // Details, File Info, History, Groups and Galleries all live under Browse.
    return 'browse';
  }

  function buildModeBar(root) {
    const nav = root.querySelector('.nav-tabs');
    if (!nav || !nav.parentElement) return false;

    const available = MODES.filter(m => navLinkFor(root, m.key));
    if (!available.length) return false;

    const signature = available.map(m => m.id).join('|');
    const existing = root.querySelector(':scope > div > .jl-modes-row');
    if (existing && existing.dataset.jlSignature === signature) return true;
    if (existing) existing.remove();

    // .jl-modes-row is a plain centering box (full line width, invisible);
    // .jl-modes inside it is the actual pill and only hugs its buttons. Two
    // elements rather than one so the pill's background never has to stretch
    // to the full row just to get centered in it.
    const row = document.createElement('div');
    row.className = 'jl-modes-row';
    row.dataset.jlSignature = signature;

    const bar = document.createElement('div');
    bar.className = 'jl-modes';
    bar.setAttribute('role', 'tablist');

    for (const mode of available) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'jl-mode';
      btn.dataset.jlModeId = mode.id;
      btn.setAttribute('role', 'tab');
      btn.textContent = mode.label;
      btn.addEventListener('click', () => {
        // Drive stash's own nav rather than its state: Tab.Container stays the
        // single source of truth for which pane is active.
        const link = navLinkFor(root, mode.key);
        if (link) link.click();
        requestAnimationFrame(() => syncModeBar(root));
      });
      bar.appendChild(btn);
    }

    row.appendChild(bar);
    nav.parentElement.insertBefore(row, nav);
    return true;
  }

  /* Re-runs just the layout-dependent measurements (tag rows + both
   * backdrops) without the rest of tagPanes(). Needed because those
   * measurements are only valid when taken in the layout state the user
   * actually sees — and three things change that state without any
   * childList mutation inside .scene-tabs for the body observer to catch:
   *   - data-jl-mode being set for the first time (the flattening CSS
   *     kicks in and every chip moves) or changing (Browse-only content
   *     hides/shows), which is a plain attribute write by syncModeBar;
   *   - a font finishing loading (chip widths change → rows re-wrap);
   *   - a viewport resize (the sidebar is full-width on mobile).
   * Before the observer was filtered to sidebar-relevant mutations (see
   * touchesSidebar below), the constant stream of unrelated re-runs
   * masked all three: some later run always happened to re-measure in
   * the right state. That's gone on purpose, so each trigger gets its
   * own explicit re-measure instead. Cheap: each function reads first and
   * only writes when a value actually changed. */
  function measure(root) {
    const details = root.querySelector('[data-jl-pane="details"]');
    const contentRow = details && details.querySelector(':scope > .row:nth-child(2)');
    const contentCol = contentRow && contentRow.firstElementChild;
    if (contentCol) sizeTagsBackdrop(contentCol);
    sizeCodeDateBackdrop(root);
  }

  function syncModeBar(root) {
    const mode = currentMode(root);
    if (!mode) return;
    if (root.dataset.jlMode !== mode) {
      root.dataset.jlMode = mode;
      // The attribute write above changes which CSS applies to every
      // sidebar item; measure again now that the new layout state is in
      // place (getBoundingClientRect forces the layout synchronously).
      measure(root);
    }
    for (const btn of root.querySelectorAll('.jl-mode')) {
      setAttr(btn, 'aria-selected', String(btn.dataset.jlModeId === mode));
    }
    unhideFromScreenReaders(root, mode);
  }

  /* ============================
   *  NAV WATCHER
   *
   *  Switching tabs only toggles the `active` class — an attribute mutation,
   *  which the childList observer at the bottom of this file never sees. Watch
   *  the nav directly so the mode bar also follows stash's own keyboard
   *  shortcuts (a / q / e / k / i / h, bound in Scene.tsx).
   * ============================ */
  let navObserver = null;

  function watchNav(root) {
    const nav = root.querySelector('.nav-tabs');
    if (!nav || nav._dlNavWatched) return;
    nav._dlNavWatched = true;

    if (navObserver) navObserver.disconnect();
    navObserver = new MutationObserver(() => syncModeBar(root));
    navObserver.observe(nav, {
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });
  }

  /* ============================
   *  MAIN
   * ============================ */
  function run() {
    const root = document.querySelector('.scene-tabs');
    if (!root) return;
    if (!root.querySelector('.tab-content')) return;

    tagPanes(root);
    if (!buildModeBar(root)) return;
    watchNav(root);
    syncModeBar(root);
  }

  /* The observer below is body-wide (React can mount the scene page's
   * markup anywhere, and the SPA swaps whole subtrees on navigation), but
   * run() only ever cares about what happens inside `.scene-tabs`. The
   * video player — a sibling of .scene-tabs, never inside it — mutates its
   * progress bar and time tooltip ~70 times a second during playback, and
   * before this filter every one of those batches re-ran tagPanes() and
   * re-measured the whole tag cloud (CDP profile, 2026-09-02: +400 forced
   * layouts and roughly double stash's own main-thread time over 6 s of
   * playback, for zero visible change). So a batch only schedules a run
   * when at least one record touches the sidebar:
   *   - its target is inside the current .scene-tabs, or
   *   - it adds a node that is / contains a .scene-tabs (a fresh scene
   *     page mounting, or scene→scene SPA navigation replacing the old
   *     one — the record's target is that node's *parent*, outside any
   *     .scene-tabs, so the target check alone would miss it), or
   *   - there is no .scene-tabs in the document at all (nothing to
   *     protect; run() bails cheaply on its own in that case anyway, but
   *     an old root being removed with the new one not yet mounted must
   *     not get stuck ignoring the mount when it does arrive). */
  function touchesSidebar(muts) {
    const root = document.querySelector('.scene-tabs');
    if (!root) return true;
    for (const m of muts) {
      if (root.contains(m.target)) return true;
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n === root || n.contains(root) || n.querySelector('.scene-tabs')) return true;
      }
    }
    return false;
  }

  let queued = false;
  const observer = new MutationObserver(muts => {
    if (queued) return;
    if (!touchesSidebar(muts)) return;
    queued = true;
    requestAnimationFrame(() => {
      queued = false;
      try { run(); } catch (e) { console.error('[SceneDashboard]', e); }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });

  // Layout-only triggers with no DOM mutation for the observer to see —
  // see measure() for why each one needs its own hook.
  let remeasureQueued = false;
  function queueRemeasure() {
    if (remeasureQueued) return;
    remeasureQueued = true;
    requestAnimationFrame(() => {
      remeasureQueued = false;
      const root = document.querySelector('.scene-tabs[data-jl-mode]');
      if (root) { try { measure(root); } catch (e) { console.error('[SceneDashboard]', e); } }
    });
  }
  window.addEventListener('resize', queueRemeasure);
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(queueRemeasure);

  run();
})();
