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

  /* ============================
   *  PILL TEXT CONTRAST
   *  Collection colors span the whole hash-derived DEFAULT_PALETTE plus
   *  whatever a user picks by hand in the settings panel — fixed white
   *  text (the solid-fill "native tag" look, see buildPill()) reads fine
   *  on most of those but noticeably weaker on the brighter ones (spotted
   *  live: '#facc15', a bright gold, next to a pink collection using the
   *  identical white). WCAG relative luminance + contrast ratio, not a
   *  rough "brightness > 128" guess — picks whichever of two fixed
   *  candidates actually contrasts better against this specific
   *  background, per-pill, rather than one fixed color for all of them.
   * ============================ */
  function srgbToLinear(c) {
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  }
  function relativeLuminance(hex) {
    const r = parseInt(hex.slice(1, 3), 16) / 255;
    const g = parseInt(hex.slice(3, 5), 16) / 255;
    const b = parseInt(hex.slice(5, 7), 16) / 255;
    const [rl, gl, bl] = [r, g, b].map(srgbToLinear);
    return 0.2126 * rl + 0.7152 * gl + 0.0722 * bl;
  }
  function contrastRatio(l1, l2) {
    const lighter = Math.max(l1, l2);
    const darker = Math.min(l1, l2);
    return (lighter + 0.05) / (darker + 0.05);
  }
  // Dracula's own foreground/background pair, not a plain white/black
  // guess — #f8f8f2 is the exact color stash's own native tag chips use
  // (see buildPill()'s own comment), so the "light" candidate already
  // matches what's elsewhere on screen; #282a36 is Dracula's canonical
  // background, dark enough for strong contrast without being a stark
  // pure #000 that would clash with the theme everywhere else.
  const PILL_TEXT_LIGHT = '#f8f8f2';
  const PILL_TEXT_DARK = '#282a36';
  const PILL_TEXT_LIGHT_LUM = relativeLuminance(PILL_TEXT_LIGHT);
  const PILL_TEXT_DARK_LUM = relativeLuminance(PILL_TEXT_DARK);
  function pickPillTextColor(bgHex) {
    const bgLum = relativeLuminance(bgHex);
    const withLight = contrastRatio(bgLum, PILL_TEXT_LIGHT_LUM);
    const withDark = contrastRatio(bgLum, PILL_TEXT_DARK_LUM);
    return withLight >= withDark ? PILL_TEXT_LIGHT : PILL_TEXT_DARK;
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

  // `configurePlugin` REPLACES the plugin's entire settings object with
  // whatever `input` map is given — it does not merge. Confirmed live,
  // the hard way: writing `{ pillStyle: "outline" }` alone wiped out an
  // already-saved `popoverPriority` on the standalone verification
  // instance (a real, user-set value, not a default). Every write needs
  // to fetch the current full config first and merge into it, or it
  // silently destroys whichever other settings weren't part of that
  // particular save — this is what saveColors() should have been doing
  // from the start, not something new pillStyle introduced.
  async function configurePluginMerged(partial) {
    const data = await gql(`{ configuration { plugins } }`);
    const current = data?.configuration?.plugins?.[PLUGIN_ID] || {};
    const merged = { ...current, ...partial };
    await gql(
      `mutation($input: Map!) { configurePlugin(plugin_id: "${PLUGIN_ID}", input: $input) }`,
      { input: merged }
    );
  }

  async function saveColors(collections) {
    const colors = {};
    for (const c of collections) colors[c.path] = c.color;
    await configurePluginMerged({ collectionColors: JSON.stringify(colors) });
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

  /* ============================
   *  PILL VISUAL STYLE
   *  Two looks, user-selectable via the popoverPriority-style plugin
   *  setting `pillStyle` fetched below: 'outline' (default — transparent
   *  background, full-opacity colored border/text; distinguishes the
   *  pill from buttons/mode-pills' shared wash mechanic rather than
   *  matching native) or 'solid' (matches stash's own native tag chips,
   *  `.tag-item.badge.badge-secondary`: solid background, contrast-
   *  picked text, no border).
   *
   *  applyPillStyle() is the single place either look is actually
   *  applied, called both from buildPill() (new pills) and from the
   *  settings-fetch IIFE below (already-inserted pills, once the saved
   *  choice loads — see that IIFE for why this can't just be decided
   *  once at build time). `pill.dataset.collectionColor` stores the raw
   *  hex so a pill can be restyled later without needing its original
   *  `collection` object back in scope.
   * ============================ */
  const PILL_STYLES = ['solid', 'outline'];
  let PILL_STYLE = 'outline';

  function applyPillStyle(pill, colorHex) {
    pill.dataset.collectionColor = colorHex;
    if (PILL_STYLE === 'outline') {
      pill.style.color = colorHex;
      pill.style.backgroundColor = 'transparent';
      pill.style.border = '1.5px solid ' + colorHex;
    } else {
      pill.style.color = pickPillTextColor(colorHex);
      pill.style.backgroundColor = colorHex;
      pill.style.border = 'none';
    }
  }

  function buildPill(collection, extraClass) {
    const pill = document.createElement('span');
    pill.className = extraClass ? `stash-collection-pill ${extraClass}` : 'stash-collection-pill';
    pill.textContent = collection.label;
    applyPillStyle(pill, collection.color);
    pill.title = 'Filter: ' + collection.label;
    pill.addEventListener('click', e => {
      e.stopPropagation();
      e.preventDefault();
      window.location.href = collectionFilterURL(collection.path);
    });
    return pill;
  }

  /* ============================
   *  POPOVER REORDERING
   *  Ported in from dracula-layout's clean-cards.js (originally the
   *  user's own customJavaScript.js) — this plugin is meant to be fully
   *  independent, producing a correct result with no other plugin
   *  installed. dracula-layout, if present, no longer knows anything
   *  about this plugin's pill beyond excluding it by class name from its
   *  OWN, separate reordering of the native icons — so without this
   *  section duplicating that whole system, this plugin's pill would go
   *  back to being a bare, unmanaged element that can wrap unpredictably
   *  on any theme whose `.card-popovers` allows flex-wrap (the original
   *  bug this whole mechanism exists to fix).
   *
   *  Layout: the pill is a fixed, always-visible anchor at the bar's
   *  left edge; every native icon is right-justified as its own group,
   *  reordered among themselves by priority, with the lowest-priority
   *  ones hidden (never wrapped) when the card is too narrow to fit
   *  them all. The pill itself never competes for that space or gets
   *  hidden — it's structurally separate from the natives' priority
   *  competition now, not just first among equals in it (an earlier,
   *  same-day version of this had `collection` as a normal priority
   *  key, pushing the *whole* row — pill included — flush right as one
   *  group; reverted per request for two distinct left/right zones).
   *
   *  Applies to EVERY scene-card popover bar, not just ones this plugin
   *  adds a pill to — a card with no matching collection still gets its
   *  native icons reordered/anchored/overflow-managed, matching how this
   *  worked before the pill even existed as a separate concern.
   * ============================ */

  const POPOVER_KEY_CLASSES = {
    "performer-count": ["performer-count"],
    "count-button":    ["count-button", "increment-only"],
    "marker-count":    ["marker-count"],
    "tag-count":       ["tag-count"],
    "group-count":     ["group-count", "tag-tooltip"],
    organized:         ["organized"],
    "other-copies":    ["other-copies", "extra-scene-info"],
  };

  const DEFAULT_POPOVER_PRIORITY = [
    "performer-count", "count-button", "marker-count",
    "tag-count", "group-count", "organized", "other-copies",
  ];

  let POPOVER_ORDER_PARSED = DEFAULT_POPOVER_PRIORITY.map(key => POPOVER_KEY_CLASSES[key]);

  // One combined fetch for both user-configurable settings
  // (popoverPriority and pillStyle) rather than two separate GraphQL
  // round trips — both live under the same `configuration.plugins`
  // query. Each is applied independently (no early-return after the
  // first), so setting only one of the two still works correctly.
  (async () => {
    try {
      const data = await gql(`{ configuration { plugins } }`);
      const cfg = data?.configuration?.plugins?.[PLUGIN_ID];

      const rawPriority = cfg?.popoverPriority;
      if (rawPriority) {
        const listed = rawPriority.split(',').map(s => s.trim()).filter(Boolean).filter(k => POPOVER_KEY_CLASSES[k]);
        if (listed.length) {
          // Any known key the user's list omitted still appears, just
          // last — after every key they did list — same "unrecognized
          // item lands last" rule reorderPopoverBar() already applies
          // one level down, to DOM children it can't match against any
          // key at all.
          const keys = listed.slice();
          for (const key of DEFAULT_POPOVER_PRIORITY) {
            if (!keys.includes(key)) keys.push(key);
          }
          POPOVER_ORDER_PARSED = keys.map(key => POPOVER_KEY_CLASSES[key]);
          // Invalidate every bar's cached reorder signature so the next
          // pass re-applies the newly-loaded priority — reorderPopoverBar()
          // below otherwise trusts a signature computed under the default
          // order and silently no-ops.
          const bars = document.querySelectorAll('.card-popovers.btn-group');
          bars.forEach(bar => { delete bar.dataset._stashPopoverSig; });
          bars.forEach(reorderPopoverBar);
        }
      }

      const rawStyle = cfg?.pillStyle?.trim().toLowerCase();
      if (rawStyle && PILL_STYLES.includes(rawStyle) && rawStyle !== PILL_STYLE) {
        PILL_STYLE = rawStyle;
        // Pills already built (before this fetch resolved) were styled
        // under the default — restyle them now rather than waiting for
        // the next unrelated re-render to happen to touch them. Reads
        // the color back from `dataset.collectionColor` (set by
        // applyPillStyle() at build time) rather than needing each
        // pill's original `collection` object back in scope here.
        document.querySelectorAll('.stash-collection-pill').forEach(pill => {
          if (pill.dataset.collectionColor) applyPillStyle(pill, pill.dataset.collectionColor);
        });
      }
    } catch (e) {
      console.error('[CollectionColors] plugin settings fetch failed', e);
    }
  })();

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
      el.style.removeProperty('display');
    });
  }

  /* Push native buttons right as their own group, then hide any that
   * overflow the bar. The pill (if present) is excluded entirely — it's
   * a fixed left anchor, always visible, never touched here beyond a
   * one-time reset of any margin/visibility/display a prior version of
   * this logic might have left on it. Single persistent ResizeObserver per
   * bar so getBoundingClientRect() is always post-layout, and keeps
   * tracking the bar across future resizes instead of measuring once and
   * going stale.
   *
   * Overflow is hidden with `display: none`, not `visibility: hidden` —
   * found live (192.168.11.109) that visibility alone left the icon group
   * floating well short of the bar's right edge on any card with the full
   * native icon set present. Root cause: `visibility: hidden` still
   * reserves its element's box in layout — the flex line's total content
   * width never actually shrinks just because some of it turned invisible.
   * natives[0]'s `margin-left: auto` only has a positive value to resolve
   * to when there's real free space in that line; if the *un-hidden*
   * content already overflowed the bar (true for any card with every
   * priority key present), the free space is zero and stays zero no
   * matter how many of those still-space-occupying items get hidden
   * afterward — the margin permanently resolves to 0, and the visible
   * icons sit wherever they landed before hiding, nowhere near the right
   * edge. `display: none` actually removes a hidden item from the line's
   * width contribution, so once enough are removed for the rest to fit,
   * the auto margin has real free space again and correctly pushes the
   * remaining icons flush right.
   *
   * SCENE-CARD ONLY — see reorderPopoverBar()'s own performer-card
   * bail-out; callers must route those bars to clearAnchorState() instead. */
  function anchorButtonsRight(bar) {
    const pill = bar.querySelector(':scope > .stash-collection-pill');
    if (pill) {
      pill.style.removeProperty('margin-left');
      pill.style.removeProperty('visibility');
      pill.style.removeProperty('display');
    }
    const natives = Array.from(bar.children).filter(el => el !== pill);

    // Reset all state from any prior call
    natives.forEach(el => {
      el.style.removeProperty('margin-left');
      el.style.removeProperty('visibility');
      el.style.removeProperty('display');
    });
    if (!natives[0]) return;
    natives[0].style.setProperty('margin-left', 'auto', 'important');

    const recompute = () => {
      // Reset every pass, not just the first — display:none actually
      // removes an item from the line's content width (that's the whole
      // point, see above), so a later pass triggered by a real resize
      // needs a clean slate to correctly re-discover that a previously-
      // hidden item now fits again. The loop below only ever *sets* the
      // hidden state; without this it would never unset one.
      natives.forEach(el => el.style.removeProperty('display'));
      // The threshold is the bar's *content*-box right edge (border-box
      // minus its own right padding), not the bare border-box edge.
      // Found live: comparing against the raw border-box edge only
      // guarantees content won't get clipped by this bar's own
      // `overflow: hidden` — it lets kept icons poke up to a full
      // padding-width into the 14px inset before the hide-cascade
      // triggers, which defeats the point of that padding (see the
      // symmetric-inset rule in collection-colors.css). Subtracting the
      // bar's own computed padding-right makes the threshold the actual
      // visual boundary icons are meant to respect.
      const barRect = bar.getBoundingClientRect();
      const barPadRight = parseFloat(getComputedStyle(bar).paddingRight) || 0;
      const barRight = barRect.right - barPadRight;
      let clipping = false;
      for (const el of natives) {
        if (clipping) {
          el.style.setProperty('display', 'none', 'important');
        } else {
          const elRight = el.getBoundingClientRect().right;
          if (elRight > barRight - 1) {
            clipping = true;
            el.style.setProperty('display', 'none', 'important');
          }
        }
      }
    };

    if (!bar._stashRO) {
      bar._stashRO = new ResizeObserver(() => recompute());
      bar._stashRO.observe(bar);
    } else {
      recompute();
    }
  }

  function reorderPopoverBar(bar) {
    if (!bar) return;

    // Performer-card bars rely on pure CSS flex-stretch for their 2-button
    // layout and must never receive the scene-card anchoring/overflow-
    // clipping treatment — see anchorButtonsRight's doc comment for why.
    const isPerformerCard = !!bar.closest('.performer-card');
    if (isPerformerCard) { clearAnchorState(bar); return; }

    const allKids = Array.from(bar.children);
    if (!allKids.length) return;

    // The pill always leads, unconditionally — not part of the priority
    // list at all any more (see POPOVER REORDERING's own comment above).
    // insertBefore() on a node that's already bar.firstElementChild is a
    // safe no-op, not a real move, so this doesn't cause a mutation (and
    // therefore doesn't self-trigger the observer watching this bar) once
    // it's already in place.
    const pill = allKids.find(el => el.classList.contains('stash-collection-pill'));
    if (pill && bar.firstElementChild !== pill) {
      bar.insertBefore(pill, bar.firstElementChild);
    }

    const reorderKids = allKids.filter(el => el !== pill);
    if (!reorderKids.length) { anchorButtonsRight(bar); return; }

    reorderKids.forEach((el, i) => {
      if (!el.dataset._stashPid) el.dataset._stashPid = String(i);
    });
    const sig = reorderKids.map(el => el.dataset._stashPid).join('|');
    if (bar.dataset._stashPopoverSig === sig) {
      anchorButtonsRight(bar);
      return;
    }

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

    // Appending after the pill (still bar's first child, untouched above)
    // lands this group right after it, which is exactly where it already
    // was — this only ever reorders the natives *among themselves*.
    const frag = document.createDocumentFragment();
    out.forEach(el => frag.appendChild(el));
    bar.appendChild(frag);

    bar.dataset._stashPopoverSig = out.map(el => el.dataset._stashPid).join('|');
    anchorButtonsRight(bar);
  }

  // Two-tier observer, matching dracula-layout's original design: a
  // lightweight "boot" observer watches the whole body just for NEW
  // `.card-popovers.btn-group` bars appearing (new cards mounting) and
  // registers each with the shared live observer below, plus reorders it
  // immediately; the live observer then only watches the (comparatively
  // few) bars actually registered with it for later changes — a rating
  // click, an async duplicate-finder icon appearing, this plugin's own
  // pill insertion, etc — rather than one giant subtree observer
  // reacting to every DOM mutation on the whole page.
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

  let popoverBootPending = [];
  let popoverBootScheduled = false;
  const popoverBootObserver = new MutationObserver(muts => {
    for (const m of muts) {
      for (const node of m.addedNodes) {
        if (node instanceof Element) popoverBootPending.push(node);
      }
    }
    if (popoverBootScheduled) return;
    popoverBootScheduled = true;
    queueMicrotask(() => {
      const nodes = popoverBootPending;
      popoverBootPending = [];
      popoverBootScheduled = false;
      nodes.forEach(node => {
        if (node.matches?.('.card-popovers.btn-group')) {
          popoverObserver.observe(node, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
          reorderPopoverBar(node);
        } else {
          node.querySelectorAll?.('.card-popovers.btn-group').forEach(bar => {
            popoverObserver.observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
            reorderPopoverBar(bar);
          });
        }
      });
    });
  });

  function initPopoverReordering() {
    popoverBootObserver.observe(document.body, { childList: true, subtree: true });
    document.querySelectorAll('.card-popovers.btn-group').forEach(bar => {
      popoverObserver.observe(bar, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] });
      reorderPopoverBar(bar);
    });
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

      // Bare pill, no wrapper, no "is someone else managing this bar"
      // detection — this plugin now runs its own complete popover
      // priority/overflow-hide system (see POPOVER REORDERING above) for
      // every scene-card bar, whether or not it ends up adding a pill to
      // it, so it's always the one thing anchoring/reordering/hiding
      // items in this bar. The live popoverObserver reacts to this
      // insertion on its own (it's a childList mutation on a bar that
      // observer is already watching), but reorder immediately too rather
      // than waiting on that round trip.
      bar.insertBefore(buildPill(collection), bar.firstChild);
      reorderPopoverBar(bar);
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
  initPopoverReordering();
})();
