# Next steps — extraction candidates & performance fixes

*Note: this plugin was named "dracula-layout" until 2026-09-02, when it
was renamed to JAV Layout — see CLAUDE.md's own renaming note. Every
"dracula-layout" mention below predates that and refers to this same
plugin, left as-written rather than rewritten.*

Risk/confidence analysis from the 2026-09-01 review of dracula-layout. Two
lists: features that could be pulled out as standalone stash plugins
(following the copy-buttons / collection-colors precedent), and performance
fixes. Each item carries the risks that would actually bite, grounded in
this codebase's own documented landmines (see CLAUDE.md), not generic
caution.

**Recommended order (safest → riskiest):**
~~title-write guard~~ (done) → ~~sceneCache cap~~ (done) → buttons.css
extraction → ~~title scrubber extraction~~ (done) → observer merge →
measure-skip fingerprinting → findScenes batch query → popover
extraction → shared ResizeObserver and watched-badge extraction (skip
both barring a specific reason).

Title scrubber extraction and the sceneCache LRU cap are both done
(2026-09-02) — see their own sections below for what shipped. The
title-write guard (item #1 below) also shipped as part of the title
scrubber extraction — that code no longer lives in this plugin at all.

---

## Performance fixes

### 1. Guard the card-title write — **DONE, via the title-scrubber extraction (2026-09-02)**

`applySceneDataToCard()` in clean-cards.js used to write
`titleEl.textContent = cleanTitleText(...)` unconditionally (unlike
`cleanDetailPageTitle()` right next to it, which equality-guarded). Not a
loop — cards were marked `stashClean` — but each write was a childList
mutation that woke every body observer once per card: ~40 avoidable
wake-ups per grid page. This code no longer lives in dracula-layout at
all (moved to the standalone `title-scrubber` plugin); the new plugin's
own version is equality-guarded in both places. Left below for the
historical reasoning, since the same "restate the guard, don't assume
the sibling function's pattern carries over" lesson still applies to any
future write reachable from a body-wide observer in this codebase.

- **Risks: near zero.** Exact equality-guard pattern already proven in
  three places in these files (`cleanDetailPageTitle`, the Framerate/
  Resolution mirrors, the empty-card flags). Cleaned value is
  deterministic, so the guard can't skip a needed write. Only theoretical
  edge — React rewriting a title after our write — behaves identically
  with or without the guard.
- **Verify:** standard idle-mutation check + eyeball a grid page.

### 2. Cap `sceneCache` (LRU) — **DONE (2026-09-02)**

The cache in clean-cards.js grew unboundedly — every scene ever seen in a
session stayed cached (performers, paths, all of it). A long
browse-everything session on a large library could accumulate tens of MB.

Fixed with the standard Map-as-LRU trick: `sceneCacheGet()`/
`sceneCacheSet()` wrap the existing `sceneCache` Map (a Map already
preserves insertion order) — `get` deletes-then-re-sets the hit key to
move it to the end (most recently used), `set` evicts from the front
(`keys().next()`, the least recently used) once the cap is exceeded.
`SCENE_CACHE_LIMIT = 4000`. Both call sites (`requestSceneData`'s cache
hit, `runBatchQuery`'s post-fetch write) now go through the wrappers; the
two remaining raw `sceneCache.has()` checks were left as-is since a pure
existence check doesn't need to touch recency order.

- **Risks realized: none.** Verified live: cards still render correctly
  end-to-end (code/date/performers all populated), and a standalone
  unit-style test of the eviction logic (3-entry cap, touch one entry via
  `get`, insert a 4th) confirmed the *untouched* least-recently-used
  entry gets evicted, not the touched one or an arbitrary one — the core
  claim an LRU cache has to get right. 0 idle mutations, 0 console
  errors.

### 3. Merge the five body-wide MutationObservers — **confidence: medium-high on correctness, medium on worth**

clean-cards.js runs three body-wide `childList+subtree` observers (popover
boot, studio-code five-function pass, card enhancement); scene-dashboard.js
and entity-dashboard.js one each. Every React commit anywhere wakes all
five. A shared dispatcher (one observer, N subscribers) would cut the
records-walking to once per batch.

- **Risks: subtle ordering/timing coupling.** The five callbacks have
  independent scheduling — two use rAF, one uses `queueMicrotask`, popover
  boot keeps its own pending-list. clean-cards' boot observer specifically
  relies on microtask timing to catch React batches; a naive merge that
  unifies scheduling could reintroduce the "batch of nodes arrived, check
  silently failed" bug already fixed once (see the `bootPending` comment
  in clean-cards.js). The safe version preserves each subscriber's own
  scheduling and only shares the observer — at which point the win shrinks
  (saves four callback invocations per batch, not four rAFs).
- Also touches all three JS files at once, contradicting CLAUDE.md's
  "changing one shouldn't require touching the others" rule.
- **Verdict: correct-but-low-yield.** Do it only if the plugin family
  keeps growing.

### 4. Fingerprint-skip the backdrop/tag-row re-measures — **confidence: medium**

`sizeCodeDateBackdrop()` and `sizeTagsBackdrop()` both do
write→measure→write (reset height to 0, `getBoundingClientRect()`, set
height), and `markTagRows()` measures every chip — ~3-4 forced synchronous
reflows per `tagPanes()` run, which fires on every rAF-batched body
mutation on a scene page (including mutations these functions didn't
cause: video-player time display, typing in Edit mode). Throttled to once
per frame so it can't spiral, but each qualifying frame pays the reflows
even when nothing changed. Fix: skip the re-measure when a fingerprint of
the measured *inputs* hasn't changed (same pattern as the badge row and
sidebar performer list).

- **Risks: the highest of any fix on this list.** Choosing the
  fingerprint inputs is the hard part, and this exact area has bitten
  before. Measured values depend on things a cheap fingerprint might not
  capture: font loading finishing (chip widths change → rows re-wrap with
  no childList mutation), the Tags collapse state (the stale-measurement
  bug already shipped once — it's a Settled Decision entry in CLAUDE.md),
  zoom/viewport changes, sidebar width. Chip count + container width
  misses the font-load case; adding total cloud height is circular
  (height is the output). A wrong fingerprint produces exactly the class
  of bug this plugin has repeatedly paid to fix: a visually-wrong
  backdrop that measurements taken at the wrong moment made "correct."
- **If done:** fingerprint on inputs only (chip id list + container width
  + collapsed flag), deliberately do NOT skip on the first few runs after
  page load, and verify against the known regression scenarios:
  load-collapsed-then-expand, many-tag scenes, narrow viewport. Real
  chance of one revision cycle after live use.
- This is the best actual perf payoff on the list.

### 5. Replace aliased `findScene`×100 with one `findScenes(ids)` — **confidence: medium**

The batch GraphQL query builds up to 100 aliased `findScene` calls per
request; the server resolves each alias independently. Stash's API has
`findScenes` with an ID filter — one resolver call, same data.

- **Risks: moderate, mostly failure semantics.** The response-handling
  code is shaped around per-alias keys (`s${id}`), partial-result
  handling, and the retry path — all of that gets rewritten, not just the
  query string. The per-alias form has a useful property: one bad scene
  ID fails one alias, the rest still resolve, and the "not in response →
  callback(null)" cleanup handles it. `findScenes` returns a single
  list — a malformed request fails everything. Must verify against THIS
  stash version (not just current `develop`) which filter shape takes
  IDs, what it returns for deleted/missing IDs, and that the same fields
  (`performers { image_path }`, `play_count`, etc.) are reachable.
- **Before touching JS:** hand-run the candidate query against the live
  instance's /graphql.
- The win is server-side CPU nobody has reported as a problem — lower
  priority than its cleanliness suggests.

### 6. One shared ResizeObserver for popover bars — **confidence: high mechanically, low value — SKIP**

`anchorButtonsRight()` creates one RO per scene-card popover bar (40+ on a
grid page). They're GC'd with their bars and RO callbacks only fire on
actual resizes, so the status quo has no measured cost.

- **Risks: low but nonzero for no payoff.** The per-bar RO doubles as the
  "already watched" flag (`bar._stashRO`); a shared RO needs a
  replacement (WeakSet), and `clearAnchorState()`'s disconnect path for
  performer-card bars needs `unobserve` instead of `disconnect`.
- **Verdict: skip unless touching that code anyway.**

---

## Extraction candidates

Target repo for extractions: `/srv/stash-plugins/` following the
copy-buttons pattern (commit/push/build/deploy/updatePackages to reach
both the local instance and 192.168.11.109).

### buttons.css → standalone plugin — **confidence: high**

Designed for this: zero JS, no page-specific DOM assumptions, manifest
comment already calls it portable. Token dependency is five known
variables consumed from scene-dashboard.css's `:root` (`--jl-pink`,
`--jl-well`, `--jl-line`, `--jl-dim`; it defines its own
`--jl-pink-wash*` family itself).

- **Risks — operational, not technical:**
  - **Load order (the real one):** as a separate plugin its position
    relative to `dracula-theme` is no longer guaranteed by
    dracula-layout's manifest — and buttons.css v5/v8 contain rules that
    win specificity *ties* by load order (the `.source-controls` fix, the
    border-state rules). If stash loads the new plugin before
    `dracula-theme`, those could silently flip. This is the documented
    cascade-tie bug family. Needs live verification on BOTH instances
    after extraction, not just locally.
  - The hand-synced Scrape-button rule in scene-dashboard.css
    (`.edit-buttons-container .scraper-menu .btn-primary`) becomes a
    cross-*plugin* dependency instead of a cross-file one — comment both
    sides.
  - Users without dracula-layout get pink buttons on stock stash — fine,
    but all five tokens must come along or Secondary's
    `--jl-well`/`--jl-line`/`--jl-dim` silently resolve to
    nothing (no error, just broken-looking).

### Title scrubber → standalone plugin — **DONE (2026-09-02), port-as-is**

`TITLE_FILTER_WORDS` + `cleanTitleText()` + the two call sites in
clean-cards.js — pure DOM text work, no GraphQL, no layout coupling, no
CSS. The filter list is hardcoded English/JAV-specific junk words; as a
standalone plugin it could read the list from stash's plugin-settings UI.
The feature most likely to be useful to people who want nothing else from
dracula-layout.

- **Shipped as a straight port, not the settings-UI version** — the
  settings-UI idea (reading `TITLE_FILTER_WORDS` from stash's
  plugin-settings API) was flagged as a separate, unproven stretch goal
  and deliberately not attempted; the extracted plugin still hardcodes
  the word list, edit `title-scrubber.js` directly to change it.
- Extraction removed the code from clean-cards.js outright (not left
  duplicated) — dracula-layout users need `title-scrubber` installed to
  keep the feature, a coordination step communicated via both repos'
  CLAUDE.md/README, not a code risk.
- One incidental fix landed with the move: the scenes-grid card title
  write was unconditional (`titleEl.textContent = cleanTitleText(...)`,
  no equality check) unlike its sibling `cleanDetailPageTitle()`, which
  already guarded — see item #1 above, same underlying issue. The new
  plugin's version of both functions is guarded in both places now.
  `textContent` (not `nodeValue`) was kept as the write mechanism either
  way, per the original "port as-is" plan — not changed during the move.
- Source lives in `/srv/stash-plugins/plugins/title-scrubber/`, same repo
  and deploy flow as `copy-buttons`/`collection-colors`.

### Watched badge → standalone plugin — **confidence: medium-low — DON'T, unless asked for**

The badge itself is trivial; the data supply is the problem. It rides on
clean-cards' batched GraphQL infra for
`play_count`/`resume_time`/`play_duration`.

- **Risks:** extraction means either duplicating the per-scene fetch (two
  plugins each batch-querying 40+ scenes per grid page — a permanent tax
  to buy modularity) or extracting the batch layer as a shared
  data-service plugin, creating a three-plugin dependency chain with
  load-order and version-skew failure modes this ecosystem has no
  precedent for.
- **Verdict: don't extract unless someone outside dracula-layout actually
  asks for it.**

### Popover reorder/anchor/overflow-hide → standalone plugin — **confidence: medium**

Self-contained code (no GraphQL, own observers), generally useful feature.
But it has the most delicate observer choreography in clean-cards (boot
observer + per-bar observers + per-bar ROs + the performer-card
bail-out).

- **Risks:**
  - It collaborates with collection-colors by class-name convention
    (`.stash-collection-pill` exclusion) — after extraction that becomes
    an undocumented contract between two *other* plugins; three plugins
    coordinating by class name. Needs the convention documented.
  - The performer-card `clearAnchorState` path exists because of CSS in
    clean-cards.css (`flex: 1 1 0` stretch) — extracting the JS without
    that CSS changes behavior on performer cards. Both halves must move.
- Doable, but the coupling audit is most of the work.

### Performer hover popup — **leave as-is**

Already a quasi-shared service (`window.JLPerformerPopup`, exposed by
clean-cards.js, consumed by scene-dashboard.js). Extracting it converts
one internal soft-dependency into two external ones for zero user-visible
gain.

### Not worth extracting

- **fonts.css** — only useful with the rules that set
  `font-family: Quicksand`.
- **Studio-code relocation, scene-dashboard, entity-dashboard** — that
  *is* the plugin; the layout work is interdependent by design.
