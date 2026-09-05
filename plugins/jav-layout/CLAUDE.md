# CLAUDE.md — JAV Layout (stash UI plugin)

A UI plugin for [stash](https://github.com/stashapp/stash) that restyles scene
cards (Dracula) and replaces the scene detail page's nine-tab bar with a
five-mode switcher in the same slot: a persistent identity header (Studio
Logo/Code, Title, Original Title, Toolbar), then the mode switcher, then
Details/Tags/Performers/Metadata/File/History/Groups/Galleries/Custom Fields
all swapping in below it per mode — matching stash's original one-tab-visible-
at-a-time behavior, just as one merged Browse tab instead of five. (Reading
order, not tab order — Metadata moved below Performers 2026-09-01; see the
Layout section's own order-value table for the authoritative current
sequence if this list ever drifts again.)

Read `README.md` for install and user-facing behaviour. This file is the
working knowledge an agent needs before changing anything.

**Renamed 2026-09-02**: this plugin was "Dracula Layout" (id
`dracula-layout`) before this date — same code, same author, moved into
this repo and renamed alongside a full `dl-`/`data-dl-`/`--dl-` → `jl-`/
`data-jl-`/`--jl-` prefix rename across every CSS class, data attribute,
and CSS custom property (`.dl-card` → `.jl-card`, `data-dl-mode` →
`data-jl-mode`, `--dl-pink` → `--jl-pink`, `window.DLPerformerPopup` →
`window.JLPerformerPopup`, the `dl.collapsedCards` localStorage key →
`jl.collapsedCards`, resetting any user's saved collapsed-card state
once). Dated entries below from before that day still say
"dracula-layout" in direct quotes and historical descriptions — that's
this same plugin under its old name, not a different one; left as
accurate history rather than rewritten. **The rename had one external
casualty, found 2026-09-03**: collection-colors drops its pill into this
plugin's documented `.jl-scene-badge-slot`, and its selector still said
`.dl-scene-badge-slot`, so the sidebar collection pill silently vanished
for a day. Fixed on the collection-colors side (v1.0.1). If a `jl-`
name that another plugin is documented to consume ever changes again,
grep the sibling plugins in this repo for the old name before calling
it done — they're independent by design, which is exactly why nothing
breaks loudly.

## Files

| File | Role |
| --- | --- |
| `jav-layout.yml` | Manifest. **Filename sets the plugin ID.** Lists load order. |
| `fonts.css` | Self-hosted `@font-face` for Quicksand SemiBold (600), embedded as base64 — no external request, no dependency on the viewer's OS having it installed. Loads first; has no page effect of its own. See below. |
| `base-theme.css` | **The whole-app palette** — dracula-for-stash's `dracula-theme.css` (MIT), vendored 2026-09-02 with its Google Fonts `@import` removed and its translucent `--<pct>pct_*` variables derived via `color-mix()`; otherwise byte-for-byte upstream, so an upstream diff still applies. Makes the separately-installed `dracula-theme` plugin unnecessary (keep it disabled). Loads second, exactly where that plugin used to sit in the cascade. See the Theming section below. |
| `themes.css` / `theme.js` | **The color token contract.** Every `--jl-*` color token, with Dracula as the bare `:root` default, plus per selectable theme one `html[data-jl-theme="…"]` block for the `--jl-*` tokens and one for `base-theme.css`'s own variables; `theme.js` stamps that attribute from the plugin's `theme` setting and replaces the setting's free-text field in Settings › Plugins with a dropdown. Loads third (CSS) / first (JS). No other stylesheet contains a color literal any more — see the Theming section below. |
| `THIRD-PARTY-NOTICES.md` | MIT attribution for the palettes the themes reproduce. Add a row when adding a theme. |
| `clean-cards.css` / `clean-cards.js` | The pre-existing `customJavaScript.js` v5.4, split into a stylesheet and the script. Scene cards, performer hover popup, studio-code relocation, popover reordering. Performer name/disambiguation and studio-code copy buttons moved out to the standalone `copy-buttons` plugin (2026-08-31); title cleanup moved out to the standalone `title-scrubber` plugin (2026-09-02) — see the Settled decisions entries near the bottom. |
| `chips.css` | Every `.tag-item` chip (sidebar Tags card, card tag/marker/performer popovers, entity-page tag lists) colored by role: tags and performers in `--jl-link`, marker chips (told apart by their `?t=` timestamp link) in `--jl-accent`, all `color-mix()` washes off the tokens. CSS only. See Settled decisions. |
| `buttons.css` | Site-wide native-Bootstrap button restyle (`.btn-primary`/`.btn-secondary`/`.btn-danger`/etc.) — CSS only, no JS, no page-specific DOM assumptions. See below. |
| `scene-dashboard.css` / `scene-dashboard.js` | The scene-page dashboard. This is the new code. |
| `entity-dashboard.css` / `entity-dashboard.js` | One config-driven module for every "entity with a tabbed relations list" page — performer, studio, group, tag. Restyles each one's native tab strip as a `.jl-modes` pill row, plus identity-header font/color tweaks. Replaced separate performer-dashboard.\*/studio-dashboard.\* files once a third and fourth page made the duplication itself the problem — see below. |
| `dev/fixture.html` | Standalone reproduction of the real scene-page DOM. **Stale** — built for the two-column grid dashboard, see Testing. |
| `dev/check.js` | 30 layout/behaviour invariants. **Stale**, same reason. |

`clean-cards.*`, `buttons.css`, `scene-dashboard.*` and `entity-dashboard.*`
are independent. Changing one should not require
touching the others.

### Theming — themes.css / theme.js (2026-09-02)

**Every color in this plugin is a `--jl-*` token defined in `themes.css`;
the other four stylesheets contain zero color literals.** Tokenized in
one pass on 2026-09-02 (about 80 literal sites across clean-cards.css,
buttons.css, scene-dashboard.css and entity-dashboard.css, only 19
distinct values among them) as the prerequisite for selectable themes.
Verified pixel-identical to the pre-tokenization render on the scene,
grid and performer pages under the Dracula default — 0 differing pixels
across three runs — so the refactor changed no visible output.

- **Tokens are named by role, not hue.** `--jl-pink` → `--jl-accent`,
  `--jl-cyan` → `--jl-link`, `--jl-lilac` → `--jl-heading`, and
  `--jl-pink-wash*`/`--jl-pink-border` → `--jl-accent-wash*`/
  `--jl-accent-border`. Older entries in this file still use the old
  names when quoting history; they mean the same tokens. Everything else
  (`--jl-text`/`-muted`/`-dim`, `--jl-fg`/`-fg-dim`, `--jl-line`,
  `--jl-well`, `--jl-danger`, `--jl-on-danger`) kept its name.
- **Two tiers: base tokens a theme sets, derived tokens computed with
  `color-mix()`.** A theme block sets ~14 base values (accent, link,
  heading, the text ramp, fg, three surface bases, popup text, danger,
  on-danger, ok). Every alpha step — the five accent washes, the four
  line strengths, the well, the code/date surface, the mode-bar pill
  fill — is `color-mix(in srgb, var(--jl-x) N%, transparent)` off a
  base, so a theme never restates its accent at five alphas and the
  wash/line/well relationships hold across themes. Odd one-off alphas
  (the history badge's 13 %/33 %, the selected pill's 15 %, Original
  Title's 55 % heading) stay as inline `color-mix()` at their original
  percentages rather than being rounded to a named step — the
  pixel-identical claim above depends on that.
- **The default theme is the absence of the attribute.** `:root` holds
  Dracula; `theme.js` never writes `data-jl-theme="dracula"`, it removes
  the attribute. An unknown or missing setting therefore degrades to
  Dracula with no code path involved — the same failsafe shape as
  constraint 6.
- **Two-stage apply, cache then setting.** `theme.js` loads first among
  the scripts, applies the `localStorage` (`jl.theme`) value
  synchronously to cover the gap before the settings round trip, then
  reads `configuration.plugins["jav-layout"].theme` via GraphQL and
  applies that as authoritative (a setting changed in another browser
  must win over this one's cache). Both writes go through an
  equality-guarded `apply()`.
- **The dropdown in Settings › Plugins is a sibling row, not a
  replacement.** Stash renders each declared setting as a `.setting` row
  with id `plugin-jav-layout-theme` (h3 / current value / description /
  an Edit button opening a free-text modal — confirmed live, same
  mechanism collection-colors relies on). `theme.js` hides that row and
  inserts its own `.setting.jl-theme-setting` row before it, built with
  `createElement`, never re-parenting anything React owns. Saving goes
  through `configurePlugin` with the existing settings map merged in —
  `configurePlugin` replaces the whole map, the config-clobbering
  landmine collection-colors already hit. The dropdown reverts itself
  and says so if the save fails. `window.JLTheme.preview(id)` switches
  without persisting, for other plugins or the console.
- **Scope of a theme: the whole app, since the base theme was vendored
  (same day, later).** The first cut recolored only what this plugin
  paints, with the rest of the app still painted by the separately-
  installed community `dracula-theme` plugin — a dependency the user
  explicitly didn't want to ship on. Rather than bridge onto that
  plugin's variable names (re-coupling to it), its stylesheet was
  brought in as `base-theme.css` under its MIT license:
  - Analysis before vendoring: once comments are stripped, upstream has
    only 38 color literals outside its `:root` (a 20-step rating-color
    ramp, black/white alphas, two odd Bootstrap validation borders) —
    it is variable-driven end to end, so theming it means redefining
    variables, not rewriting rules. (An earlier count of "173 literals"
    in this file's history was inflated by comments.)
  - Two transforms, nothing else: the leading `@import` of Google Fonts
    is removed (stash concatenates plugin CSS into one response, and an
    `@import` not at the very top is ignored — so it could never work
    here, and it was already a CSP violation on stricter setups), and
    the 48 `--<pct>pct_<name>` rgba() variables become
    `color-mix(in srgb, var(--<name>) <pct>%, transparent)` off their
    base — percentage taken from the upstream alpha, not the name, since
    upstream defines `--10pct_muted_purple` twice and the later, .25
    definition is the one that was ever in effect. Three upstream bases
    were a few units off their own named color (alt_bg, cyan, overcast)
    and are normalized onto it; invisible behind ≤75 % alpha.
  - **Lato is deliberately not self-hosted** in the import's place.
    Only `.scene-card` asks for it, and checked live via
    `CSS.getPlatformFontsForNode` with the external stylesheet
    injected: the Google import was never what painted Lato on this
    instance — the OS-installed Lato faces (Medium/Semibold, which
    Google's Lato doesn't ship) were, and a web-font declaration would
    replace those with synthetic 400/700. So Lato stays an OS-font
    reliance, like every non-Quicksand face here.
  - **Verified pixel-identical to the external plugin**: grid,
    performers list, tags list, settings at 0 differing pixels; scene
    page 11 px and performer page 2 px, all in the video progress bar
    and a hover state. The baseline for that comparison was taken with
    dracula-theme *disabled* in stash (the user had already disabled
    it) by injecting the external stylesheet's text as a `<style>` at
    `DOMContentLoaded` — after the app bundle's `<link>`, before the
    plugin `<link>`s React adds later, i.e. its real cascade position.
    Injecting it at document start instead put it *before* the bundle
    and produced a false 15 % sidebar diff from tied-rule order; if this
    technique is reused, the injection point matters.
  - Per theme, `themes.css` now carries a second block setting
    base-theme.css's ~22 base variables (background/foreground/
    selection/comment, the seven hues, superdark/alt_bg/intermediate_bg,
    the misty/overcast/gloomy text ramp, tag_background, disabled, the
    ansi/vsc darks), and one shared `html[data-jl-theme]` rule derives
    the families upstream hand-picked for Dracula (pale/dark/spdk
    tints and shades, wisp/ash/smoke neutrals, muted_purple, ANSI
    brights) with one `color-mix()` formula per family. Dracula matches
    no theme attribute and keeps upstream's literals exactly.
  - **A comment landmine hit while writing that block:** a CSS comment
    containing a glob spelled `pale_*/dark_*` ends at the `*/` and
    silently swallows the next rule — the Mocha base block simply
    didn't exist until the parsed stylesheet was inspected via
    `document.styleSheets` and the `html[data-jl-theme="catppuccin-
    mocha"]` rule with a `--background` declaration was found missing.
    Check `cssRules` for the block you just wrote, not just the page.
- **`--selection` is the CARD surface, not a highlight — map it as one.**
  The base theme paints every card type (scene, performer, gallery,
  image, marker, tag) with `.card { background-color: var(--selection) }`,
  so whatever a palette calls "selection"/"highlight" is the wrong
  thing to reach for; what matters is the lift over the page ground.
  Dracula's is about +30 per channel (#282a36 → #44475a). The first
  mappings took each palette's step by NAME and got Mocha +40
  (surface1) and Kanagawa +53/+53/+69 (sumiInk6, its lightest ink — a
  washed lilac slab, reported live as the worst case). Fixed 2026-09-02
  to surface0 (#313244) and sumiInk5 (#363646), with `--alt_bg`/
  `--intermediate_bg`/`--tag_background` re-ordered underneath so the
  ramp superdark < background < alt_bg < intermediate_bg < selection
  still holds; Rosé Pine's highlight-med was already at +33 and stayed.
  Prototyped by CSS injection and screenshotted before/after on the
  scenes grid before touching themes.css. `--selection` has 73 uses
  (hover rows, some input fills too), so a change moves those by the
  same step — same role, checked on the sort dropdown and Settings.
- **Nine more themes added 2026-09-03** (Tokyo Night, Moonlight,
  Synthwave '84, Night Owl, Aura Dark, Andromeda, Horizon Dark, Sonokai,
  Poimandres — the colorful end of the scheme review, all MIT), then
  **Night Owl, Andromeda and Poimandres removed the same day** as
  near-duplicates: measured by Lab ΔE on page/card/accent they sat
  within ~10 of Tokyo Night, Horizon/Dracula and Kanagawa respectively,
  and a theme whose only distinct role is its link color isn't a theme.
  **Monokai (classic, from VS Code's MIT theme extension — not Monokai
  Pro, which is commercial) added in their place**; its pink is its
  red, so danger takes VS Code's `#f44747` error color. Each is
  ONE combined `html[data-jl-theme]` block holding both the `--jl-*`
  tokens and the base-app variables, unlike the first three's two
  blocks; either shape works, the combined one is just shorter. Values
  a palette doesn't have are marked `derived` inline; three palettes
  (Horizon, Sonokai, Poimandres) use their only red as the accent, noted
  in their block comments. Card grounds were picked by lift over the
  page, per the `--selection` note above, not by palette names. Every
  block was verified live to parse (cssRules count) and to paint the
  page ground, since a stray `*/` swallowed a whole block once before.
- **Adding a theme**: two `html[data-jl-theme="<id>"]` blocks in
  `themes.css` (the `--jl-*` tokens and the base-app palette, each with
  an attribution comment), an entry in `THEMES` in `theme.js`, a row in
  `THIRD-PARTY-NOTICES.md`, and the name in the manifest's setting
  description. Dark palettes only — the surface tokens and every wash
  alpha were tuned against dark grounds, and a light flavor needs those
  relationships redesigned, not recolored.
- **"Dracula Pro" was dropped from every reference** the same day
  (README, manifest, clean-cards.css/js headers, this file's opening
  line). The accent value itself (`#ff80bf`, a Dracula PRO pink; classic
  Dracula's is `#ff79c6`) was left as-is — a hex value isn't what that
  product's license restricts, and changing it would have altered the
  plugin's look. Swap it in `themes.css` if zero ambiguity is ever
  wanted.

### Action placement — one convention for every page (buttons.css §4, 2026-09-02)

Stash puts its page-level action buttons in four different places,
measured live on this instance before anything was written:

| Page | Viewing | Editing |
| --- | --- | --- |
| performer | `.details-edit` in the header, 460px down (the photo pushes it) | `.details-edit.col-xl-9` rendered **twice**: `.mb-3` above the form, `.mt-3` below it |
| studio / group / tag | `.details-edit` in the header near the top | `.details-edit.col-xl-9.mt-3` below the form only |
| scene / gallery / image | nothing visible; actions live in the Edit tab | `.edit-buttons-container`, the FIRST child of the edit `<form>`, inside a scrolling sidebar |
| list pages | nothing | the toolbar becomes a selection bar (`.has-selection`) |

The convention, enforced with CSS only (constraint 1 holds — nothing
here moves a React node), so it can be checked page by page:

- **Viewing: actions sit in one pill directly under the identity
  block, styled exactly like the mode bar** (`--jl-pill-bg`, `--jl-line`,
  8px, 3px padding, 28px segments = 36px), page-centered on the same
  axis as the mode bar and the list toolbar, 14px above the mode bar.
  Buttons are segments: the NEUTRAL ones (Auto tag…, Merge…) take the
  mode bar's own resting color, `--jl-muted`, instead of tier 1's bright
  `--jl-fg` (requested live 2026-09-02, "same font color as the
  un-highlighted mode buttons"); the ROLE ones keep their color — Edit/
  Submit accent, Delete danger — because a first pass that muted all
  five was immediately reported as "too consistent, we lost the role
  color". Hover: `.jl-mode:hover` verbatim for neutrals, the role's own
  wash for accent/danger. Every state restates color, background and
  border; the active rules carry tier 1's `:not(:disabled):not(.disabled)
  :active` chain to stay ahead of it. Verified via `CSS.forcePseudoState`
  + live computed style — note that `CSS.getComputedStyleForNode` read
  under a forced pseudo-state returned the un-forced values and looked
  like a failure; read `getComputedStyle` from the page instead.
- **Editing: actions are a bar pinned to the bottom of whatever
  scrolls.** On entity pages that is a `position: fixed` footer across
  the whole viewport, with matching `padding-bottom` on
  `.detail-header.edit` (74px, 116px below md where the bar wraps) so
  the last field always scrolls clear — verified on desktop and at
  390px. On the tabbed pages it is `position: sticky; bottom: 0` inside
  the sidebar that scrolls, which the bar already spans edge to edge.
  Both use the SOLID deep surface (`--jl-surface-deep`, `!important`),
  a hairline top border and an upward shadow. Performer's top duplicate
  is hidden. The bars' neutral buttons (Scrape with…, Set image…, the
  scene bar's demoted Scrape) rest at `--jl-muted` like the viewing
  pill's neutrals and brighten to `--jl-text` on hover/press; they keep
  tier 1's bordered ghost look (only `color` is claimed), and Cancel/
  Save keep the accent, Clear Image/Delete keep danger. **The mode bar
  is hidden while an entity is being edited** (`body:has(.detail-header.edit)
  .jl-modes-row` — `body`, not `main`: stash's `.main` is a class on a
  div, there is no `<main>` element, which the first draft assumed): stash hides the relations list in edit mode
  but leaves the tab strip's box, so the mode bar was the one thing
  left below the form and the fixed footer sat on top of it — measured
  on all four entity pages (performer at page end, studio and group at
  any scroll, tag clear by 44px). Nothing else exists below the header
  in edit mode (no toolbar, cards or pager — confirmed), so that was the
  footer's only possible overlap. The tabbed pages can't hit this: their
  bar is sticky inside the pane, below the mode bar. On the tabbed pages the toolbar is the form's first child
  and sticky can only hold an element back, never pull it forward, so
  the form becomes a flex column and the toolbar takes `order: 99`
  first (`form:has(> .edit-buttons-container)`); verified pinned in
  `.scene-tabs`, `.gallery-tabs` and `.image-tabs`, with "Scrape with…"
  opening upward. The scene page's marker creation form (Markers mode →
  Create Marker; `form > .form-container + .buttons-container > .d-flex
  > Save, Cancel`, same shape as the Edit form) gets the identical bar
  treatment via `.tab-pane form > .buttons-container` (2026-09-03,
  requested to match the performer edit bar): last in a flex-column
  form, sticky to the sidebar's bottom, deep surface, centered, tier-2
  sized, Cancel muted. Verified on scene 10273 by injecting the
  working-tree plugin — note the form is short enough to fit, so the
  bar sits at the form's end there and only pins once the form is
  taller than the sidebar, which is sticky working as intended.

  **Reported live twice as "the sticky bar is transparent" — it never
  was, and both real causes are worth remembering.** (1) On the scene
  page the vendored base theme paints the bar with `#scene-edit-details
  .edit-buttons-container { background-color: var(--background) }` —
  an id selector, which out-specifies any class rule — so the bar took
  the page color: opaque, but indistinguishable from the page, with the
  form appearing to scroll straight under the buttons. (2) Even painted
  correctly, the first fills chosen (`--jl-surface` at 95 %, then 97 %)
  are only a shade off Dracula's page ground, and a sticky bar inside
  the form column left the rest of the page scrolling in plain view on
  either side of it — which reads as a translucent strip. Diagnosed the
  way the user suggested: scroll an image behind the bar and screenshot
  (the performer photo at a 320px-tall viewport, the scene's cover
  image in the form). Fixed by the id-beating `!important`, the deep
  surface, and the full-width fixed footer. Check a bar against an
  image, not against form fields — fields are the same color as the
  page and hide exactly this.

**How the viewing pill gets one position when the header doesn't
give it one** (entity-dashboard.css, the block above the mode-bar
margins): the row is the last child of the header's TEXT column, but
the header's height is set by the image or by wrapper spacing, so the
row ended 14/34/59/79px above the header's bottom on performer/studio/
group/tag. A flex-column + `margin-top: auto` version was tried and
does nothing — the text column hugs its content. What works: make the
Bootstrap `.col` wrappers static (they're `position: relative` by
default; performer has one extra), which makes `.detail-container`
the containing block on all four pages; reserve a 64px band with
`padding-bottom` on `.detail-header` (14 + 36 + 14); and absolutely
position the row at `bottom: -50px` (14 − 64, measured against the
container's bottom, which is the header's *content* edge) with `left:
50%; transform: translateX(-50%)`. Below 768px the row goes back into
flow and centers in the now full-width column, because it wraps to two
lines there and a fixed band can't fit it. Verified live: row bottom
to header bottom = 14px, row/mode-bar/toolbar centers equal, name
offsets unchanged, on all four pages.

**Rhythm**: with the row anchored, the mode bar's per-entity centering
margins (15/22/14/14px, each measured to split a header of arbitrary
height) collapsed to one `margin: 0 0 14px` for all four entities, and
the entity tab panes' `.item-list-container` lost stash's 15px
`padding-top`, so header→pill, pill→mode bar and mode bar→toolbar are
all 14px on every entity page. The list pages proper keep that padding
— there it separates the toolbar from the nav bar.

**The list toolbar** (buttons.css §4c) took the same treatment: it
hugs its content and centers (`margin-inline: auto !important` — stash
sets its 40px side margins from a higher-specificity rule, confirmed by
the top margin overriding while the sides didn't), its controls are
28px ghosts with the mode bar's type recipe (dim wash on hover, accent
wash + accent text when `.active`, `:active` or `[aria-expanded]`), the
search field is a `--jl-well` box, group segmentation is one hairline
`box-shadow` between siblings, and the zoom slider's `accent-color` is
the accent — the base theme's purple no longer appears in the row.
Sizing is scoped to `.filtered-list-toolbar` only — the v1 blanket
`.btn` padding is what broke the pager, and nothing here is blanket.
The `.has-selection` state (Select All / Play / bulk actions) inherits
the same look.

**Three follow-ups reported live from the scenes list, 2026-09-03**
(buttons.css §4c/§4d): (1) the sort control's label button sits inside
`.input-group-prepend`, which Bootstrap's inner-corner squaring never
reaches, so its hover wash kept four rounded corners and ran into the
caret segment — its right corners are squared now; (2) the pagination
group's outline came from each button's own borders, and stash's
bundle strips the first button's left border while the "1 of N"
dropdown wrapper has none, so the outline dropped out at those edges —
the pager is now the same pill as the toolbar (§4d: one outline on
`.pagination.btn-group`, 28px borderless segments with the mode bar's
6px radius, muted/text/accent states; `!important` on the segment
borders because the bundle sets border-left/right per button with its
own longhands), scoped to `.pagination` so it is still not blanket
sizing; (3) the toolbar's bottom edge touched the pager's top edge
(measured 0px), so the toolbar carries `margin-bottom: 8px`.

### fonts.css — self-hosted Quicksand

Exists to close a gap the OS-installed-font approach (used everywhere else
in this plugin — Lato, Inter, Quicksand itself originally) can't close:
**verifying a font renders correctly on the machine running Claude's own
CDP checks proves nothing about whether it renders anywhere else.** Every
other font choice in this plugin still works that way, on the reasoning
that Lato/Inter/etc. are common enough OS-preinstalled fonts that the risk
is low — Quicksand specifically is not, and got upgraded to actually
shipping with the plugin rather than hoping for it.

Two real, separate problems surfaced this, on two different machines:

1. **Reported live**: a Firefox instance elsewhere threw a CSP
   `style-src-elem` violation for
   `fonts.googleapis.com/css2?family=Lato...&family=Roboto+Mono...`.
   Traced via `fc-list`/grep to the *separate*, third-party `dracula-theme`
   plugin (not this one) — its own CSS opens with
   `@import url("https://fonts.googleapis.com/css2?family=Lato:...")` for
   its own base typography. A stricter CSP than this dev instance's blocks
   that external request outright. **Not this plugin's bug** — confirmed
   by grepping every file here for `@import`/`fonts.googleapis`/
   `@font-face` and finding nothing, before saying so.
2. **The actual reason this file exists**: even once (1) was ruled out,
   Quicksand itself was only ever confirmed against *this* server's
   installed fonts (`fc-list`) and rendered in headless Chrome *on this
   same server* (`CSS.getPlatformFontsForNode`) — neither proves anything
   about a real viewer's machine, iOS included, which resolves
   `font-family` against fonts installed locally on *that* device. Since
   Quicksand isn't a common preinstalled OS font, most other machines were
   silently degrading to the `"Liberation Sans", Arial, sans-serif`
   fallback — no error, just quietly not the font actually chosen.

Fixed by self-hosting: `@font-face` + `src: url(data:font/woff2;base64,...)`
for the SemiBold (600) weight — the only weight this plugin actually uses
(scene titles, performer/studio names) — sourced directly from
`fonts.gstatic.com` (Google's own CDN, fetched with a browser UA to get
real `.woff2` files, not the TTF fallback given to unspecified clients),
not re-derived from the local `fc-list` install. That distinction matters:
this server's local Quicksand package only ships static 300/400/500/700
weights, no true 600, so whatever the browser had been substituting for
`font-weight: 600` before this file existed was already an approximation,
not genuine SemiBold — the embedded file is a closer match to the
originally intended look, not just a more portable copy of what was
already there. Two `unicode-range`-scoped `@font-face` blocks (`latin`,
`latin-ext`, ~15KB each as WOFF2) cover the range actually needed for
scene titles and romanized names; the Vietnamese-range subset Google also
offers was left out to keep the embed smaller — add it the same way
(fetch from `fonts.gstatic.com`, base64-encode) if a title ever actually
needs it.

Loads first in the manifest, ahead of every file that sets
`font-family: Quicksand`, though `@font-face` registration doesn't
actually require declare-before-use ordering the way a compiled language
would — placed there for documentation clarity, not correctness. Confirmed
live post-change via `CSS.getPlatformFontsForNode`: every Quicksand
consumer now reports `isCustomFont: true` (the embedded face, not an OS
lookup), same visual result as before this file existed, confirmed
side-by-side.

### buttons.css — site-wide button restyle

Started as a one-page mockup for the scene Edit toolbar
(`.edit-buttons-container`: Save/Delete/Scrape with…), the one pane this
plugin had never styled. Generalized to every page on explicit request,
since stash's native buttons all use the same Bootstrap classes everywhere
and this plugin's CSS already loads site-wide — no per-page work needed for
the color half of it.

**Went through two revisions after live feedback — the history matters,
don't silently revert to v1:**

- **v1** shipped with an invented warm accent (`#d97757`, clay/terracotta)
  and generous padding (9px 18px, 8px radius). Feedback once seen live: too
  chunky next to the mode bar's own pills. The actual bug was radius,
  matched to `.jl-modes`' *outer bar* (8px) instead of the individual
  `.jl-mode` *pill* (6px) — a mismatch, not just "too big."
- **v2** drops the invented accent entirely and reuses `var(--jl-pink)`
  directly — not the same hex as a new token, the mode bar's own
  selected-pill *mechanism*: `[aria-selected="true"] { color: var(--jl-pink);
  background: rgba(255,128,191,.15) }`, a translucent wash, never a solid
  fill. Adapted into a three-role system (primary/danger/secondary all
  share the same wash construction, differing only in hue and in how far
  they escalate on hover — see below) and resized down toward `.jl-mode`'s
  own 5px/13px/12px recipe, close but not identical, since these are
  freestanding buttons with their own border rather than segments inside a
  shared pill bar.
- **v3**: two fixes after "nav bar/filtered list/button text feels too
  heavy." First, `.btn { font-weight: 600 }` was global with no
  exclusion — safe for *size* (confirmed via the live injection test
  below), but nothing protected *weight* the way native's own
  `.btn.minimal{background:...}` rule already protected color, so the main
  nav bar (Scenes/Images/.../Donate/the "Stash" logo — all literally
  `.btn.btn-primary.minimal`) silently inherited 600 weight despite being
  category 6 ("leave native") in the button categorization pass. Fixed by
  scoping the whole tier-1 rule to `.btn:not(.minimal)` — nav bar and
  scene-card rating/favorite/count-value chips (also `.minimal`) are now
  fully exempt from color *and* weight/font, not just color. Second: the
  weight reduction (600→500) was paired with an actual font change,
  `Lato, "Liberation Sans", Arial, sans-serif` — not just a lighter weight
  of whatever was already rendering. Verified installed on this server via
  `fc-list` before picking it (genuinely zero-dependency, same as every
  other font choice in this plugin), and confirmed via
  `CSS.getPlatformFontsForNode` that it actually renders as Lato, not a
  silent fallback. Bonus: Lato is stash's own native font for scene-card
  titles (`Lato, Inter, Helvetica, Arial, sans-serif`) — buttons/toolbar
  text had been landing on Liberation Sans purely by fallback accident,
  never actually matching the cards next to it; this fixes that
  incidentally, not by design intent going in.
- **v4 (current)**: font-family only, reverted from Lato to `"Segoe UI",
  sans-serif` — byte-identical to the nav bar's own native declaration —
  on explicit request to normalize every button in the app (nav bar,
  modal confirmations, detail-edit rows) to one font. Weight (500) and
  the `:not(.minimal)` exclusion both carry over unchanged from v3; only
  the font-family value moved. "Segoe UI" still isn't installed here
  (same as it never was for the nav bar's own rule), so this resolves to
  Liberation Sans, not literal Segoe UI — the point is matching the nav
  bar's own fallback *result*, not achieving a font that was never
  actually available. See the Settled decisions entry near the top of
  this file for the full framing.
- **v5 (2026-09-01)**: tier-2 padding/font-size now matches
  `.jl-mode`'s own 5px/13px/12px/600/0.3px recipe *exactly*, not "close
  but not identical" as v2's original reasoning chose — plus a new
  border-color: transparent rule, on tier-2 buttons only, at rest and
  through `:hover`/`:active`. Explicit live decision after a mocked-up
  options comparison (three levels: coverage-gap fix only / + exact
  type match / + borderless), not a silent extension of v2's own
  reasoning. Two concrete things fell out of building it:
  - **`.scene-markers-panel .btn-primary`/`-secondary`/`-danger` joined
    the tier-2 opt-in container list.** Create Marker was found still on
    native Bootstrap padding/font-size — neither `.modal-footer` nor
    `.edit-buttons-container` cover it. Scoped to the three role classes
    specifically, not a bare `.btn` on that container: it also holds
    every marker row's own compact `.btn-link` title/Edit controls, the
    exact class of thing the original v1 blanket-padding test broke;
    those never carry a role class, so this scoping excludes them for
    free without needing an explicit exclusion.
  - **The Scrape split-button pair needed an explicit carve-out from the
    new border rule.** `.edit-buttons-container` also holds
    `.btn-primary.dropdown-toggle` ("Scrape with…") joined to an
    icon-only `.btn-secondary.dropdown-toggle` caret with zero text —
    confirmed live before writing the exclusion, not assumed. Stripping
    borders there would leave the icon-only half with nothing at all
    signaling it's clickable, since the pair currently reads as one
    control specifically because they share a continuous border.
    `:not(.dropdown-toggle)` on every rule in this section (rest,
    hover, active) keeps that pair fully bordered, sizing aside.
  - **Border-color has to be restated for `:hover`/`:active` explicitly,
    not left as a side effect.** A same-day sibling bug in the
    copy-buttons plugin (see its own CLAUDE.md-equivalent memory) was
    exactly this: a scoped rule tied tier 1's `:hover`/`:active` rules on
    specificity (both two class-level selectors) and won on source
    order alone — here that tie happens to land in this rule's favor
    (it's later in the file), so it "just works" without the explicit
    states — but relying on that would silently break if this section
    ever moved earlier in the file. Restated explicitly so the
    borderless-at-every-state behavior is robust, not a source-order
    accident — confirmed live via `CSS.forcePseudoState` that border
    stays `rgba(0,0,0,0)` through hover and active, matching `.jl-mode`'s
    own computed border (`0px none` at every state, unselected/selected/
    hovered alike) exactly.
  - **Secondary reads noticeably plainer at rest now — flagged before
    building, confirmed live after.** Secondary was already
    `background: transparent` at rest, so removing its border too
    leaves genuinely nothing but text until hovered (Cancel in a
    confirmation modal, live-screenshotted: no visible button boundary
    at rest at all). Primary/Danger don't have this problem — their
    16%/transparent-with-red-text washes still read as a button shape
    on their own. Shipped as explicitly chosen (this exact trade-off was
    named before the go-ahead), not something to silently "fix" by
    giving Secondary a rest-state wash — if that's ever wanted, treat it
    as its own follow-up decision, not a bundled correction here.
- **v6 (2026-09-01)**: reverted straight back to Option B — the
  border-removal rule (v5's whole second half, both the rest-state and
  the hover/active restatement) is commented out as one block, not
  deleted, specifically for an easy revert back to v5/Option C if that
  direction is wanted again. Everything else from v5 stays: the exact
  5px/13px/12px/600/0.3px sizing match, the `.scene-markers-panel`
  container addition, the `:not(.dropdown-toggle)` exclusion (now inert
  since nothing currently reads it, but left in place so uncommenting
  the block reactivates a already-correct rule, not a stale one).
  Confirmed live: Save/Delete/Cancel/Create Marker all show their
  border again (pink/red/neutral per role, from tier 1's own untouched
  rules — nothing in tier 1 was touched by this), sizing unchanged, 0
  idle mutations.
- **v7 (2026-09-01)**: audited the rest of the app after the
  user flagged Settings as still native — extended the tier-2 sizing
  list with two more containers, both confirmed against stashapp/stash's
  own source (`ui/v2.5/src/components/`) before shipping, per explicit
  request to check upstream rather than keep hand-testing page by page:
  - **`.details-edit`** — Cancel/Save/Set image…/Clear Image/Scrape with…
    on performer/studio/group/tag detail pages' edit mode. One shared
    wrapper across all four entity types (confirmed live on each) —
    scene alone uses the separate `.edit-buttons-container`; there's no
    single class spanning all five entity types' edit toolbars.
  - **`.setting .btn:not(.btn-sm)`** — every Settings tab's real
    actions. `.setting` is one real component
    (`Settings/Inputs.tsx`'s `Setting`, confirmed by reading the actual
    source, not inferred from class names), used by every settings
    panel. It is **not** Settings-page-scoped, though — the same
    component also renders inside the "Generate" dialog reachable from
    ordinary Scene/Image/Gallery pages (`GenerateDialog.tsx` →
    `SettingSection` → `ModalSetting`), so this rule reaches both,
    confirmed from source though not manually click-tested live (a
    react-bootstrap dropdown toggle didn't respond to a scripted click
    in headless verification — same CSS selector already proven correct
    on Settings, so treated as sufficiently confirmed without forcing
    that specific interaction). `:not(.btn-sm)` is required, not
    defensive dressing: `PluginTasks.tsx` renders per-plugin task
    buttons as `size="sm"` directly inside a `<Setting>` — confirmed in
    source, then confirmed live that `.btn-sm` items (Flush
    Dependencies, Reload tagged scenes, etc — not core stash, likely
    from another installed plugin, but styled by the same rule either
    way) correctly stayed at native compact sizing while `.setting`'s
    own full-size buttons (Edit, Generate API key, Clean, Backup, etc)
    picked up the pill-matched recipe.
  - **No universal fix exists app-wide** — checked via source, not
    assumed. No shared `<Button>` abstraction anywhere in the app
    (`ui/v2.5/src/components/Shared/` has single-purpose button
    components, not a reusable wrapper); Tagger and bulk-edit dialogs
    weren't confirmed to share a reusable class either from source or
    live spot-checks in the time available. This list is still opt-in,
    container by container — just two real, substantial wins added,
    not a general solution.
  - Verified live: Settings > Library's "Edit" buttons now match Save/
    Create Marker's look exactly (screenshot-compared); pagination/
    rating-chip compact controls elsewhere on the scenes list stayed
    untouched (the original v1 regression this whole tiered system
    exists to prevent); 0 idle mutations across Settings (Library/
    Interface/Tasks/Plugins tabs) and the performer edit toolbar.
- **v9 (current, 2026-09-02)**: every role-color rule (Primary/Success,
  Secondary, Danger/Outline-Danger — rest, hover, active, AND disabled)
  now carries `:not(.minimal)`, completing what v3's tier-1
  `.btn:not(.minimal)` scoping started but the role blocks never got.
  User reported "native button color flashes on when any nav-bar button
  is pressed" — the nav items are literally `.btn.btn-primary.minimal`,
  and their protection from this file's role rules was an *accident of
  specificity*, not a real exemption: at rest/hover, native's
  `a.minimal`/`button.minimal` (element + class) happened to out-rank
  this file's bare `.btn-primary` (one class), but the `:active` rule
  (four class-levels, written to match native Bootstrap's selector shape
  for the earlier active-leak fix) out-ranked native's
  `a.minimal:active:not(:disabled)` (three classes + element) — so ONLY
  the pressed state leaked, flashing the full Primary treatment
  (`--jl-pink-wash-active` + pink border/text) on every nav press.
  Confirmed via `CSS.forcePseudoState` + `getMatchedStylesForNode`
  before changing anything: the computed active background was this
  file's own pink wash, nothing native. The `.source-controls` rules
  (v8) are deliberately untouched — those buttons are never `.minimal`
  and their selector shape is a load-order tie-break design. Verified
  live post-fix: nav press now shows dracula-theme's own native minimal
  treatment (purple text + 10%-purple wash, identical to its hover);
  non-minimal buttons keep the full pink 0.16→0.26→0.34 progression at
  rest/hover/active; the scene-card-style minimal chips (favorite etc.)
  also stay native in every state; 0 idle mutations. **The lesson, added
  to the partial-property-override one from earlier: an exemption that
  only holds because a competing rule happens to out-specify yours in
  *some* states is not an exemption — write the `:not()` on every rule
  of the block, or the first state whose selector grows past the
  competing rule's specificity silently un-exempts itself.**
- **v8 (2026-09-01)**: user reported Settings > Plugins'
  "Available Plugins"/"Available Package Sources" tables' Edit buttons
  still solid-purple and flat after v7 — a real color bug, not just
  missing tier-2 sizing. `.setting` doesn't reach these tables at all
  (their DOM chain is `.source-controls` > `.package-source` > `table` >
  `.package-manager-table-container`, nothing to do with Settings'
  `Setting` component), and worse: even tier-1's own global Primary
  color rule was losing outright. Root cause confirmed via
  `CSS.getMatchedStylesForNode`, not assumed: a separately-installed
  theme plugin ships `.source-controls .btn-primary { background-color:
  var(--purple); ... }` — two classes, genuinely higher specificity than
  this file's bare `.btn-primary` (one class), so it legitimately won
  regardless of load order. Same bug family as the `.alias-head`
  override documented elsewhere in this project, hitting Primary's own
  color for the first time. Checked both `:hover` (same higher-
  specificity problem, `.source-controls .btn-primary:hover`) and
  `:active` (no competing scoped rule there, so this file's own active
  rule — equal specificity, later in source order — already won without
  help) via `CSS.forcePseudoState` before writing anything, rather than
  assuming both states needed the same fix. Fixed by adding
  `.source-controls .btn-primary`/`:hover` matching native's own
  selector shape exactly, so the specificity tie breaks on load order.
  Left sizing alone — these are `.btn-sm` (a dense table's own row
  actions), matching the established "don't resize `.btn-sm`" precedent
  elsewhere in this file; the reported "flat" complaint was about the
  solid-fill color, confirmed by what fixing just the color produced.
  Verified live: rest state shows the translucent pink wash instead of
  solid purple, hover deepens it correctly (`rgba(...,0.26)` + solid
  pink border, matching Primary's hover recipe elsewhere exactly), 0
  idle mutations.

**Split into two tiers, deliberately not treated the same way:**

1. **Color, radius, font-weight, font-family — applied globally to every
   real `.btn` (`:not(.minimal)`)** — `.btn-primary`/`.btn-secondary`/
   `.btn-danger`/`.btn-success`/`.btn-outline-danger`. Confirmed *size* safe
   by injecting the rule set live into the scenes list page and
   screenshotting: grid/list/tag toggles, pagination, scene-card rating/
   favorite chips all kept their native size, because none of these
   properties affect box dimensions — weight/font safety is a separate
   claim the `:not(.minimal)` exclusion handles now (see v3 above), size
   safety alone doesn't cover it.
   - **Primary** (`--jl-pink` text + `--jl-pink-wash` background + a pink
     border at ~40% alpha) is reserved for the genuinely primary action.
     Hover only deepens the wash (16%→26%→34% on active) — same restraint
     as `.jl-mode:hover`, never jumps to a hard fill.
   - **Danger** shares the identical wash construction but is deliberately
     kept *red*, not pink — pink is this plugin's identity color everywhere
     else, and destructive needs to read as unambiguous "stop." Danger's
     hover escalates further than primary's: a full solid fill, not just a
     deeper wash — the one place this system asks for a harder
     confirmation before committing.
   - **Secondary** stays neutral/ghost at rest, but its hover wash now
     tints pink (`--jl-pink-wash-soft`) instead of a neutral tint, so even
     a lower-emphasis button's hover state points at the one identity
     color, without secondary ever wearing it at rest.

   `.btn-success` is folded into the same treatment as `.btn-primary`: stash
   itself isn't consistent about which class a page's own Save button uses
   (scene's is `.btn-primary`, the performer page's is `.btn-success`) —
   same role, so same look now, instead of reproducing that native
   inconsistency.
2. **Padding and font-size are NOT global — confirmed unsafe, not just
   assumed.** Injecting a blanket `.btn { padding: 9px 18px }` on the
   scenes list (during v1) visibly ballooned the toolbar's icon buttons and
   the pagination row (« < 1 of N > »), which carry no distinguishing class
   beyond bare `.btn.btn-secondary` — there's no reliable selector to
   exclude them by class name alone (confirmed: performer edit's own Save
   button carries no wrapping container class either, unlike scene's
   `.edit-buttons-container`, so container-based scoping isn't even
   consistent enough to invert into an exclusion list). Font-size wasn't
   blanket-tested either way in v2, so it stays scoped alongside padding on
   the same reasoning rather than assumed safe. Both are opt-in per
   confirmed-safe container instead — currently `.modal-footer` (Bootstrap's
   own confirmation-dialog footer, catches every delete/confirm modal in
   the app for free) and `.edit-buttons-container`. Extend this list
   container-by-container as other real action rows turn up; don't try to
   flip it into a blanket rule with exclusions.

Scene-dashboard.css layers one page-specific refinement on top: "Scrape
with…" is natively `.btn-primary`, same class as Save right beside it, so
buttons.css's global rule alone would keep them equally weighted despite
Save being the only genuinely primary action on that row. Demoted to
buttons.css's own `.btn-secondary` (ghost) look, scoped to
`.edit-buttons-container .scraper-menu .btn-primary` — the one place native
markup doesn't already carry `.btn-secondary` for a secondary-weight action,
so the fix has to live in the page-specific file, not the page-agnostic one.
Its hover reuses `--jl-pink`/`--jl-pink-wash-soft` — kept in sync with
buttons.css's own `.btn-secondary:hover` by hand, since this rule has to
live in scene-dashboard.css (see above) rather than reuse the class
directly.

**Bug found post-ship: hover/active text color silently reverted to native
purple/native fills on several buttons — a partial-property-override gap,
not a specificity loss.** Reported live: performer page's Edit and Submit
to Stash-Box buttons showed a purple hover that "did not feel intentional."
Root cause via `CSS.getMatchedStylesForNode` with `CSS.forcePseudoState`
(the right tool for exactly this: a color that's correct at rest but wrong
in one interaction state needs the matched-rules list *for that state*, not
just computed style at rest) — `.btn-primary:hover`/`.btn-success:hover`
set `background-color`/`border-color` but never redeclared `color`, so on
hover the text color fell through to whatever else matches `.btn-primary`
with a `color` property — the `dracula-theme` plugin's own
`.btn-primary:hover { color: var(--purple) }`, equal specificity, still
in the cascade because nothing in this file's `:hover` rule contested that
one property. Background/border were never the issue; CSS applies winning
values **per property**, not per rule, so "the rule already covers this
button" is not the same claim as "every property this rule sets is
actually the one that wins." Same gap existed for `:active` on
`.btn-secondary`/`.btn-danger` (found by checking, not assumed once the
first instance turned up) — native's own `:not(:disabled):not(.disabled):
active` rules for those two variants were never touched by this file at
all, so pressing either would flash native's dark-slate/solid-red fill.
Fixed by explicitly setting `color` in every `:hover`/`:active` rule this
file defines, and adding `:active` rules for secondary/danger that match
native's own selector pattern exactly (same `:not():not():active` chain,
so specificity ties and cascade order — this file loading after the app
bundle — decides it deliberately, not by accident). **Any hover/active/
disabled state added to this file going forward needs every property that
row's *default* state sets restated, not just whatever changed** — a
partial override reliably leaks whatever a competing rule (native
Bootstrap, `dracula-theme`, or a future third plugin) still supplies for
the untouched properties.

**`--jl-fg`/`--jl-fg-dim` (`#f8f8f2` / `rgba(248,248,242,.72)`) — a second,
genuinely neutral text pair, deliberately separate from this plugin's own
`--jl-text`/`--jl-muted`/`--jl-dim`.** All three of those (scene-dashboard.css's
`:root`) are Catppuccin-ish and carry a real blue tint (`#cdd6f4`/`#8b9bc7`/
`#6272a4`) — fine for the sidebar they were designed for, but `.btn-secondary`
governs pagination and every other secondary control across *every*
filtered-list page (scenes, performers, galleries, tags — all of them, not
just one), so that tint read as the whole list surface trending blue, not a
sidebar accent. Reported live as "hover coloring... does not feel
intentional" — same root complaint as the purple-hover bug above, different
cause: not a missing override, a wrong-hued token used correctly. Fixed by
introducing `--jl-fg` (Dracula's actual foreground — already used natively
elsewhere in this plugin, e.g. clean-cards.css's subheader divider) instead
of nudging the existing blue tokens, so the sidebar's own use of
`--jl-text`/`--jl-muted` in Metadata/File/History/performer detail-items is
untouched — this was scoped to the filtered-list surface specifically, not a
plugin-wide palette change. `-dim` is a flat opacity step off the same hex,
not a second hue, so darkening it can never reintroduce a tint by accident.
scene-dashboard.css's Scrape-button hover (which mirrors this rule by hand,
see above) was updated to match for the same reason.

**Scene Metadata's label and performer's `.detail-item-title` were both
monospace — should be sans-serif, matching the scene File section's own
native split (its `<dt>` is native "Segoe UI", its `<dd>` is monospace).**
Metadata's version of the bug: the row sets `font-family: monospace
!important` for the *value*, and the `::before` label pseudo-element has no
font-family of its own, so it silently inherited the value's monospace
instead of getting File's sans-serif treatment — fixed by giving `::before`
its own explicit sans-serif `font-family` (no `!important` needed: a
property set directly on an element always beats an inherited value,
`!important` or not — inheritance only applies when nothing sets the
property on that element at all). Performer's version: `.detail-item-title`
had been explicitly forced to the same monospace stack as
`.detail-item-value` outright, rather than differentiating the two — fixed
by giving title `"Segoe UI", system-ui, -apple-system, sans-serif` while
value keeps the monospace stack. **The convention going forward: labels
sans-serif, data/values monospace, everywhere this "Label: value" pattern
appears — except Edit-mode forms, which stay all sans-serif** (native form
inputs, not a label:value display pattern — nothing to split).

**Same fix, one layer deeper: Metadata's label/value also had the wrong
`line-height`, at an *identical* `font-size` to File's — reported live as
"the font size... looks bigger," confirmed via computed style that size
was never the actual difference.** File's native `<dt>`/`<dd>` render at
12px/18px and 13px/19.5px respectively (both a clean 1.5 ratio — a Bootstrap
default this plugin never had to set explicitly there). Neither of
Metadata's `[data-jl-item^="scenedetails"]` rules set `line-height` at all,
so both fell back to the browser's own `normal` (~1.2), landing at 14.4px/
15.6px — visibly tighter at the *same 12px/13px font-size*, which reads as
"smaller" even though no font-size ever differed. Fixed by adding
`line-height: 1.5` to both the row (value) and the `::before` (label)
rules, landing on File's exact 18px/19.5px. Performer's `.detail-item-title`/
`-value` already matched File's line-height natively (real, separate
elements inherit ambient line-height correctly; only the ::before-label /
force-monospaced-row constructions were missing it) — no change needed
there. **Whenever matching another element's type scale, check
`line-height` alongside `font-size`/`font-family`/`font-weight` — identical
font-size with a different line-height reads as a size mismatch just as
easily as an actual size difference does, and doesn't show up unless
computed style is checked for both.**

**One layer deeper still: even the line-height fix didn't fully close the
gap — reported live again ("check the performer data - looks like it has
the same issue"), and it turned out to be neither font-size nor
line-height this time, but font *substitution*.** Both label rules
(Metadata's `::before` and performer's `.detail-item-title`) declared
`font-family: "Segoe UI", system-ui, -apple-system, sans-serif` — "Segoe
UI" was never actually installed on this server, same as File's own native
`"Segoe UI", sans-serif`, but the *rest* of the fallback stack still
matters: `getComputedStyle().fontFamily` only ever echoes back the
declared stack, never which font the browser actually substituted, so
nothing here looked wrong from CSS alone. The real answer needs CDP's
`CSS.getMatchedStylesForNode`'s sibling method, **`CSS.getPlatformFontsForNode`**
— pass it a `nodeId` and it returns what's *actually painting* that
element's text. That's how this was actually diagnosed: File's `<dt>`
resolves to "Liberation Sans"; both label rules here, with the longer
stack, resolved to "DejaVu Sans" — two different typefaces, visibly wider
letterforms in DejaVu Sans, at the *identical* declared 12px. Fixed by
trimming both rules to File's exact stack, `"Segoe UI", sans-serif` — no
extra fallback names — confirmed via the same `CSS.getPlatformFontsForNode`
call that both now resolve to "Liberation Sans" too. **When matching
another element's font and the two still look different after font-size/
line-height/font-weight all check out equal, suspect the fallback stack
itself: two declared stacks can share the same *unavailable* first name
and still resolve to different real fonts if anything after it differs.
`getComputedStyle` cannot detect this — `CSS.getPlatformFontsForNode` is
the only tool that shows the actually-rendered font, and this project's
own established fix for "looks different but I can't see why" (see the
`getMatchedStylesForNode` note elsewhere in this file) needs this sibling
method added to it specifically for font-family mismatches.**

### entity-dashboard.* — performer/studio/group/tag pages

One config-driven module for every "entity with a tabbed relations list"
detail page — performer, studio, group, tag. **Not the original
architecture**: performer-dashboard.\* shipped first, studio-dashboard.\*
was a deliberate copy-paste of it onto a second page, and both were then
retired in favor of this file once a third and fourth page (group, tag)
made the duplication itself the problem — two copies didn't justify a
generalization, four did. See "Why this generalized now" below for the
actual reasoning, not just the outcome.

**Confirmed structurally identical before merging anything, not assumed
from the class names**: all four pages share a `Tab.Container`-driven
`.nav-tabs` (`data-rb-event-key`, count badges as separate `<span
class="badge">` children, absent entirely at count 0), a `.detail-group`
whose only children are `.detail-item` divs (no scattered-group-card
workaround needed on any of them), and an `<entity>-name` span
(`.performer-name`/`.studio-name`/`.group-name`/`.tag-name`) following the
identical naming convention.

**`entity-dashboard.js`** is one config array (`ENTITIES`, one row per
page: `id`, `tabsRoot` selector, `gateAttr` name) driving a single copy of
the mode-bar builder (`buildModeBar`/`syncModeBar`/`watchNav`/
`navLinkFor`/`readTabs`/`currentKey`) — the exact same functions the old
per-page files each had verbatim, now written once. `run()` tries each
entity's `tabsRoot` in turn and stops at the first match, since only one
can ever exist on a given page (they're different routes). Each entity
gets its own gate attribute (`data-jl-perf-ready`/`-studio-ready`/
`-group-ready`/`-tag-ready`) — same failsafe property as the scene page's
`[data-jl-mode]` (constraint 6), distinct names so none of the five are
ever confused even though only one is ever set on a given page.
`watchNav()` tracks already-watched nav elements with a `WeakSet` rather
than the old single module-level "current observer" variable each per-page
file had — simpler and correct for the same reason: a SPA navigation
between two entities of the *same* type (tag A → tag B) produces a new
`nav` element, which just isn't in the set yet and gets its own observer
for free, no manual disconnect/reconnect bookkeeping needed.

**Per-entity specifics, confirmed live for each:**
- **Performer**: 6 tabs (Scenes/Galleries/Images/Groups/Appears With/
  Markers). Markers is anomalous — a real external `href` to a filtered
  scenes-markers page, not a Tab.Pane in this Tab.Container. `.click()`
  still navigates correctly since it's a genuine anchor. Has a visible
  `.alias-head`.
- **Studio**: 6 tabs (Scenes/Galleries/Images/Performers/Groups/
  Subsidiary Studios), all normal in-container panes — no Markers-style
  anomaly. No `.alias-head` rendered at all, even though GraphQL exposes
  a studio `aliases` field — confirmed live, not an oversight.
- **Group**: 3 tabs (Scenes/Performers/Sub-Groups), all normal panes. No
  `.alias-head`.
- **Tag**: 7 tabs (Scenes/Images/Galleries/Groups/Markers/Performers/
  Studios), all normal panes — tag's own "Markers" tab is a real in-
  container pane here, unlike performer's. Has a visible `.alias-head`.

**`.alias-head { color: var(--jl-muted) !important }` is one unscoped rule
covering both performer and tag for free** — deliberately not written per-
entity, since studio/group simply never render the element at all, so the
rule harmlessly matches nothing there. Confirmed live that tag's
alias-head picks up the brightening without any tag-specific code.

**`.detail-group .detail-item-title`/`-value` and the `<entity>-name`
rules are each one shared selector** (comma-separated across all four
entities' own class, for the name rule; scoped to the structurally-common
`.detail-group` rather than enumerating `.performer-head`/`.studio-head`/
etc., for the detail-item rule) — not four near-copies. `.detail-group`
is not boxed as a card on any of the four, matching the performer page's
own reverted decision ("it felt like a forced fit") — don't silently
reintroduce it on any of them.

**Why this generalized now, not from the start**: two copies
(performer, studio) were similar but each still had one genuinely
page-specific number (the pill-bar centering margin) worth keeping
visible in its own file rather than hidden in a shared one. A third and
fourth copy would have meant four files differing *only* in that one
margin plus a handful of selector prefixes — at that point the
duplication itself was the maintenance risk (a fix to the mode-bar logic
needing to land identically in four places, by hand, four times), not the
page-specific numbers. The margins are still page-specific — see below —
they just live as four rules in one file instead of four files.

**The mode bar's centering margin is measured per page, every time —
confirmed NOT portable even between structurally identical pages:**
performer 15px, studio 22px, group 14px, tag 14px (all `margin: <n>px 0
14px`). Group and tag's own numbers went through two wrong answers before
landing here — both worth knowing, since each caught a different class of
mistake:

1. **First attempt: negative top margins (-25px/-59px)**, sized to
   numerically match the 36px gap below by measuring "gap above" as the
   distance from the *Edit/Delete button row's own bottom edge* — the
   wrong reference point, though the numbers alone didn't reveal that.
   This pulled the row up far enough to fall spatially behind
   `.detail-header` (the name/aliases/edit-buttons block, a sibling above
   the tab strip, natively `position: relative; z-index: 20`) — the pill
   row was rendering *invisible*, even though
   `getBoundingClientRect()`/`getComputedStyle()` on it reported
   completely correct position, size, opacity and visibility the whole
   time; none of those catch a stacking-order problem. Only
   `document.elementFromPoint()` at the row's own coordinates did,
   resolving to `.detail-header` instead of the row. Fixed generally —
   `.jl-modes-row { position: relative; z-index: 21 }`, applied
   unconditionally to the shared class rather than capping how negative
   any one entity's margin is allowed to be — so a future entity needing
   an even larger negative margin doesn't reintroduce the same failure
   mode. **The lesson, not just this instance**: after any layout change
   that can plausibly move an element behind a positioned/z-indexed
   sibling, verify with `elementFromPoint` at the element's own center —
   not just its computed box model — before calling it confirmed.
2. **Second attempt: same negative margins, now visible post-fix, still
   wrong** — reported live as a real bug ("modal buttons... spanning the
   upper and lower sections"). The z-index fix made the row paint on top
   instead of vanishing, but it was still *positioned* overlapping
   `.detail-header`'s own background panel, which extends well past the
   button row via its own real `padding-bottom` — so the pill sat partly
   on the header's lighter panel background and partly on the plain page
   background below it, a visible seam through the middle of the pill
   bar (group) or entirely inside the header's panel (tag, whose more
   negative margin pulled it in further). The reliable reference point
   turned out to be `.detail-header`'s own bottom edge, not the button
   row's: at `margin-top: 0`, the pill row's natural position lands
   *exactly* flush with it on both pages (confirmed via
   `getBoundingClientRect()`) — because `.jl-modes-row` is the first
   child inserted into `.group-tabs`/`.tag-tabs`, which itself begins in
   normal flow immediately after `.detail-header` with no gap of its
   own. **That makes the rule simple and safe going forward: 0 is flush
   with the previous section's true end, small positive values add
   breathing room below it, negative values are categorically wrong here
   — they can only ever push back into the previous section's own box.**
   14px (matching the row's own existing bottom margin, already an
   established constant) is what both pages landed on; confirmed live
   with a dedicated overlap check (`row.top >= header.bottom`) after,
   not just a visual glance.

## Non-negotiable constraints

These are the facts the design rests on. Each was verified against upstream —
do not relax one without re-checking it.

**1. Never move a DOM node React owns. Append only.**
React holds references to the nodes it rendered. Re-parenting a `.tab-pane`, or
removing one of its children, risks `NotFoundError` on React's next update.
Adding a node React has never seen is safe — React will never try to remove it.
Every header this plugin injects is `appendChild`ed and floated to the top with
CSS `order: -1`, never prepended.

The reading-order reorder (see Layout below) leans on this constraint hard: it
uses `display: contents` on wrapper elements to un-box them — the DOM tree is
untouched, only which elements generate a box changes — so deeply-nested nodes
(inside the Details pane) can become flex items of `.scene-tabs` alongside the
page header and the mode bar, without a single `appendChild`/`insertBefore`
that would touch a React-owned node.

**2. Every pane except Edit is already mounted.**
`react-bootstrap` 1.6.6 `TabPane` wraps its child in `<Fade>` and only returns
`null` when `unmountOnExit` is set. In `Scene.tsx` only `scene-edit-panel`
passes `mountOnEnter`. So File Info, History, Groups and Galleries are in the
DOM at all times, merely hidden by Bootstrap's `.tab-content > .tab-pane
{ display: none }`. **The dashboard is a CSS reveal, not a merge.**

**3. Panes carry no readable key; nav links do.**
`TabPane` strips `eventKey` before render, and stash's `Tab.Container` has no
`id`, so `getControlledId` returns undefined and panes get no `id` either.
Panes must therefore be fingerprinted by the markup their panel component
emits — that is what `SIGNATURES` in `scene-dashboard.js` does, and it is the
most fragile part of the plugin. Nav links *do* carry `data-rb-event-key`
(set by `AbstractNavItem`), which is why the mode bar drives them directly.

**4. The cascade trap.** The reveal rule is:

```css
.scene-tabs[data-jl-mode="browse"] .tab-content > .tab-pane[data-jl-browse]:not([data-jl-pane="details"]) {
  display: flex !important;
}
```

That is five class-level selectors with `!important`. **Any rule that needs to
set `display` on a browse pane must include `[data-jl-browse]` or it silently
loses.** This bit three separate rules during the build: the details pane's
`display: grid`, the empty-card hide, and the card chrome. If a card renders
with its header at the bottom, or an empty Groups card appears, this is why.

The reveal must be `flex`, not `block`, because `.jl-card` relies on flex plus
`order: -1` to keep injected headers first.

The same trap applies to the flattening rules in Layout below: the reveal rule
above and the Details pane's `display: contents` rule must never both match
the same element, or whichever is later in the file wins by luck, not by
design. The reveal rule's selector explicitly excludes Details
(`:not([data-jl-pane="details"])`) for exactly this reason — prefer an
explicit exclusion over relying on source order when two `!important` rules
could otherwise collide.

The trap isn't limited to rules *within* this plugin, either — `clean-cards.css`
and stash's own bundled CSS both style elements this file repositions, for a
different original context (a card-thumbnail overlay badge, a small watermark
logo), and their margins leak straight through:

- `.studio-code` carries `margin: 18px auto 12px !important` from
  `clean-cards.css` (sized for an absolutely-positioned badge over a
  thumbnail). Needs `!important` to beat it.
- `.studio-logo` (the `<img>`) carries `margin-top: 1rem` from stash's own
  bundle — not `!important`, but easy to miss since it's not in either of
  this plugin's own files.
- `.tag-item` carries `margin: 5px` from stash's own bundle, which stacks
  with this plugin's own flex `gap` into doubled, uneven spacing if not
  zeroed.
- `clean-cards.css` has its *own*, older `.scene-details h6 { font-size /
  font-weight / color / margin-bottom: 2px !important }`, written for the
  original inline (pre-dashboard) layout, still loaded and still matching —
  every Metadata card row is a `.scene-details h6`. Its `margin-bottom: 2px`
  is what caused a stubborn ~2px seam between rows that the `-10px`
  margin-top (chosen to exactly cancel the container's `row-gap: 10px`) never
  touched, because it's the opposite side of the box. Confirmed via
  `CSS.getMatchedStylesForNode` over the CDP protocol, not by guessing —
  worth reaching for when `getComputedStyle` shows a value with no
  explanation in either of this plugin's own files.

Moral: when a repositioned element looks like it has mystery padding, check
`getComputedStyle` before assuming the bug is local — grep `clean-cards.css`
and the served `/assets/index-*.css` bundle for the class first.

**5. Tab switches are attribute mutations, not childList.**
Selecting a tab only toggles the `active` class. A `MutationObserver` watching
`childList` never sees it. `watchNav()` observes `.nav-tabs` with
`attributeFilter: ['class']` — without it the mode bar desyncs and stash's own
Mousetrap shortcuts (`a q e k i h`, bound in `Scene.tsx`) stop updating it.

**6. Everything is gated on `[data-jl-mode]`, which is the failsafe.**
`scene-dashboard.js` sets it only after the mode bar is successfully built. If
the script throws or upstream markup moves, no layout rule matches, the native
tab bar is not hidden, and stash renders exactly as it does today. **Preserve
this property.** Do not write dashboard CSS that applies without the gate.

## Architecture

`scene-dashboard.js` does three things and nothing else:

1. `tagPanes()` — stamps `data-jl-pane` / `data-jl-browse` on each pane, adds
   `.jl-card` to the ones that stay boxed (File, History, Groups, Galleries),
   injects their headers, marks empty Groups/Galleries with `data-jl-empty`,
   and tags the Details pane's un-classed `<h6>` headings (Created/Updated,
   "Details:", "Tags", "Performer") with `data-jl-item` so CSS can address
   them individually once flattened. Matched by leading text, same hard-coded-
   English tradeoff as `CARD_TITLES` — revisit only if the UI language changes.
   Also mirrors Framerate/Resolution from Subheader into new Metadata rows
   (`data-jl-mirror="true"`) and sizes `.jl-codedate-backdrop`, the joined
   background behind Studio Code and Subheader/Date — see the Layout
   section's Metadata/Subheader notes for both.
2. `buildModeBar()` — inserts `.jl-modes` before `.nav-tabs`. Rebuilt only when
   the available mode set changes (Queue appears only when a queue exists).
3. `syncModeBar()` — reads the active nav link, maps its `data-rb-event-key` to
   a mode, writes `data-jl-mode`.

All layout lives in CSS keyed off those attributes.

Mode buttons call `link.click()` on stash's own nav rather than touching React
state, so `Tab.Container` stays the single source of truth for the active pane.
Browse maps to the Details tab and reveals its siblings.

There used to be a fourth step, `watchWidth()` — a `ResizeObserver` writing a
narrow/wide breakpoint so a two-column grid could drop to one column on
narrow panes. It's gone: stash's own CSS pins `.scene-tabs` to exactly
`flex: 0 0 450px` above the 1200px viewport breakpoint (there is no drag-
resize; the scene-divider button only toggles `.scene-tabs.collapsed{display:
none}`), so the pane has exactly one stable width on desktop and a two-column
grid never had room to prove itself. See Layout below for what replaced it.

## Layout: reading order, not a grid

The sidebar is one reading-order column, built by making `.scene-tabs` (which
stash's own CSS already sets to `display:flex;flex-direction:column`) hold
every piece we want to order as a direct-ish flex item, then placing each with
`order`. "Direct-ish" because most of the content we need to place — Original
Title, the remaining Details metadata, the description, Tags — lives several
levels deep inside the Details tab-pane, disconnected from the page header and
the mode bar. `display: contents` on every wrapper in between (the header
div, `.scene-header-container`, `.tab-content`, the Details pane itself, its
`.row`s, `.scene-details`) un-boxes each one without moving anything, so their
children end up participating in `.scene-tabs`'s flex layout as if they were
direct children.

Two wrinkles this creates:

- **Flex items in a `flex-direction: column` container each get their own
  line — there's no equivalent of `order` for "these several items sit on the
  same line."** Tags need to wrap into a chip cloud, not stack one per line,
  so `.scene-tabs[data-jl-mode]` is actually `flex-direction: row` +
  `flex-wrap: wrap`, and every item gets `flex: 0 0 100%` to force its own
  line — a manual version of column stacking. `.tag-item` is the one
  exception (`flex: 0 0 auto`), so consecutive tag chips with the same
  `order` pack together and wrap naturally, the same as before flattening.
- **Visibility can no longer be "the whole Details pane shows only in Browse
  mode."** Original Title and the Metadata/Details/Tags cards are meant to
  stay up as persistent context in every mode, while Performers and Custom
  Fields (also inside the Details pane) are Browse-only, same as
  File/History/Groups/Galleries. So the Details pane's own `display: contents`
  is unconditional on `[data-jl-mode]` (any mode, not just `="browse"`), and
  the Browse-only pieces inside it get their own
  `:not([data-jl-mode="browse"]) { display: none !important }` rule instead
  of inheriting hidden-ness from an ancestor.

File, History, Groups and Galleries are **not** flattened — they stay whole
`.tab-pane` elements with their existing `.jl-card` chrome, just placed with
`order` like everything else. Custom Fields is the same story one level down:
it's one contiguous element inside the Details pane, so it gets `.jl-card` +
`ensureHead()` directly and is just placed at the bottom of the Browse body
with `order`. Metadata, Details and Tags are different — see below.

### Scattered-group cards (Metadata, Details, Tags)

These three are visually `.jl-card`s but structurally cannot be: each one's
content is several sibling elements sharing a React-owned parent with *other*
content that must NOT be part of the card. Original Title shares a parent
(`.scene-details`) with the Director/Created At/Updated At fields that make up
Metadata. Performers and Custom Fields share a parent (the Details pane's
content column) with the Details paragraph and the Tags chip cloud. There is
no single element to hang one `.jl-card` off, and constraint 1 rules out
moving nodes into a new wrapper to make one.

The fix: every member of the group gets the card's background and
left/right border **individually**, pulled flush against its neighbours with
`margin-top: -10px` (fully cancelling the container's `row-gap`, for a
seamless fill — see the row-gap-double-counting notes further down for why
it's the full value, not a partial claw-back). The head (see below) gets the
top border/radius; whichever member is genuinely last gets the bottom
border/radius. Since Metadata's rows (Director/Created At/Updated At) all
share one `data-jl-item="scenedetails"` value, and Tags' chips are all
`.tag-item`, neither can be targeted by attribute value alone — both get an
explicit `data-jl-last-in-group="true"` flag from scene-dashboard.js instead,
recomputed every `tagPanes()` run, rather than trusting `:last-of-type` (a
future stash markup change adding another bare sibling of the same tag name
would silently pick the wrong element).

**The head is a new function, `ensureGroupHead()`, not `ensureHead()`.**
`ensureHead()` appends into one host and toggles a class on that same host to
collapse it — there is no "host" here. `ensureGroupHead()` appends the head
into whichever real parent the group's members live in (`.scene-details` for
Metadata, the Details pane's content column for Details/Tags) — safe, because
that parent already accepts our other appended children, and DOM position
doesn't matter once `order` places the head at the front of its group. The
collapsed flag lives on `.scene-tabs` itself as `data-jl-collapsed-<groupId>`
(`applyGroupCollapsed()`), and CSS hides each member by its own existing
selector — there's no shared child-combinator rule to write, because there's
no shared parent to scope it to.

That "hide each member individually" list has to include anything the head
itself grew for the expanded state, not just the body — the inset-divider
`::after` (above) is on the *head*, not a body member, so it's easy to
forget when writing the `[data-jl-collapsed-*="true"]` rules and end up with
a stray divider line under the label with nothing left to divide.

**The generic `.jl-head { order: -1 }` rule does not apply to these heads.**
That rule means "float first within *my own* small internal flex-column,"
which only makes sense for a head nested inside a real `.jl-card`. These
heads are themselves top-level `.scene-tabs` flex items now, so `order: -1`
would float them above literally everything on the page, Title included. Each
one gets an explicit `order` instead (`.jl-head[data-jl-card="metadata"]`
etc.), which — being a more specific selector — overrides the generic rule.

**Subheader shows only Date now; Framerate/Resolution are mirrored into
Metadata instead of shown in the header.** This has flip-flopped twice:
v1 hid `.scene-subheader` outright and mirrored all three (Release Date/
Framerate/Resolution) into Metadata; that was reverted in favor of
showing native, unmirrored Subheader with all three visible in the
header, Metadata holding only genuinely native fields. Now reverted again,
partially: Framerate/Resolution are back in Metadata (Release Date is
not — it stays in the header, paired with Studio Code, see below), because
Studio Code moved onto Subheader's line and there wasn't room/reason to
keep three technical values crowded onto that line too. If this direction
changes again, the mechanism is the same each time and needs the same two
things to avoid the landmine below: mirrored nodes need their own
tagging-loop skip, and a way to tell mirrors apart from natives for the
"who's genuinely last" bottom-border logic.

**The tagging-loop-skip landmine that bit v1 is avoided this time with an
explicit `data-jl-mirror="true"` flag, checked with a `continue` at the top
of the native-fields loop.** v1's version of this loop tagged every
non-Original-Title `<h6>` as a native field unconditionally; on the second
`tagPanes()` run, it walked straight into its own three previously-injected
mirror `<h6>`s (still children of `.scene-details` from the first run) and
reprocessed them as if native, clobbering their distinct `data-jl-item`
values back to the generic one and silently re-sorting them. This version's
mirrors carry `data-jl-mirror="true"` (plus `data-jl-mirror-key` to look
each one up by key on the next run) and the native loop's very first check
is `if (h6.dataset.jlMirror === 'true') continue;` — skip, don't process.
Mirrors are handled entirely separately, in their own block right after the
native loop, which both creates them (once — reuses the existing node by
key on later runs, via `metaCol.querySelector('h6[data-jl-mirror-key="..."]')`,
never appends a second copy) and refreshes their `textContent` from
Subheader's `.frame-rate`/`.resolution` spans on every run — checked, not
unconditional: `if (h6.textContent !== value) h6.textContent = value;`.
Both native and mirror h6s get pushed into the same `scenedetails` array
before the "who's last" `data-jl-last-in-group` pass runs, so a mirror can
end up as the true last row without any special-casing there.

**That equality check on the mirror's `textContent` write isn't there to
avoid misparsing (there's no native "Label: value" text here to strip,
unlike `splitLabelValue()`) — it's load-bearing against a genuine
permanent 60fps busy-loop, found during a performance review, not by
design.** `.textContent =` always removes and reinserts a child text node
per spec, regardless of whether the new value equals the old one — a
childList mutation. `tagPanes()` runs from inside the body-wide
`MutationObserver` at the bottom of this file (`childList: true, subtree:
true` on `document.body`), so an *unconditional* write here mutates
exactly the kind of thing that observer watches for, on every single run —
including runs the write itself triggered. The result: write → self-
observed mutation → queued `requestAnimationFrame` → another `tagPanes()`
run → write again (same value, still a mutation) → observed again →
forever. Confirmed via CDP on a live scene page: 180 childList mutations
on this exact node over 3 idle seconds (60/sec, matching `requestAnimationFrame`)
with the unconditional write; 0 with the guard. See the Testing section's
note on checking for this class of bug on any future DOM write reachable
from inside that observer's call chain.

**Metadata's row order (Resolution, Framerate, Director, Created, Updated)
is set entirely by per-`data-jl-label` `order` values in scene-dashboard.css,
not by DOM/push order.** All five rows share one `data-jl-item="scenedetails"`
value, so — same principle as everywhere else in this file — visual
position comes from `order`, keyed here off `data-jl-label` instead (the
one thing that actually distinguishes the five). This is the only option:
Director/Created/Updated are native and can't be reordered relative to
each other by moving them (constraint 1), and the two mirrors are always
`appendChild`ed — always last in DOM order — regardless of when
`tagPanes()` creates them, so DOM position could never put them first
either way. The `data-jl-last-in-group` pass (above) was originally
array-order-based (mark whichever `scenedetails[i]` came last in the
array), which broke the moment display order stopped matching push order —
it now walks a `METADATA_ROW_ORDER` constant matching these same `order`
values to find the genuinely-last-displayed row instead. Keep both in sync
if this order ever changes again.

**Studio Code moved off its own line onto Subheader's, mirroring the
scene-card grid's `.stash-code-date` bar — code on the left, date on the
right, one joined pill behind both.** They're two separate native React
elements with no shared parent (Code lives inside `.scene-header-container`,
Subheader is a sibling of that container one level up — both flatten into
the same `.scene-tabs` flex context via `display: contents`, but there's
still no single element to paint one background on), so this pairs them
via flex placement — both `flex: 0 0 auto` with consecutive order values so
they land on the same flex-wrap line, Code's `margin-right: auto` pushing
Date to the line's right edge (the same auto-margin trick this used to use
to push Code right when it shared Studio Logo's line, just aimed the other
direction now that Code leads) — and joins their backgrounds with
`.jl-codedate-backdrop`, built by `sizeCodeDateBackdrop()` using the exact
same appended-backdrop technique `.jl-tags-backdrop` uses for the same
underlying problem: a height reservation plus equal-and-opposite negative
`margin-bottom` (net zero flow contribution), with Code and Subheader's own
`margin-top: -10px` (a plain static rule this time, not measurement-gated
like Tags' row detection — there's always exactly one line here) pulling
them up to sit on top of it. Code and Date each also still carry their own
individual pill background (Code natively from clean-cards.css, Date from
a new rule here); since every layer paints the identical
`rgba(30,30,50,.95)`, the redundancy is invisible and the backdrop's only
real job is filling the gap between them that neither element's own
background reaches.

**The joined Code/Date bar's position has moved three times now — currently
between Studio Logo (order 5) and Title (order 20).** It started sharing
Studio Logo's own line; then moved to its own line between Original Title
(30) and Toolbar (50); now sits right after Studio Logo instead, before
Title. Only the three order values (backdrop, Code, Date) change between
these — nothing about how the group is built, sized, or joined moves with
it, so relocating it again in the future is just picking new order numbers
in the gaps between neighbours.

**Every full-width row's right edge is inset 16px via one rule —
`.scene-tabs[data-jl-mode] { padding-right: 16px }` — not a per-element
calc/margin repeated on each card.** This started as a narrower ask (make
the joined Code/Date bar sit inset under Title, not spanning the full row —
first centered with 8px margin each side, then left-justified with all
16px on the right via the backdrop's own `flex: 0 0 calc(100% - 16px);
margin: 0 16px 0 0`) and was later generalized: bringing every OTHER
block's right edge to match Code/Date's, to give `.scene-tabs`'s own
internal scrollbar (which already eats 10px with zero padding of its own
natively) real clearance everywhere, not just on one element. Doing the
inset once at the container level means every `flex: 0 0 100%` child
(Title, Toolbar, every card, the mode bar) — and even things that aren't
`flex: 0 0 100%`, like tag chip wrapping, which is governed by the
container's available line width rather than its own basis — automatically
lands on the same right edge for free, no per-element rule needed. The
`.jl-codedate-backdrop`/Code/Subheader trio that originally carried this
inset themselves went back to the plain shared pattern once the container
took over: backdrop is `flex: 0 0 100%; margin: 0` again, Subheader lost
its `margin-right: 16px` (Code's own `margin-right: auto` already pushes
Date to the line's edge, which now matches everyone else's automatically).

**Toolbar tried the mode-bar's dark-pill treatment (background/border/
radius) and reverted — it now sits flush on the page background again,
same as Title/Original Title, with only `order` set.** With Toolbar and
the mode switcher both wearing identical chrome and sitting 10px apart,
the two read as one stacked block instead of two separate controls. Settled
by comparing three fixes side by side (screenshots, not just discussion):
more gap between them, merging both into one bordered bar with a divider,
and stripping the pill off Toolbar entirely so only the mode switcher — the
one that's actually navigation — keeps a border. The last one won: it's the
only one of the three that keeps the two controls visually independent
instead of just spacing apart two duplicates or fusing them into one unit.
If Toolbar's own pill treatment is ever revisited, that comparison (and why
"more gap" and "merge" both lost to "flat") is the starting point, not a
blank slate.

**The Code/Date bar's height has moved five times now (2px→7px→0→5px→3px
vertical padding on Code/Date, i.e. 26px→36px→22px→32px→28px bar
height) —
via padding on Code/Date, not a height set on the backdrop directly.**
The backdrop's height is *measured*, not assigned (`sizeCodeDateBackdrop()`
sets it to `Math.max(code.height, subheader.height)` every run — see that
function's own comment for why), so changing the bar's height means
changing what it measures: `.studio-code`'s and Subheader's own vertical
padding. History, each step requested live in sequence the same day
(2026-09-01):
1. Native `2px`/`2px` → `7px`/`7px`, landing the backdrop at 36.4px to
   match `.jl-modes`' 36px.
2. `7px`/`7px` → `0`/`0`, to match the scene-card grid's own Code/Date
   bar instead — measured live, not assumed, that the grid bar has
   genuinely *zero* vertical padding (`.stash-code-date { padding: 0
   9px }`) and its box height exactly equals its own text's rendered
   height (both 22.39px, confirmed via `getBoundingClientRect()` on the
   text itself, not just read off the CSS).
3. `0`/`0` → `5px`/`5px` (32px bar) — reported live as too tight once
   actually seen at zero padding. Not measured against another
   reference this time, a plain visual call; 10px total was offered as
   a range ("8 or 10px") and 5px/5px picked as the midpoint-ish, round
   value.
4. `5px`/`5px` → `3px`/`3px` (28px bar) — reported live as too much
   immediately after step 3 shipped. "Reduce by 4px" translated
   directly to −2px each side (4px off the 10px total), not −4px each
   — the request was about total bar height, not either padding value
   individually.

Four course-corrections on one property in one sitting is worth noting
plainly: this number has no principled derivation, it's been tuned by
eye against live feedback each time. Don't treat 28px (or any of the
earlier values) as settled just because it's currently what's shipped —
check with the user before "fixing" it back to any previous number if
it comes up again.

Horizontal padding was untouched by any of these four steps (Code's
`4px`/`10px`, Subheader's `10px`/`4px` — see the left/right-edge fix
documented separately above) — only top/bottom ever needed to change to
affect height, and both elements need the *same* value to stay level
with each other, since the backdrop tracks whichever one is currently
taller. If this needs revisiting again, the underlying lever is always
the same: Code's and Subheader's own `padding-top`/`padding-bottom` in
scene-dashboard.css, changed together.

**Metadata's "Label: value" lines are two-toned like File/History's
`<dt>`/`<dd>` via `splitLabelValue()`, not a wrapped `<span>`.** A real label
element would mean restructuring the text node — unsafe for Director, whose
value React can rewrite after an Edit-mode save (see constraint 1). Instead
the label portion is removed from the text (same nodeValue-in-place move as
`stripLeadingLabel()`) and stashed in `data-jl-label`, which CSS reads back
via `::before { content: attr(data-jl-label) ": " }` — the label is rendered,
never stored as a live text node, so there's nothing for a future React
update to conflict with.

The removal itself isn't a single-node slice: **React renders `{label}:
{' '}{value}` as separate sibling text nodes**, not one merged string — for
"Created At: December 10, 2022 9:39 PM" that's five nodes ("Created At",
":", " ", the date, a trailing " "). Checking only `h6.firstChild` (the
first version of this code) finds no colon in "Created At" alone and
silently no-ops, leaving the raw unstripped text on screen.
`splitLabelValue()` does a read-only scan across the leading text nodes to
find which one actually contains the colon before mutating anything, and
bails untouched if it hits a non-text child first (Director's value is a
real `<a>`, reached only if a colon never turns up in the text before it).

It has to be idempotent because Metadata's native fields (Created/Updated
At, Director) are never reset — a second `splitLabelValue()` call after the
first already-successful split would rescan the *value*, and a value like
"December 10, 2022 9:39 PM" contains its own colon, which would get
misread as label text. The call site guards this with
`if (!h6.dataset.jlLabel) splitLabelValue(h6)`.

**Created At / Updated At are further reformatted from stash's native
"December 10, 2022 9:39 PM" down to "2022-12-10" by `formatDateValue()`,
called right after `splitLabelValue()` and gated on `h6.dataset.jlLabel`
being one of those two labels (Director's value isn't a date and is left
alone).** Same in-place-`.nodeValue`-mutation technique as everywhere else
in this file, for the same constraint-1 reason — but unlike
`splitLabelValue()`, it needs no explicit idempotency flag: the regex it
matches against only recognizes stash's native "Month D, YYYY ..." shape,
so a second call after a successful reformat (now "2022-12-10", no leading
month name) simply fails to match and no-ops on its own. That self-guarding
is what makes it safe to call unconditionally on every `tagPanes()` run
(unlike the guarded `splitLabelValue()` call right before it) — if React
later overwrites the same node with a new native-format timestamp (e.g.
Updated At after an edit), the regex matches again next run and it
reformats again.

**The labels themselves are shortened from "Created At"/"Updated At" to
"Created"/"Updated"** — right where `splitLabelValue()` first captures
them, inside the same `if (!h6.dataset.jlLabel)` guard, so the rename
itself only ever runs once per h6. "At" reads as noise once the value next
to it is a bare date with no time-of-day left to justify it. The
`formatDateValue()` gate above matches the *shortened* labels
("Created"/"Updated"), not the native ones — correct because this branch
runs before the very first `tagPanes()` call finishes, so every later
call already sees the shortened form; gating on the native "... At" text
would silently stop matching (and stop reformatting new Updated At values)
the moment the rename landed.

**A gap between two rows in a scattered-group card must be closed with
padding on the row, never a margin between rows.** `--jl-well` is
`rgba(30, 30, 50, 0.42)` — translucent, because in a real `.jl-card` it's
painted once by the shared parent. A scattered-group card has no shared
parent (that's the whole reason it exists), so each row paints its own
`--jl-well` independently; any real gap between two rows' boxes exposes the
plain page background through that gap, one shade off from the translucent
fill — which reads as alternating bands, not intentional whitespace, once
there are more than two or three rows. `margin-top: -10px` (fully negating
the container's `row-gap: 10px`) keeps the boxes flush with zero exposed
gap; the File-matching breathing room between rows comes from
`padding-top` on each row instead, which stays inside the filled box.

**`row-gap` already runs between every pair of adjacent flex items —
don't also give one of them a matching margin "to add the gap back."** A
card head needs the *default* `row-gap: 10px` before it (same as any other
top-level item), not zero — so unlike its card's members (which cancel
`row-gap` with `margin-top: -10px` for a seamless fill) it should carry no
compensating margin at all, and `margin-top: 0` is correct. An earlier
version set it to `10px` instead, on the theory that the gap needed adding
back explicitly; `row-gap` doesn't stop applying just because a neighboring
element also has its own margin, so this doubled to 20px before every card,
and — worse — an equivalent `margin-bottom` in the collapsed-state rule
stacked a *third* 10px, for a 30px gap between two collapsed cards where
File/History (no extra margin either side) had 10px. If a gap looks
right in isolation but wrong stacked with its neighbors, check for exactly
this: two sides both independently "restoring" the same `gap`.

Two more instances of this same family of bugs, found afterward:

- **`.jl-modes-row`'s own `margin-top: 14px`** was adding to `row-gap: 10px`
  for a 24px gap after Toolbar — fixed to `margin: 0 0 6px` (no top margin,
  `row-gap` alone provides the standard gap; the 6px bottom margin is real
  intentional extra room before Metadata, not a duplicate of anything).
- **`.jl-tags-backdrop` had no `margin-top` at all**, so the plain
  `row-gap: 10px` between the Tags head and the backdrop rendered as a
  visible strip of page background — a *literal* gap in the fill, not just
  excess whitespace, since (unlike Metadata's members) the backdrop's whole
  purpose is to be a seamless single-color fill. Needs the same
  `margin-top: -10px` every other card-body member uses to cancel it.

Two more, found by systematically measuring every gap in the sidebar rather
than by eye — the gap after Studio Logo and after Original Title both read
as "a little bigger" without being obviously wrong, exactly the kind of
small stacked-margin drift this bug family produces:

- **`h1.studio-logo` carries a native `margin-bottom: 7px`** (an h1
  default, never zeroed here) **that stacked with `row-gap: 10px` for 17px
  after the logo**, vs. the 10px baseline every other adjacent pair in the
  column uses. Fixed with a plain `margin-bottom: 0` alongside the existing
  `margin-top: 4px` override.
- **`.stash-original-title` inherits clean-cards.css's `.scene-details h6
  { margin-bottom: 2px !important }`** — the same "stubborn 2px seam" rule
  documented above for Metadata's rows, hitting a different element this
  time — **stacking with `row-gap: 10px` for 12px after Original Title**.
  Fixed the same way: `margin-bottom: 0 !important` (needs `!important` to
  beat that rule's own).

**A collapsed scattered-group head was missing its bottom edge entirely.**
The expanded head sets `border-bottom: none` (the inset divider is drawn by
`::after` instead, see above) — collapsing hides that `::after` but never
turns the real border back on, so a collapsed card was outlined on
top/left/right and open on the bottom. Every collapsed-state rule for these
heads needs its own explicit `border-bottom: 1px solid var(--jl-line)`.

**Tag chip padding needed margin on the chips, not `column-gap` on the
container, and `column-gap` had to go to zero to avoid double-counting
(same family of bug as above, just horizontal).** `.tag-item` carries
`margin: 0 8px` as its base, for inter-chip spacing (two adjacent chips'
margins combine to 16px between them); stacking that with the container's
`column-gap: 6px` would have added both together. The container's
`column-gap` is safe to remove entirely: nothing else visibly depends on it
(everything but the Studio Logo/Code pair and tag chips is `flex: 0 0
100%`, and the Logo/Code pair's spacing is actually governed by Studio
Code's `margin-left: auto`, not `column-gap`).

**Each row's own leftmost/rightmost chip gets its outward-facing margin
bumped from 8px to 14px, to align with the 14px inset every other card's
content uses — a plain shared `.tag-item` margin can't do this alone.**
The first version of this card left every chip at a uniform 8px edge inset,
6px short of Metadata/Details/File's convention, because flexbox has no
"first/last chip of this wrapped line" selector to single out just the
edge chips. `markTagRows()` in scene-dashboard.js (the same measurement
pass that flags row 1 for the row-gap-cancelling margin, see below) now
also flags each row's first and last chip by DOM-order position — a
row-boundary chip is one whose measured `top` differs from its previous or
next sibling's — with `data-jl-tag-row-start`/`-end`, and CSS keys
`margin-left`/`margin-right: 14px` off those instead of applying it
unconditionally. Interior chips keep the plain 8px margin on both sides
(still 16px between two ordinary chips, unchanged), so the chip cloud's
density is untouched — only the chips that actually sit at a row's edge
move out to meet the wider inset.

**Metadata's rows are tab-aligned like File's own `<dt>`/`<dd>` grid, via a
fixed column width, not a shared grid.** File's `<dl>` is `display: grid`
with dt/dd as direct grid-item siblings, so every dt shares one auto-sized
column across the whole list for free. Metadata's rows aren't siblings
inside a shared container (same reason they need `ensureGroupHead()` at
all), so that's not available — instead each row is independently
`display: grid; grid-template-columns: 72px 1fr` with `::before` pinned to
column 1. 72px is a constant, canvas-measured against the row's actual
rendered font (12px/800-weight monospace) rather than eyeballed: it
comfortably fits "Director:", the longest label in the current set at
~65px. Was 112px (and before that 80px, sized for "Release Date:") back
when Release Date/Framerate/Resolution were still mirrored into this card
— see the reverted-mirroring note above; with those gone, the wider column
just left a gap between every label and its value. **If a longer label is
ever added to the Metadata card, measure it and bump this number** — there's
no auto-sizing safety net here the way there is in File's dl.

**Metadata/Details' head divider is inset like File's, via a `::after`
pseudo-element, not a plain `border-bottom`.** File's jl-head sits *inside*
its card's own padding, so its border-bottom is narrower than the card's
outer border around it — a real, visible detail, not an accident. This
jl-head has no such wrapper (it shares the card's left/right border
directly, being flattened same as everything else here), so a plain
`border-bottom` on it would span the same full width as the card's own
sides, not the inset look. Fixed by removing that border and adding
`::after { flex-basis: 100%; border-bottom: ... }` instead — as a flex
child inside the head's own padded content box (not the head's border box),
it naturally lands at the same inset as File's.

Two more techniques worth knowing before touching this area:

- **Centering a shrink-to-fit item without stretching its background: use two
  nodes, not `margin: auto`.** The mode bar (`.jl-modes-row` > `.jl-modes`) is
  built by our own JS, so unlike the panes it's free to restructure. The
  natural instinct — one pill element, `flex: 0 0 auto` and `margin: 0 auto`
  — measurably does *not* center reliably in a multi-line wrapped flex
  container (verified: the auto margins split a smaller "free space" than the
  actual line width, for reasons not fully run down). The reliable fix is an
  outer full-width `.jl-modes-row` (`display:flex; justify-content:center`,
  `flex: 0 0 100%` like its siblings) wrapping an inner `.jl-modes` that keeps
  `inline-flex` and hugs its buttons. Same split applies to `.studio-code`:
  it doesn't need a wrapper because it only needs to hug *and* right-align,
  which plain `margin: 0 0 0 auto` handles fine — the wrapper is specifically
  for true centering.
- **Stripping a label prefix from stash's own text is safe if you mutate the
  Text node's `nodeValue` in place, not `el.textContent =`.** `Original Title:
  `'s prefix is removed this way (`stripLeadingLabel()`). `textContent =`
  destroys the existing Text node and creates a new one; React holds a
  reference to the original and calls `.nodeValue = ...` on it when the value
  changes, so replacing the node would silently break future updates (no
  crash, just staleness). Mutating `.nodeValue` directly is the same
  operation React itself performs, so its reference stays valid.

## Testing

**This checkout is the live stash instance's plugin directory** (symlinked
in place, not a separate dev copy) — edits here take effect on the real
running stash immediately, not in a sandbox. Reflect that in how carefully
you edit, but it also means the fastest verification loop is the real UI:
reload the scene page (or headlessly render it, e.g. via a Chrome DevTools
Protocol driver — see chat history for a working no-npm-deps CDP screenshot
script if that's not set up) and look at it directly.

`dev/fixture.html` + `dev/check.js` (30 layout/behaviour invariants — paste
into the console with the fixture open after `python3 -m http.server 8732`)
were written for the two-column grid dashboard and **are now stale**: pane
order, the flattening rules, and most of what they assert no longer match
`scene-dashboard.css`/`.js`. They still document the mechanics worth testing
(pane fingerprinting, mode-bar sync, tab-switch attribute mutations) but the
assertions themselves need a rewrite for the reading-order layout before
trusting a "30/30 passed" from them again.

`node --check` is not useful on these files — they are browser code using
optional chaining.

**Check for self-triggering mutation loops whenever a function called from
inside a body-wide `MutationObserver` callback writes to the DOM.** Both
`scene-dashboard.js` and `clean-cards.js` run a `childList: true, subtree:
true` observer on `document.body` to catch React's re-renders — which means
any DOM write that function makes (adding/removing/replacing a node) is
itself a childList mutation the same observer will see, re-triggering the
whole pass. A write that isn't equality-guarded (writes unconditionally,
even when the new value equals the old one) creates a permanent loop: write
→ self-observed mutation → re-run → write again → forever, throttled only
by `requestAnimationFrame` (~60/sec) — see the `formatDateValue`/Framerate-
mirror landmine below for a real instance of exactly this, found by
symptom ("performance review") rather than by design review. To check
whether a change introduces this: load the affected page, open a CDP
session (or the real DevTools console), and watch for silence:

```js
window.__mutCount = 0;
new MutationObserver(m => window.__mutCount += m.length)
  .observe(document.body, { childList: true, subtree: true, attributes: true, characterData: true });
// wait ~3-5 idle seconds, doing nothing
window.__mutCount   // should be 0 once the page has settled
```

Any nonzero count on an idle page (no user interaction, nothing actually
changing) means something is writing to the DOM without checking whether
the write is actually needed — track it down via the same equality-guard
lens (`if (current !== next) write(next)`) applied everywhere else in
these files, and fix the same way.

**The idle check is necessary but not sufficient — also check the page
while it is legitimately busy.** The 2026-09-02 playback finding (see
Settled decisions) passed the idle check with 0 mutations every time,
because the cost only appeared once *something else* (the video player)
was mutating the DOM and waking this plugin's observers. The probe that
caught it, worth repeating after any change to an observer callback or a
measuring function:

```js
// on a scene page with many tags, via CDP or the console
const v = document.querySelector('video'); v.muted = true; await v.play();
// compare Performance.getMetrics() LayoutCount/ScriptDuration deltas over
// ~6 s with jav-layout's JS loaded vs. blocked (Network.setBlockedURLs
// '*/plugin/jav-layout/javascript*'); with the plugin they should be
// within noise of each other, and Element.prototype.getBoundingClientRect
// (wrapped to count calls from /plugin/jav-layout/ frames) should stay at 0.
```

A zero-dependency Node CDP harness that does exactly this (plus the
per-page A/B matrix and per-plugin observer attribution) was written for
that session; it lived in the session scratchpad, not this repo — rebuild
from the description above if needed (Node 20 has no WebSocket, and no
`ws` is installed, so it needs a ~100-line hand-rolled RFC 6455 client).

## Dev loop on the server

Symlink rather than copy, so edits land without a re-copy:

```bash
ln -s /path/to/jav-layout /path/to/stash/config/plugins/jav-layout
```

- Editing CSS or JS: refresh the browser.
- Adding, renaming or removing a file: **Settings → Plugins → Reload Plugins**,
  then refresh.
- `Settings → Interface → Custom JavaScript` must be disabled (the checkbox,
  not just an empty textbox), or everything in `clean-cards.js` runs twice.
  Confirmed off via `config.yml`'s `javascriptenabled: false` as of this
  writing — if dashboard behaviour ever looks doubled, check this first.

## Conventions

- Prefix everything this plugin introduces with `jl-` / `data-jl-*`. There are
  currently zero collisions with `clean-cards.*`; keep it that way.
- Build DOM with `createElement`, not `innerHTML` — matches `clean-cards.js`.
- Comments explain *why*, especially where a line encodes one of the
  constraints above. The specificity and observer traps are invisible in the
  code otherwise.
- Card titles in `CARD_TITLES` are hard-coded English rather than read from
  stash's locale bundle. Deliberate; revisit only if the UI language changes.

## Settled decisions — do not silently redo

- **Chips are colored by role — Option B, chosen 2026-09-04 from three
  live-rendered options (`chips.css`).** Tag chips and the performer
  popover's name chip take `--jl-link` (cyan), marker chips take
  `--jl-accent` (pink); fill 14 %, border 34 %, hover 22 %/50 %, the
  button wash recipe, all `color-mix()` off the tokens so every theme
  follows. The alternatives were A (lilac tags / orange markers, my
  recommendation, on the grounds that orange was an unused hue) and C
  (green tags / orange markers); B was picked for one "leads out of this
  scene" color across tags and performers, with markers joining the
  code/date accent family. Inventory, measured live before proposing:
  every chip in stash is the same `.tag-item.badge.badge-secondary` span
  and they render in the scene sidebar's Tags card, the scene card's tag
  popover, its marker popover ("title - m:ss") and its performer popover;
  the Markers mode panel has no chips, only `.btn-link` rows. The card
  popovers are portaled to `<body>` and open on hover (`HoverPopover`),
  so a marker chip can only be recognized by its own link
  (`a[href*="?t="]`) — nothing about the trigger is available to scope
  on. The 1px border grows each chip by 2px; `markTagRows()`'s fixed-
  point pass absorbs that (5 row-start flags on scene 34396, unchanged).
  Verified live under Dracula, Kanagawa Wave and Synthwave '84 via
  `JLTheme.preview`, 0 idle mutations, no console errors. Not touched:
  `.wall-tag` on the markers wall (`rgb(68,68,68)` on a gradient, close
  to invisible natively) — a separate ask if wanted.
- **Copy buttons work over plain http (2026-09-04).** `navigator.clipboard`
  exists only in a secure context (https, or localhost/127.0.0.1); reached
  over a LAN IP on http it is undefined, and the scene-card studio-code
  copy button in clean-cards.js called `writeText` unguarded, so the click
  threw and did nothing ("only works with https hosts"). Now `copyText()`
  in clean-cards.js tries the async API in a secure context and otherwise
  falls back to a readonly hidden textarea + `document.execCommand('copy')`
  — with `setSelectionRange` (iOS Safari ignores `select()`), the
  `execCommand` return value checked (it returns false rather than
  throwing when refused, so the button shows a red `.copy-failed` state
  instead of a false green check), and focus handed back afterwards. The
  copy-buttons plugin (performer name/disambiguation, sidebar studio code)
  already had an execCommand fallback and got the same hardening in
  v1.0.1; the helper is deliberately duplicated, not shared, because the
  two plugins are independent and copy-buttons may not be installed.
  Verified under CDP with `navigator.clipboard` deleted and
  `isSecureContext` forced false: a real mouse click on each of the three
  buttons ran `execCommand('copy')` with exactly the right text selected
  ("ABP-914", "Rio Hamasaki"), returned true, flashed `.copied`, removed
  the textarea and did not navigate the card link. Incidental finding, NOT
  fixed: the installed fontawesome-js plugin replaces every `<i>` with an
  `<svg>`, so the icon swap to `fa-check` on copy (both plugins set
  `className` on the now-detached `<i>`) has no visible effect — only the
  color change shows. Fix, if wanted: key the icon on the button's class
  in CSS, or set the class on `btn.firstElementChild` at click time.
- **The watched indicator is a "✔ Watched" text badge on the scene
  sidebar's collection-pill line, right-justified; the check icon over
  the card thumbnail is gone (2026-09-04).** The icon was
  `.watched-badge` in `.video-section`, created by clean-cards.js and
  positioned by the user's own Custom CSS (those Custom CSS rules are
  now dead and can be deleted). Replaced on request with the explicit
  badge the scheme board mocked up: `syncWatchedBadge()` in
  scene-dashboard.js appends `.jl-watched-badge` to
  `.jl-scene-badges-row` (pill left, badge pushed right by
  `margin-left: auto`, so it stays right-justified with no pill too);
  `.jl-scene-badges` became `flex: 0 0 100%` so that row spans the
  sidebar — as `0 0 auto` it was 165px wide and "right" meant 165px in.
  Watched-ness has ONE definition, `isSceneWatched()` in clean-cards.js
  (play_count > 0, resume_time 0, play_duration ≥ 10s), exposed with a
  fresh single-scene fetch as `window.JLSceneData` — not routed through
  the card grid's session-long sceneCache, because the scene you just
  finished is exactly where a stale flag shows. **The fetch is aliased
  (`{ s: findScene(...) }`) on purpose**: the installed
  stashUserscriptLibrary plugin hooks every GraphQL response and walks
  `data.findScene.performers`, so an un-aliased findScene that doesn't
  select performers threw inside their code on every scene page (seen
  live). Verified on a watched scene (badge, right edge flush with the
  title's) and two unwatched ones (no element), zero thumbnail badges on
  the grid, no console errors. **The card's own indicator** (chosen the
  same day from three options: a second status pill beside the
  collection pill in the footer, a check glyph in the code/date bar, or
  a right-floated badge on the performers line) is the glyph:
  `.stash-watched-check` in the code/date bar — chosen because the bar has
  no spare width on a narrow card and, unlike the popover row, nothing in
  it is ever hidden by the overflow-priority logic, so it is always
  visible. It was first placed before the date (inside a `.date-group`
  wrapper that still exists) and then moved next to the studio code, after
  its copy button, the same day — "I prefer it next to the code"; the date
  stands alone on the right. `WATCHED_CHECK_HOST` in clean-cards.js
  (`'code'` | `'date'`) is the one switch between the two, and the CSS
  for both hosts is kept. Created once per watched card (guarded by
  `data-stash-watched`), never for unwatched ones. The sidebar badge
  carries `margin-right: 4px` (the subheader's own padding-right) so its
  right edge aligns with the date's TEXT edge, not the bar's box —
  measured 424 vs 420 before the fix.
  **Both checks (card bar and sidebar badge) are one stroked inline SVG
  (`makeWatchedCheckIcon()` in clean-cards.js, shared as
  `window.JLSceneData.checkIcon`), not the ✔ text glyph** — the glyph's
  weight was whatever the fallback symbol font drew, and at 0.9em it was
  reported "easy to miss" (2026-09-04). The SVG's 3-unit stroke on a
  16-unit box is a weight this plugin controls; the card's copy is sized
  1.25em (deliberately taller than the digits beside it), the sidebar's
  14px. Bump `stroke-width` or those sizes if it ever needs to change
  again, not the font.
- **Studio, group and tag cards' name and details are styled to match
  scene and performer cards (clean-cards.css, 2026-09-04; groups and
  tags added the same day on request — identical `h5.card-section-title
  > .TruncatedText` structure; groups have a `.group-card__details`
  block, tags have no details in the grid).** The name takes the
  performer card's exact title recipe (Quicksand 600, two-line clamp,
  1.25 line-height) plus `--jl-heading`, the color the scene card title
  resolves to; the "Part of … / Parent of N Studios" block is muted
  13px with its links in `--jl-link`, the same cyan as scene-card
  performer names. One deliberate asymmetry surfaced while matching:
  performer CARD titles render in the body text color, not
  `--jl-heading` (scene card titles do) — no rule of this plugin's
  sets a color on them. Studio took the scene card's heading color, the
  stronger of the two references; if performer cards should join it,
  that's one `color` line on `.performer-card .card-section-title
  .TruncatedText`, not a new decision. Verified by injecting the
  working-tree plugin (jav-layout was disabled in stash at the time).
- **Popover-row reordering and overflow clipping (clean-cards.js, and
  collection-colors' own copy) are scoped to `.scene-card` bars,
  positively, not by excluding known other card types (2026-09-04).**
  Reported live: studio cards' native popover buttons were being
  suppressed. Both plugins registered every `.card-popovers.btn-group`
  on the page and only bailed for `.performer-card`, so studio (and tag,
  gallery, image, group) bars got the scene-card treatment: a priority
  reorder keyed on class names that only exist on scene cards, and
  `anchorButtonsRight()`'s overflow clipping, tuned for a scene card's
  wide icon row, which on a studio card's short row measured wrong and
  hid everything. Now `reorderPopoverBar()` returns (after
  `clearAnchorState()`, so a bar touched by an earlier version is put
  back to native) unless the bar is inside `.scene-card`, and the boot/
  initial observers only register scene-card bars at all. The lesson:
  when a treatment is really specific to one card type, select that
  type — an exclusion list only covers the cards someone happened to
  look at. Verified on studios, performers, tags and galleries (native
  rows intact, no inline styles, no observers) and scenes (still
  reordered, anchored, pill present).
- **Type is Quicksand (display) + Nunito Sans (UI and body) +
  JetBrains Mono (code, date, metadata values), all self-hosted, via
  three tokens in fonts.css — `--jl-font-display`, `--jl-font-ui`,
  `--jl-font-mono` — and NO stylesheet names a font directly any more
  (2026-09-03).** This supersedes every earlier "Segoe UI" and Lato
  decision in this file (buttons.css v4's Segoe normalization, the
  Metadata/performer label stacks, the Lato sidebar-performer list):
  those were all choices between OS fallbacks — "Segoe UI" never
  rendered as Segoe UI on Linux or iOS, Lato only where the OS had it —
  and the user asked for something fresher. Chosen from a live type
  board (Manrope / Figtree / Nunito Sans, each rendered as the real
  sidebar and card); Nunito Sans was picked over the recommended
  Manrope for the one-rounded-family coherence with Quicksand,
  accepting a softer overall read. Both new faces are Google's variable
  builds (one WOFF2 per subset covers every weight used), latin +
  latin-ext, ~150 KB of base64 added to fonts.css. The vendored
  base-theme.css's three Lato/Roboto Mono declarations were retargeted
  to the tokens too, noted in its header. Verified with
  `CSS.getPlatformFontsForNode` (the only check that shows what
  painted) on body text, nav, mode bar, buttons, sidebar and card
  performer names, titles, studio codes and metadata rows. Two things
  the first verification pass caught: this instance's own **Custom CSS**
  (Settings › Interface, served at `/css` — not a plugin) carries
  `body { font-family: "Segoe UI", sans-serif !important }`, which beat
  the plain `body` rule and left the nav bar, mode bar and detail labels
  in Liberation Sans — fonts.css now uses `html body … !important` to
  out-specify it; and stash's own `code, .code` rule out-specifies
  inheritance, so the scene-card studio code needed the mono token named
  on it explicitly (clean-cards.css). To change a face again: edit the
  three tokens and the @font-face blocks in fonts.css, nothing else.
- **The File card's head carries stash's file-count badge
  (`.jl-head-count`, via `syncHeadCount()` in scene-dashboard.js,
  2026-09-03).** Stash puts `<span class="badge badge-pill">N</span>`
  on the File Info tab when a scene has more than one file; that tab is
  hidden behind the mode bar and File lives under Browse rather than
  being a mode, so the count is mirrored to where the tab's content
  went — the File card's head, between the label and the chevron,
  styled like the entity pages' `.jl-mode-count` pill. Read from the
  nav's own DOM on every `tagPanes()` run (React can change it) with
  every write equality-guarded; no badge means no span (removed, not
  emptied). Deliberately NOT on the Browse mode button: a bare "2" next
  to "Browse" says nothing about what it counts. Generic by
  `data-rb-event-key`, so another tab growing a badge is a one-line
  call. Verified on a two-file scene (34055) and a single-file one by
  injecting the working-tree plugin into the page, since the plugin was
  disabled in stash at the time.
- **`.filtered-list-toolbar` is boxed with the mode bar's own fill and
  outline (`--jl-pill-bg`, `--jl-line`, 8px radius) — buttons.css
  section 3, 2026-09-02.** Reported live right after the base theme was
  vendored: the toolbar strip above every filtered list "isn't getting
  themed." It never was — stash's bundle paints it `#202b33` and the
  base theme's only override for it uses a `.scene-list-toolbar…`
  selector that doesn't match current markup — it just passed under
  Dracula because the stock ground is close to Dracula's. The rule
  reuses the pill's tokens rather than copying numbers, adds 10px of
  horizontal padding so the border isn't flush with the first/last
  control, and repeats the base theme's three-class selector so it
  keeps winning if upstream markup brings that class back. Verified on
  the scenes grid and the performer page's relations list under Dracula
  and Kanagawa.
- **`markTagRows()` iterates to a fixed point (up to three passes), not
  one measure-and-flag pass.** Found by pixel-diffing the tokenization
  (2026-09-02): a fresh load intermittently left one tag row's first
  chip at the 8px interior margin instead of the 14px row-start margin.
  The flags this function writes change the chips' own margins, and a
  wider margin on a line's last chip can push it onto the next line —
  moving the row boundaries the flags were just computed from. Before
  the observer filter (see the performance entry below) the constant
  unrelated re-runs converged this by accident, one pass each; after it,
  the last run could land one pass short. Now: measure, flag, and if any
  flag actually changed (`setData` returns whether it wrote), measure
  again against the resulting layout. Steady state is one read pass and
  no writes, so the performance fix is untouched. Confirmed with three
  consecutive fresh loads at 0 differing pixels against the pre-change
  baseline.
- **2026-09-02 performance pass (scene-dashboard.js only): the body
  observer is filtered to sidebar-relevant mutations, every per-run DOM
  write is equality-guarded, and the backdrop sizers no longer reset
  before measuring.** Found by a CDP A/B profile (headless Chrome,
  jav-layout's JS/CSS blocked via `Network.setBlockedURLs` as the
  baseline — see the Testing section for the method). Page-load cost
  was minor everywhere (grid/performer ≈ 10 ms of plugin CPU; scene
  ≈ 70 ms, 45 of it the `getBoundingClientRect` loop in
  `markTagRows()`). The real cost was *ongoing*: the video player is a
  sibling of `.scene-tabs`, never inside it, and its progress bar/time
  tooltip mutate ~70×/s during playback — every batch re-ran
  `tagPanes()`, which re-measured the 48-chip tag cloud with ~3 forced
  synchronous layouts and rewrote ~1,500 unchanged attributes per
  second. Measured over 6 s of playback: +400 layouts, +300 style
  recalcs, ScriptDuration 283 vs 152 ms, TaskDuration 742 vs 367 ms
  (roughly doubling stash's own playback cost). After: 173 vs 136 ms
  script, 33 vs 27 layouts, 0 `getBoundingClientRect` calls from this
  plugin during playback. Three changes, and one that fell out of them:
  1. **`touchesSidebar(muts)`** — a batch schedules `run()` only if some
     record's target is inside the current `.scene-tabs`, or adds a node
     that is/contains one (fresh mount, scene→scene SPA navigation —
     that record's target is the *parent*, outside any `.scene-tabs`),
     or no `.scene-tabs` exists yet. Confirmed live that SPA navigation
     between two scenes still gets tagged and sized.
  2. **`setData`/`setAttr`/`setStyle`/`addClass`/`toggleClass`** — every
     write `tagPanes()`/`markTagRows()`/`syncModeBar()`/`applyCollapsed()`
     makes on every run goes through one of these and only touches the
     DOM on an actual change. Same-value attribute writes still queue
     observer records and invalidate style for every selector keyed on
     that attribute (most of scene-dashboard.css) — and, more
     importantly, a run that writes nothing leaves layout clean, so its
     reads force no layout at all. Writes on freshly-created nodes are
     left raw; they're one-time by construction.
  3. **No reset-to-zero before measuring** in `sizeTagsBackdrop()`/
     `sizeCodeDateBackdrop()`. The backdrop's height and its equal-and-
     opposite negative margin-bottom net to zero flow contribution, so
     neither its own `top` nor the last chip's `bottom` (nor Code's/
     Subheader's intrinsic heights) ever depended on the previous value.
     The reset was a write→read→write costing a forced layout per run.
  4. **Explicit re-measure triggers, because the constant re-runs had
     been silently papering over three stale-measurement cases.** The
     first `verify` pass after (1)–(3) showed a fresh load with the tags
     backdrop at 10px (should be 520) and Code/Date at 2px (should be
     28): `tagPanes()` measures *before* `syncModeBar()` sets
     `data-jl-mode` (which flips the flattening CSS and moves every
     chip), and previously some later unrelated run always fixed it.
     Now `measure(root)` (tag rows + both backdrops) runs from
     `syncModeBar` whenever the mode actually changes, from
     `document.fonts.ready`, and from a rAF-debounced `resize` listener.
     Alongside that, both sizers return early when their subject
     measures to an empty rect (Tags collapsed, any non-Browse mode, a
     collapsed sidebar) instead of flagging every hidden chip as "row 1"
     and writing a 0px height — CSS already hides the backdrop in those
     states, and the next visible-state run replaces the kept value.
     **If a layout-affecting state change is ever added that produces no
     childList mutation inside `.scene-tabs`, it needs its own
     `measure()` call — the observer will not catch it any more, by
     design.**
  Verified live after: fresh load, collapse→expand, load-collapsed→
  expand, Browse→Edit→Browse, and SPA scene→scene all measure exactly
  (backdrop height == last-chip-bottom − backdrop-top + 10; first-row/
  row-start/row-end flag counts == measured rows), 0 idle mutations, no
  console errors.

- **`.detail-group .detail-item` (performer/studio/group/tag detail-list
  rows — Gender/Age/.../Stash IDs) is converted from native's
  `display: table` to `display: flex`, not just given extra font/padding
  on top of the native layout.** Three requests in one 2026-09-02
  session: lower visual hierarchy via padding, let a long Stash ID value
  get cut off instead of forcing its own row's label to wrap, and
  center-align `.details-edit`'s buttons when they wrap to two rows on
  mobile. The padding and button fixes were simple; the Stash ID fix is
  why the row's `display` changed. Confirmed live: native
  `table-layout: auto` shrinks columns in proportion to a global width
  deficit and does NOT treat an `overflow: hidden` cell as free to shrink
  past its content's min-content the way flexbox does — with no
  `white-space: nowrap` on the label, auto-layout took width from the
  *label* column instead of the value column at mobile widths, wrapping
  "Stash IDs:" onto two lines while the pill rendered at full natural
  width regardless. Adding `white-space: nowrap` to the label alone
  wasn't sufficient either (tested and reverted) — it stops the label
  wrapping but doesn't stop the row growing past the viewport instead (a
  hard, ellipsis-less clip whenever some ancestor happens to have
  overflow-x hidden, if any does — not a design to rely on). The real fix
  needs the *value* side to be a genuine flexbox shrink target: only
  flex/grid items get the "an overflow:hidden item's automatic minimum
  size is 0" behavior that lets a flex-shrink:1 sibling absorb a width
  deficit instead of a non-shrinking one — table-cell auto-layout has no
  equivalent. `.detail-item-title` keeps a **fixed** `flex: 0 0 130px`
  (matching native's own `width: 130px`) rather than sizing to its own
  content per row — tested content-sized-per-row (`width: 1%` /
  `flex: 0 0 auto`) first and reverted: it breaks the column alignment
  every row shared under native's identical fixed width, which reads as
  a regression the instant two adjacent rows have differently-length
  labels, and was never asked for.

  **The Stash ID pill needed its own descendant-chain fix on top of the
  flex conversion** — confirmed live that `min-width: 0`/`overflow:
  hidden` on `.detail-item-value` alone doesn't propagate down through
  `<ul>`/`<li>`/`.stash-id-pill`/`<a>` (four more markup layers): `<ul>`
  is native `display: block`, `<li>` is native `display: flex`, and
  neither is itself a flex item of an ancestor whose overflow lets ITS
  automatic minimum resolve to 0 — each layer silently renders at its own
  full natural width, undoing the fix, unless each layer separately gets
  `min-width: 0` (`overflow: hidden` too, for ul/li specifically). Scoped
  to `.stash-ids` rather than applied blanket to every
  `.detail-item-value ul/li`, since no other field currently has content
  long/unbreakable enough to hit this (URLs field wraps fine on `/`) —
  revisit narrowly if another field needs it, don't widen the selector
  preemptively.

  Verified live on both performer (mobile 390px: labels stay one line,
  Stash ID values ellipsis-truncate to fit exactly within the viewport,
  `pageScrollWidth` unchanged from a pre-existing unrelated 15px overflow
  elsewhere on the page — confirmed via baseline diff, not caused by this
  fix) and desktop (1400px: column alignment and full-width values
  unchanged from before) — and cross-checked on the studio page (shares
  `.detail-group` via the same generalized selector) at mobile width:
  URLs and Parent Studios fields both single-line, aligned, zero page
  overflow. 0 idle mutations.
- **Edit mode's own `.details-edit` had a second, unrelated wrapping
  problem the `justify-content: center` fix didn't touch: a native rule
  grows one specific child to fill the whole row.** Reported live
  2026-09-02, initially described as "modal button bar" (there is no
  modal here — Edit mode's toolbar, same `.details-edit` class as view
  mode) with "too much space on the left side, and the right side
  extends past the page," confirmed specific to mobile rendering.
  Root cause, found via `CSS.getMatchedStylesForNode`, not assumed: native
  ships `.col-md-8 .details-edit div:nth-last-child(2), .detail-header
  .edit .details-edit div:nth-last-child(2) { flex: 1 }` — a deliberate
  rule that grows the second-to-last child (the unclassed wrapper div
  around the Clear Image button) to fill all remaining row width,
  shoving Save alone against the far right edge. By design this reads
  fine on desktop where the row never wraps — a deliberate gap separates
  the primary Save action from the rest. Once the row wraps (native's own
  `flex-wrap: wrap`, confirmed pre-existing, not something this plugin
  added), that same grow-to-fill div lands *alone* on the second line
  with just Save, consuming nearly the entire line — Clear Image pinned
  far left, Save pinned far right. Fixed with
  `.details-edit > div:nth-last-child(2) { flex: 0 1 auto !important; }`
  in entity-dashboard.css, applied unconditionally rather than only at
  narrow widths: even on desktop, a stretched spacer conflicts with the
  "read as one balanced, centered cluster" intent the justify-content fix
  above already established for this exact bar — leaving one child at
  grow:1 would silently reintroduce the same left-cluster/right-single-
  button split this plugin already chose against. Confirmed live on both
  performer (5-button Edit-mode toolbar, Scrape with… included) and
  studio (4-button, no Scrape) at mobile and desktop widths — Clear Image
  and Save now sit adjacent, centered, no dead space, 0 idle mutations.
- **`.jl-modes` (the mode-bar pill, shared by the scene page and all four
  entity pages) gets `justify-content: center` — a wrapped second row was
  left-justified instead of centered.** Reported live 2026-09-02 on the
  tag page (7 tabs — Scenes/Images/Galleries/Groups/Markers/Performers/
  Studios) at a mobile viewport width, where the bar wraps to two rows;
  with no `justify-content` set, the default `flex-start` packed the
  (narrower) second row against the pill's own left edge instead of
  centering it under the first. Same symptom, same fix as `.details-edit`
  just above it in this same session. Confirmed live this only matters
  once wrapping actually happens — performer/studio/group's own tab
  counts (6 or fewer) fit on one line even on a phone, and a single-line
  bar has no leftover space for `justify-content` to redistribute either
  way, so this is safe unconditionally, no width/media-query gating
  needed.
- **The 2026-09-02 performer-page detail-list session (padding, Stash ID
  truncation, `.details-edit` centering) went through a same-day
  follow-up correcting three things once seen live — see
  entity-dashboard.css's own comment above `.detail-group .detail-item`
  for the full reasoning on each:**
  1. **Indent** — the block read flush with the name/aliases column
     above it; fixed with `padding-left: 16px` on `.detail-group` itself.
  2. **Row spacing overshoot** — the `padding-bottom: 12px` bump (added
     for "lower visual hierarchy") was reported as too much once live;
     reverted to native's own 7px, kept explicit rather than deleted.
  3. **Age's dotted underline stretched the full row width** — native
     puts `border-bottom: 1px dotted` directly on `.detail-item-value`;
     giving that element `flex: 1 1 auto` (to fill the row) stretched the
     border along with it. Fixed by dropping flex-grow to 0
     (`flex: 0 1 auto`) — shrink (needed for the Stash ID truncation fix)
     is independent of grow and still works, but every other row's value
     box now hugs its own content again instead of stretching to fill
     the row.
- **Same session, one more pass: `.detail-group .detail-item` rows now
  match the scene sidebar's own File block exactly — 23.5px top-to-top,
  gap-to-gap, not approximated.** Requested live as "tighten up the
  vertical spacing... match the vertical spacing used for the File tab."
  File's native `<dt>`/`<dd>` (scene-dashboard.css's
  `.scene-file-info dd { margin-bottom: 4px }`) carry no vertical padding
  at all — row height is pure line-height (18px/19.5px, already matched
  font-size-for-font-size in the pass above), plus dd's 4px margin as the
  only separator, measured live at 23.5px/row. This block's own rows
  measured 32.5px before this pass — not a font mismatch, but 3px top+3px
  bottom padding this file had added to title/value (from the *original*
  "lower visual hierarchy" ask, before that ask was itself walked back —
  see the row-spacing-overshoot correction above) plus a 7px item
  padding-bottom, versus File's zero+4px. Matched by mirroring File's
  mechanism, not guessing at a smaller number: title/value's vertical
  padding to 0 (their horizontal padding — the label/value gap — is
  untouched, that's not part of vertical rhythm), item's own
  `padding-bottom` from 7px (already reduced once, see above) to 4px.
  Confirmed live: 23.5px on every row on both performer and studio pages,
  desktop and mobile; Stash ID truncation and the 16px indent both still
  hold at mobile width, 0 idle mutations.
- **A compact performer name list sits in the persistent header, between
  the Code/Date bar and Title (`.jl-sidebar-performers`, order: 15,
  scene-dashboard.css) — additive, not a replacement for the native
  Performers grid.** Requested live 2026-09-01 in two steps: first "match
  the scene-card performers list, same functionality, look, and feel"
  (read as replace the native big-card grid in the Browse-only Performer
  section with this compact list), then corrected — "keep the native
  performer display in the scene sidebar and move the performer names
  between the code-date bar and the title to match the location in the
  scene-cards." **The first version is not a dead end worth forgetting**:
  it's exactly what this settled on, just relocated to the header instead
  of the Browse body, and left the native grid (`.scene-performers`,
  further down, Browse-only, still with its 190px two-per-row width fix —
  see below) completely untouched. The list reuses `.stash-performers`,
  the same class the scene-card grid tiles use (clean-cards.css/js), so it
  inherits that rule's cyan-link/nowrap+ellipsis/dot-separator look and the
  same hover-popup behavior (photo/name/disambiguation) for free — the
  popup itself is reused via `window.JLPerformerPopup`, exposed by
  clean-cards.js (loads first, see jav-layout.yml) rather than
  duplicated. Built by `buildSidebarPerformerList()` in scene-dashboard.js,
  reading id/name/disambiguation/image straight out of the native grid's
  own already-rendered markup (no extra GraphQL fetch, unlike the
  scene-card grid's own version, which fetches because its card doesn't
  render full performer data). Order matches the scene-card tile's own DOM
  sequence exactly: code-date bar, then performers, then title (see
  `enhanceCard()` in clean-cards.js) — order 15, between the backdrop (10)
  and Title (20). Rebuild is fingerprinted on the performer id list, not
  unconditional, for the same self-triggering-mutation-loop reason as the
  Framerate/Resolution mirrors (see tagPanes() in scene-dashboard.js).
  Unlike the Browse-only Details-pane content, this list carries no
  `[data-jl-mode="browse"]` gating — it's meant to stay visible in every
  mode, same as Title/Toolbar/Studio Code.
- **The Code/Date bar (`.jl-codedate-backdrop`) is deliberately 10px
  wider than every other full-line item in the sidebar** (Title, Toolbar,
  the mode bar all still get plain `flex: 0 0 100%`) — 5px past the
  shared left edge, 5px past the shared right edge, via `flex: 0 0
  calc(100% + 10px); margin-left: -5px`. Requested live to make the bar
  read as more of an anchor for the sidebar, addressing Title sometimes
  feeling too far left relative to it. **A `flex: 0 0 100%; margin: 0
  -5px 0 -5px` version was tried first and was wrong** — for a flex item,
  margin doesn't add to a fixed flex-basis's own rendered width, it only
  shifts the box: confirmed live that version left the box at exactly the
  same 409px width, just shifted 5px left, so the *right* edge actually
  moved 5px inward instead of outward. The extra width has to come from
  flex-basis itself (`calc(100% + 10px)`), with a single margin-left to
  center it. Both directions were checked for safe room before
  committing to 5px/5px, not just picked: left has `.scene-tabs`'s own
  15px padding-left to give back from; right has its 16px padding-right
  *plus* a separate ~10px the sidebar's own scrollbar gutter already eats
  natively (confirmed live: content's measured right edge sits 26px
  short of the container's true right edge, only 16 of which is the
  padding-right rule) — 5px stays well inside the 16px padding portion,
  nowhere near the scrollbar track itself. Code/Subheader's own text
  positions are untouched — they still start/end at the old, narrower
  edges, so the extra width shows as more colored bar peeking out on each
  side, not the text shifting.
- **The scene-card grid's own Code/Date bar (`.stash-code-date`,
  clean-cards.css) got two follow-up fixes the same day (2026-09-01),
  both requested live, both applied to its sidebar counterpart above
  too for consistency:**
  1. **Padding split from mismatched originals (4px card / 10px
     sidebar) to a common 9px** — but not a plain arithmetic split.
     The sidebar bar's own backdrop already extends 5px past its
     content on each side (the anchor rule directly above), so its
     *true* text-to-visible-edge gap was actually 10+5=15px, not the
     10px its padding value alone suggested — confirmed by measuring
     live, not assumed. Split against the card's real 4px and the
     sidebar's real 15px: (4+15)/2 = 9.5, rounded to 9. Landed as
     `padding: 0 9px` on the card bar (no backdrop offset to account
     for there) and `padding-left`/`padding-right: 4px` on the
     sidebar's Code/Date elements respectively (4+5=9, matching).
  2. **Then found to be narrower than the title text's own margin** —
     reported live after the padding fix landed. Root cause: the card
     bar's outer box was sized by plain flex-stretch (the parent `<a>`
     is `display: flex; flex-direction: column`, so children fill 100%
     width on the cross-axis by default) — meaning it exactly matched
     the title container's own width, not wider, so the 9px padding
     read as the *only* inset and looked tight next to title text with
     none. Fixed with the exact same anchor technique as the sidebar
     bar above: 10px wider than the title (5px each side), via
     `box-sizing: border-box; width: calc(100% + 10px); margin-left:
     -5px` — `width`, not `flex-basis`, since flex-basis governs the
     *main* axis (vertical, in a column flex) not the cross-axis width
     a plain `width` controls here. Padding stayed at 9px unchanged on
     purpose — code/date's distance from the bar's *own* edge doesn't
     move, only the bar's edge itself shifts outward past the title's.
     Confirmed live afterward: bar is exactly 10px wider than the title
     text across every card checked, code/date still measure exactly
     9px from the bar's own edge, card's own 5px border-radius leaves
     ample room before the new 10px inset, 0 idle mutations.
  3. **The 5px-each-side overshoot from fix 2 was then getting clipped
     flush square — rounded corners cut off entirely.** Reported live
     immediately after fix 2 shipped. Root cause: `.scene-card
     .card-section > a` (the same `<a>` this bar/title/performers all
     live inside) carries this plugin's own `overflow: hidden` — a
     leftover from the original v5.4 port, there for *vertical* overflow
     safety (`min-height: 0` alongside it gives this away: a flex column
     needs that pairing to stop a long description from blowing out the
     card's fixed height) — with zero horizontal slack of its own: its
     box exactly matches the title's width, confirmed live, no
     margin/padding to spare. First attempt — splitting to `overflow-x:
     visible; overflow-y: hidden` on that same `<a>` — created a *worse*
     regression: per spec, pairing `visible` on one axis with a non-
     visible value on the other forces the visible one to compute as
     `auto`, and since the bar's overshoot is genuine overflow (not
     merely visual), that produced an actual horizontal scrollbar,
     confirmed live via a screenshot showing the scrollbar track and
     reverted immediately once seen — not something to ship and hope
     looks fine.

     **Real fix: moved the bar itself out, to be a sibling of that `<a>`
     instead of a child inside it** (`enhanceCard()` in clean-cards.js —
     `titleContainer.closest('a').before(bar)`, was
     `titleContainer.before(bar)`; performers now inserts via
     `titleContainer.before(perf)` instead of `bar.after(perf)`, keeping
     it exactly where it always was, inside the `<a>`, immediately before
     title — only the bar's own position changed). Its new parent,
     `.card-section`, has real room to spare (14px padding, confirmed
     live) with its own overflow boundary well outside the bar's
     overshoot — no mixed-axis scrollbar risk, since `.card-section`'s
     own `overflow: hidden` is uniform on both axes, and `hidden` (unlike
     `auto`) never renders a scrollbar regardless of genuine overflow.

     **One more thing this move exposed, not introduced by it**:
     `.card-section` already has its own `gap: 4px` between direct
     children — previously inert, since the `<a>` was its only visible
     child (the other, `.scene-card__details`, is `display: none`).
     Making the bar a second visible child activates that gap for the
     first time, which would have *doubled up* with the bar's own
     `margin: 0 0 4px` (4+4=8px instead of 4px) — the same row-gap-plus-
     margin bug family documented at length elsewhere in this file.
     Fixed by dropping the bar's margin to `0`, relying purely on
     `.card-section`'s own gap now, same as every other pair of adjacent
     children there implicitly does.

     Verified live: both rounded corners render fully again (screenshot-
     compared against the clipped state), gap between bar and the title
     block measures exactly 4px (not 8), no scrollbar at any viewport
     width tested (1400px down to 380px), the 10px bar-vs-title width
     difference holds at every width including 380px, 0 idle mutations.
- **Code and Date now share one monospace font
  (`source-code-pro, Menlo, Monaco, Consolas, "Courier New", monospace`)
  in both the scenes grid (`.stash-code-date`, clean-cards.css) and the
  scene detail page's own Code/Date bar
  (`.scene-subheader`, scene-dashboard.css) — Date didn't, before this.**
  Checked live first, not assumed: `.code` in both places was already
  monospace (grid inherits it natively with no rule of this plugin's own;
  detail's `.studio-code` has carried the stack explicitly since early in
  this file's history) — `.date` in both places had never gotten the same
  treatment, inheriting plain `"Segoe UI"` (→ Liberation Sans) same as
  surrounding body text, so Code and Date read as two different typefaces
  sharing one pill despite being visually presented as one unit. Fixed by
  adding the identical `font-family` declaration to `.stash-code-date
  .date` and `.scene-subheader .date` — confirmed via
  `CSS.getPlatformFontsForNode` that all four (grid code/date, detail
  code/date) now resolve to the same actual font (Liberation Mono),
  not just the same declared stack.
- **Nav bar font-weight: tried 600 (matching `.jl-mode`, the mode-bar
  pills), reverted to native 500 — "too strong" once seen live.** Native
  already gives the nav bar weight 500 with zero rule from this plugin
  (confirmed via matched-rules inspection, not just computed style, before
  the 600 attempt: traced to a native `.btn{font-weight:500}` base rule).
  So the reverted state needs no CSS at all — there's deliberately no
  `.top-nav .btn.minimal` weight rule in `buttons.css` right now, and
  that absence is correct, not a gap. Full context (why 600 was tried,
  what it turned out to actually match/not match) is in `buttons.css`'s
  own comment at the reverted rule's old location — don't silently
  reintroduce a nav-weight override without re-reading it and confirming
  live against both reference points (this file's own 500 buttons, and
  the mode-bar's 600 pills) that it's actually wanted this time.
- **All buttons this plugin styles (`buttons.css`'s `.btn:not(.minimal)`
  rule — nav bar excluded, always native) render in `"Segoe UI",
  sans-serif`, byte-identical to the nav bar's own native font stack,
  not Lato.** Third font-family this rule has carried: v3 picked Lato to
  fix a real nav-bar font-weight leak and to match scene-card titles;
  this reverted that pick specifically (kept v3's weight, 500 — a
  separate, still-valid fix for heaviness) on explicit request to
  normalize every button — nav bar, modal confirmations, detail-page edit
  rows — to one font. "Segoe UI" was never actually installed on this
  server before this change and still isn't; both v3 and this resolve to
  a fallback (Lato vs. Liberation Sans respectively) rather than the
  literal named font anywhere. The point of this change isn't rendering
  literal Segoe UI, it's that every button now resolves to the *same*
  fallback the nav bar's own untouched native rule already produces,
  closing a three-way font split (nav bar native / this rule's own pick /
  scene-card titles) down to two matching halves. Full version history in
  buttons.css's own header comment and the buttons.css section below.
- **Scene titles and performer names render in Quicksand at weight 600,
  not native Lato/Liberation Sans.** Applies in four places: scene-card
  titles and performer-card names (`clean-cards.css`, `.card-section-title
  .TruncatedText`, both scene and performer variants), the scene detail
  page's own Title (`clean-cards.css`,
  `.scene-header-container .scene-header .TruncatedText`), and
  `.performer-name` (originally `performer-dashboard.css`, now
  `entity-dashboard.css` — see that section above). Checked live before
  picking anything: scene-card titles already rendered in native Lato, but
  the other three fell back to plain Liberation Sans — their native rule
  is just `"Segoe UI", sans-serif` with nothing better behind it, an
  inconsistency nobody had chosen on purpose. Quicksand won out of a
  mockup comparing it against Lato-everywhere/Inter/Inter Display, each at
  weight 500/600/700, across real titles at each context's own native
  size (19–28px depending on context) — 600 was the weight sweet spot,
  500 read thin against the surrounding chrome and 700 read as shouting
  on long scene titles. Confirmed installed on the server via `fc-list`
  before shortlisting it, and confirmed via `CSS.getPlatformFontsForNode`
  post-change that it's genuinely rendering as Quicksand in all three
  checkable contexts (list-page nodes don't always return a result from
  that call — a measurement-visibility quirk, not a rendering failure;
  `getComputedStyle`'s declared value still confirmed correctly on the
  fourth). **`.studio-name` got the identical treatment one turn later**,
  on request — deliberately left out of the first pass since the original
  ask was scoped to scene titles and performer names specifically, then
  added once asked for by name. `.group-name`/`.tag-name` picked up the
  same rule automatically once all four entity pages were consolidated
  into `entity-dashboard.css` — one shared selector, not per-entity
  copies (see that section above). All title/name contexts in the app now
  agree. **Quicksand itself is now
  self-hosted** (`fonts.css`, see above) rather than relying on the OS
  having it installed — a real gap this session's own verification method
  couldn't have caught on its own, since checking font availability and
  rendering both happened on the same machine serving the page.
- **Metadata, Details and Tags are boxed cards, not plain flattened text.**
  An earlier version left Original Title / Scene Details / Description / Tags
  as bare flattened items with no chrome. They're now scattered-group cards
  (see Layout above) matching File/History's look. Subheader (date/fps/
  resolution) is native and visible, its own strip between Original Title
  and Toolbar — an earlier version hid and mirrored it into Metadata instead;
  see the reverted-mirroring note further down.
- **`.scene-subheader` gets an explicit `padding-right: 14px`, unlike every
  other identity-header item.** Native stash gives it zero horizontal
  padding and its own `justify-content: space-between`, which — combined
  with `.scene-tabs`'s own internal scrollbar eating the last 10px of its
  content box — pins the fps/resolution group flush against the sidebar's
  true right edge. Every other right-inset reference point in the sidebar
  (Metadata/Details/Tags' `padding-right: 14px`, the studio-code pill's
  auto-margin gap) reads as more contained, so Resolution looked like it was
  spilling past their bounds even though it never actually crossed the flex
  item's own outer edge. 14px matches the scattered-group cards' inset,
  landing Resolution's right edge exactly on Metadata's text (not border)
  right edge.
- **Tags is boxed like Metadata/Details, but via a different mechanism —
  `.jl-tags-backdrop`, not per-member `--jl-well` fills.** First attempt
  gave every `.tag-item` its own `--jl-well` background, same as Metadata's
  rows; reverted because it either overwrote the chips' native pill colors
  (if applied straight to `.tag-item`) or, avoided by wrapping, left visible
  seams at the gaps between chips and between wrapped lines — worse than
  Metadata's version of that problem, since chips wrap irregularly instead
  of each claiming a full line. `.jl-tags-backdrop` is a single real element
  (JS-appended, `sizeTagsBackdrop()`) sized every run to the chip cloud's
  current bounding box and placed *before* the chips in DOM order with a
  height/negative-margin pair that hands its reserved space straight back —
  so it paints once, seamlessly, behind the whole cloud, while the chips
  (later in DOM order, same stacking context) paint over it with their own
  colors intact. No z-index needed; normal same-stacking-context paint order
  already puts later siblings on top of earlier ones. Metadata's `<h6>`
  lines and the Details paragraph use the simpler per-member-fill approach
  instead, matching File/History's `dt`/`dd` text (`--jl-muted`/`--jl-text`,
  12–13px) — that works fine there because each row already claims a full
  line, so there's no wrapping to create seams.

  One landmine already hit: a 0-height div still paints its own
  `border-bottom` as a visible 1px line. When there are no chips to measure
  (zero tags, or the card is collapsed and every chip is `display:none`),
  `sizeTagsBackdrop()` must set `display: none` outright — leaving
  `height: 0` and trusting the border to disappear on its own does not work.
- **Wrapped tag rows get the container's `row-gap: 10px` back; row 1 alone
  still cancels it.** Every `.tag-item` originally carried the same
  `margin-top: -10px`, which — being per-chip, not per-row — zeroed the gap
  between every wrapped line along with the one above row 1 (the one that
  actually needs cancelling, to sit flush under the divider/backdrop). CSS
  has no "first line" selector to split those two cases, so
  `markTagFirstRow()` in scene-dashboard.js measures each chip's
  `getBoundingClientRect().top` after layout and flags the ones matching the
  first chip's top with `data-jl-tag-first-row="true"`; only those get the
  cancelling margin now, via an attribute-gated rule instead of the blanket
  one. Row membership is a horizontal (main-axis) wrap decision the flex
  algorithm makes independent of this vertical margin, so measuring first
  and correcting the margin after is safe — it can't feed back into which
  chips land on which line.
- **The Tags collapse toggle re-runs `sizeTagsBackdrop()` (row marking
  included), not just the `data-jl-collapsed-tags` flag.** `tagPanes()`
  calls `sizeTagsBackdrop()` once per run, which is fine when Tags starts
  expanded — but a page load that starts Tags *collapsed* (persisted state)
  measures every chip while `.tag-item` is `display:none`, so every chip's
  `getBoundingClientRect()` comes back zeroed: `markTagFirstRow()` flags all
  of them as row 1 (all "tops" equal 0), and the backdrop measures a
  near-zero cloud height. Expanding afterward only flipped the attribute —
  the stale measurement stuck, so the card rendered too short with every
  row's `row-gap` wrongly cancelled (all margin-top: -10px). Fixed by having
  the Tags head's click handler call `sizeTagsBackdrop(parent)` itself,
  after `applyGroupCollapsed()` flips the attribute — `getBoundingClientRect
  ()` inside it forces a synchronous reflow, so it measures the
  post-toggle DOM state correctly regardless of which direction the toggle
  went.
- **`.jl-tags-backdrop`'s collapsed state needs its own CSS rule — it isn't
  derived from `sizeTagsBackdrop()` re-running on every collapse toggle.**
  The Tags head's click handler (`ensureGroupHead`'s listener) only flips
  `data-jl-collapsed-tags` via `applyGroupCollapsed()`; it never calls
  `sizeTagsBackdrop()`. Collapsing right after a page load that started with
  Tags expanded left the backdrop's last-measured inline `height`/
  `display` sitting there unchanged — chips hidden by the `.tag-item`
  collapse rule, but the full outlined box behind them still visible. Fixed
  with a plain `[data-jl-collapsed-tags="true"] .jl-tags-backdrop { display:
  none !important; }` rule, the same pattern as the `.tag-item` collapse
  rule right above it — cheaper and more robust than teaching the click
  handler to re-measure.
- **Tags' own head has `padding-bottom: 0`, overriding the `9px` the shared
  Metadata/Details/Tags head rule gives it — reported live (2026-09-01) as
  visibly more whitespace above the chip cloud than below it.** The 9px
  sits inside the head's own box, below the divider — separate from
  `.jl-tags-backdrop`'s own `margin-top: -10px` (cancels the container's
  row-gap between head and backdrop) and its 10px internal inset to the
  first chip row. Stacked: 9 + 0 + 10 = 19px above the first row, against a
  symmetric 10px below the last (confirmed via `getBoundingClientRect` on
  head/backdrop/first-and-last-chip, not assumed from the CSS numbers).
  Metadata/Details don't show this — their own rows only add 3px on top of
  the same 9px (12px vs. 10px, real but far smaller), so the fix is scoped
  to Tags' own head rule rather than the shared one. Full reasoning is in
  scene-dashboard.css right above the rule (search
  `.jl-head[data-jl-card="tags"] { padding-bottom: 0 }`). Verified: the
  collapsed state (a plain bordered box with no body) is unaffected — the
  head's own top/left/right/bottom border is set independently of this
  padding.
- **Studio Logo carries one override of its own: `margin-top: 4px`, trimmed
  down from stash's native `1rem` (14px) to tighten the gap above it by
  10px.** Otherwise no styling overrides — only flattening (its own
  full-width line, lowest order). An earlier version force-capped it at
  `max-height: 60px`, `opacity: 1`, `text-align: left` — a deliberate
  Dracula-specific restyle. Reverted in favor of the original v5.4 look
  clean-cards.css's `.studio-logo img` rule already provides (100px,
  opacity: 0.85) plus whatever centering stash's own bundle CSS gives the
  `h1.studio-logo` container.
- **Studio Code sits on its own line between Studio Logo and Title** (order
  10, vs. 5 and 20) — moved there from sharing Studio Logo's row (pushed
  right with `margin-left: auto`), and before that from its own line down
  by Subheader. Its corner radius once had a Dracula-specific
  `border-radius: 8px` pill override on top of clean-cards.css's native
  3px; reverted, along with everything else about its native look (size,
  horizontal centering margin, colors) except one thing: its *vertical*
  margin is deliberately overridden to `4px 0` (`!important`, needed to beat
  clean-cards.css's own `!important`). Native `margin: 18px auto 12px`
  reads as roomy once this element has a full line of its own — sized for
  sharing Studio Logo's line, not for anchoring a line by itself — and
  stacks with the container's row-gap: 10px on top of that for a ~28px/22px
  gap either side, the same row-gap-plus-margin double count documented
  elsewhere in this file. The horizontal `auto` half of the native margin
  stays untouched; that's what centers it.

  Landmine hit while wiring this up: the flattened items elsewhere in this
  file all use `flex: 0 0 100%` to force their own line, so the first
  version of this rule copied that pattern — but `.studio-code`'s native
  rule is `width: fit-content`, and **`flex-basis` overrides `width` for
  sizing on a flex item**, so `flex: 0 0 100%` silently stretched the pill
  to the sidebar's full width, discarding `fit-content` even though nothing
  ever touched `width` directly. `flex: 0 0 auto` fixes it — `width:
  fit-content` governs sizing again — and the element still lands on its
  own line regardless, because Studio Logo and Title on either side both
  force full-width lines of their own. Any future flattened item that's
  supposed to hug its content (not fill the line) needs `0 0 auto` here,
  not the `0 0 100%` most of this file reaches for by default.
- **Multi-performer scenes get `.performer-card` shrunk to
  `calc(50% - 10px)` (from the native `210px`), gated behind
  `:has(.performer-card ~ .performer-card)`.** Native card width plus its
  own 5px margin (220px per card) doesn't fit two per row in this
  sidebar's usable width, so every scene with more than one performer
  silently fell back to one huge card per line — stash's own
  `.row.justify-content-center` flex-wrap never got the chance to wrap
  two per line the way it does at wider native widths. A single-performer
  scene isn't touched at all (`:has()` doesn't match), and an odd last
  card in a 3+ scene still centers alone on its own line, exactly like
  today — no grid, no other rule changed.

  **Went through two fixed-px values before landing on a responsive
  formula, each fixed value breaking the moment the sidebar's own usable
  width changed again — the actual lesson, not just the history.** `200px`
  broke once `.scene-tabs[data-jl-mode]`'s own `padding-right: 16px` (added
  to bring every block's right edge in line with the Code/Date bar)
  narrowed the sidebar's usable width from 425px to 409px — two 200px
  cards (420px with margins) no longer fit, so multi-performer scenes
  silently fell back to one column again until caught and remeasured to
  `190px`. That value then broke again the same way — reported live
  2026-09-02 — the moment the sidebar was viewed on **mobile**, where
  `.scene-tabs` is the full (much narrower) viewport rather than the
  fixed desktop width `190px` had been tuned against: same failure mode,
  a second width nobody had measured for. Fixed for good this time with
  `calc(50% - 10px)` instead of a third fixed number: each card's native
  5px+5px margin is 10px per card, 20px for the pair, so half the
  container minus 10px always leaves exactly enough room for two,
  self-adjusting to whatever the container's actual width is — this
  sidebar's own padding changing again, a future breakpoint, any device —
  with no remeasuring required. Confirmed live at both mobile (390px
  viewport, two 170px cards) and desktop (409px sidebar, two 195px
  cards), same rule, no media query.
- **The performer hover popup is gated on `(hover: hover)` — touch-only
  devices (iPhone included) never see it, and a document-level
  `touchstart` dismisser covers hybrids.** Reported live from a real
  iPhone (2026-09-02): "the popup won't clear." Root cause is structural,
  not a styling bug: the popup's ONLY dismiss path was `mouseleave` on
  the anchor, and iOS Safari has no real hover — a tap on a
  hover-reactive link fires an *emulated* `mouseenter` (popup shows) and,
  because that handler visibly mutated the DOM, iOS withholds the
  navigation click; `mouseleave` then only fires if a later tap lands on
  another hoverable element, and frequently never fires at all — so the
  first tap summons a `position: fixed` body-level popup that nothing can
  ever dismiss. Reproduced the mechanism under CDP mobile emulation
  before fixing (emulated mouseenter on a `hover: none` device → popup
  visible → click/tap anywhere else → still visible, no dismiss path
  exists). Fix, all in clean-cards.js (both trigger sites — the card grid
  and the sidebar list via `window.JLPerformerPopup` — route through the
  same `showPerformerPopup`, so one gate covers both): (1)
  `showPerformerPopup` bails when `matchMedia('(hover: hover)')` doesn't
  match, checked at call time (not cached) since convertibles can change
  it mid-session — on hover-less devices a performer tap now just
  navigates on the first tap, stock behavior; (2) a new
  `hidePerformerPopupNow()` (unconditional, no anchor check, no 60ms
  debounce) driven by a capture-phase passive `touchstart` listener on
  `document` that dismisses the popup on any touch not inside the current
  anchor — for hybrid devices (touchscreen laptop, iPad+trackpad) whose
  *primary* pointer hovers so they pass the gate, but whose screen-taps
  fire the same emulated mouseenter; also exposed as
  `JLPerformerPopup.hideNow`. Verified live: mobile emulation → popup
  never shows; desktop (hover stubbed to `hover`, since pointer-less
  headless Chrome itself genuinely reports `hover: none`) → hover shows,
  mouseleave hides, touch-away dismisses immediately, touch ON the
  anchor doesn't flicker it; 0 idle mutations. **Real-iPhone
  confirmation still pending** — same verification gap as every
  iOS-reported issue here (no real device on this end); the gate rests
  on iOS reliably reporting `hover: none`, which is a platform guarantee,
  so confidence is high.
- **The sidebar performer list's font-weight is 500, not 600 — reverted
  one step from where it landed the same day it was built.** When
  `.jl-sidebar-performers` first shipped (see its own entry above), a
  font-family/font-size fix intentionally went a step past matching the
  scene-card grid's own performer text — 16px/600 (Lato Semibold) against
  the card's 15px/500 (Lato Medium), deliberately heavier since this
  list sits in the more prominent header context. Requested live
  2026-09-02 — "reduce the performer font weight by 100" (confirmed as
  specifically this list, "the performer text above the title") — pulled
  back to 500, still resolving to a genuine Lato-Medium face (confirmed
  via `CSS.getPlatformFontsForNode`, not a synthetic bold), now matching
  the scene-card grid's own weight exactly. 16px stays — only the weight
  half of the original "step past parity" was walked back, not the size.
- **The sidebar performer list's gap to Title is 4px, matching Title's
  own gap to Original Title exactly — same session, same mechanism.**
  Requested live 2026-09-02 immediately after the Title/Original Title
  tightening below: "reduce padding between the performer and title
  text - match the spacing between title and original title."
  `.jl-sidebar-performers` gets `margin-bottom: -6px`, the same
  -6px-off-`row-gap: 10px` trick `.stash-original-title`'s own
  `margin-top: -6px` uses — landing on the identical 4px gap. The gap
  *above* the performer list (from the Code/Date backdrop) is untouched,
  ~10px as before — only the performer-to-Title gap was asked to
  tighten, so only that side's margin changed. Briefly reverted the same
  session on a scope-confirmation misunderstanding (worry that this had
  touched the native `.scene-performers` grid further down instead — it
  never did, only `.jl-sidebar-performers` was ever in this rule), then
  confirmed correct and re-applied.
- **Title/Original Title/Metadata's info line (Resolution | Framerate |
  Director) got two opposite spacing nudges the same session, both off
  the container's own `row-gap: 10px` baseline.** Requested live
  2026-09-02 as one combined ask: "reduce padding between Title and
  Original Title... so they read more related" (`margin-top: -6px` on
  `.stash-original-title`, pulling 10px down to 4px — a
  translation/romanization pairing reading as one grouped unit, same
  logic as a caption sitting close under its heading) and "add a small
  additional padding between Original Title and the resolution,
  framerate, and Director display" (`margin-top: 6px` on
  `.jl-scene-badges`, pushing 10px up to 16px — explicitly the opposite
  direction, so pulling Original Title closer to Title above it doesn't
  also drag it into the metadata line below by proximity). Both are flat
  additions applied regardless of whether Original Title is present on a
  given scene — a scene with no Original Title just sees Title followed
  directly by the metadata line at row-gap(10)+6=16px, which is harmless
  (still reads as normal spacing, not broken), not something either rule
  needs to special-case for.
- **Date's color was pulled from clean-cards.css's uniform bold `#f8f8f2`
  to `--jl-pink`, before fps/resolution moved to Metadata and Studio Code
  moved onto this same line.** `#f8f8f2` isn't one of this plugin's own
  palette tokens (`--jl-pink`/`cyan`/`lilac`/`text`/`muted`/`dim`, see
  `:root` in scene-dashboard.css), and at the time gave Date no more visual
  weight than the fps/resolution detail next to it despite being the one
  fact worth scanning for. `--jl-pink` happens to also be Studio Code's own
  native color, which reads as intentional now that the two sit pill-by-pill
  on one joined bar (see above) — that pairing was a lucky side effect of
  this fix, not its original motivation.
- **`.performer-disambiguation` (the "(愛須心亜)" text next to a performer's
  name) gets `color: #73c1e6`, matching its own copy button** — clearly
  meant to match the text it copies, but the text span itself had no rule
  of its own anywhere in this codebase at the time, so it silently fell
  back to stash's own muted default instead of standing out the way it
  used to before the dashboard rework. **2026-08-31: this rule, its copy
  button, and the performer-name/studio-code copy buttons generally were
  all extracted into a separate `copy-buttons` plugin** (own repo, source
  in `stash-plugins`) — none of it lives in this file any more. This
  plugin no longer builds or styles any copy button; if one looks wrong,
  check `copy-buttons.css`/`.js`, not here.
- **2026-09-02: title cleanup (`TITLE_FILTER_WORDS`, `cleanTitleText()`,
  and its two call sites — the scenes-grid card title and the scene
  detail page's own header title) was extracted into a separate
  `title-scrubber` plugin** (own repo, source in `stash-plugins`), same
  pattern as the `copy-buttons` extraction above. Requested live —
  "remove the title rewrite from dracula-layout and make it a standalone
  plugin." Clean extraction: title cleanup only ever operated on
  already-rendered native title text (`.card-section-title .TruncatedText
  :not(.scene-card__description)` on cards, `.scene-header-container
  .scene-header .TruncatedText` on the detail page) — it never depended
  on this plugin's own batched GraphQL scene data or any other
  dracula-layout state, so nothing else needed to change at the call
  sites beyond deleting them. One incidental fix landed with the move: the
  scenes-grid card title write (`applySceneDataToCard()`, inside
  `enhanceCard()`'s per-card GraphQL callback) had been unconditional —
  `titleEl.textContent = cleanTitleText(...)` with no equality check,
  unlike `cleanDetailPageTitle()` right next to it, which already
  guarded. Not a self-triggering loop (each card is marked
  `stashClean` and this only ran once per card per data arrival), but
  every write was still a childList mutation waking every body-wide
  observer on the page for no reason once the title was already clean.
  The new plugin's own version of both functions (`cleanCardTitles()`,
  `cleanDetailPageTitle()`) is equality-guarded in both places, matching
  the one that was already correct rather than porting the gap forward.
  clean-cards.js bumped 5.4 → 5.5 for the removal; this plugin's own
  manifest version bumped 1.1.0 → 1.2.0. If a title looks unclean and
  title-scrubber isn't installed, that's expected — this plugin no longer
  touches title text at all; check `title-scrubber.js`, not here.
- **Custom Fields moved to the very bottom of the Browse body**, after
  Galleries, got real `.jl-card` chrome, and its native
  `.collapse-header`/"Custom Fields" label is hidden — our jl-head is the
  only visible toggle now. Its native `.collapse` defaults to *closed*
  though (`class="collapse"`, no `.show`), so scene-dashboard.js clicks the
  native toggle once, programmatically, the first time it finds it still
  closed — hiding the only control without first opening it would have
  permanently stranded the content. `.click()` is safe here for the same
  reason it's safe on the mode bar: it fires the real event React's own
  `onClick` listens for, not a DOM mutation.
- **The mockup's original two-column masonry split is still not
  implemented.** It put synopsis, performers and tags in column one with
  metadata, file and history in column two, simultaneously visible
  side-by-side. That's a different problem from the reading-order reorder
  that shipped (single column, `display: contents` flattening) — masonry
  needs two independent layout tracks, which for content this deep would mean
  either moving nodes (violates constraint 1) or duplicating the flattening
  trick per column, which was judged not worth the complexity for a 450px-wide
  pane. Revisit only if someone actually wants side-by-side columns again.
- **Two-column grid dashboard, abandoned.** An earlier version of this
  plugin put File/History/Groups/Galleries in a `grid-template-columns:
  minmax(0,1fr) minmax(0,1fr)` grid, gated by a `ResizeObserver`-driven
  narrow/wide breakpoint at 900px. It never actually showed two columns in
  real use: stash pins `.scene-tabs` to exactly `flex: 0 0 450px` on desktop
  with no way to widen it, so the pane's one stable width never crossed 900px
  regardless of window size. Replaced by the reading-order column described
  in Layout above.
- **Not built on `PluginApi.patch`.** `ScenePage`, `ScenePage.Tabs`,
  `ScenePage.TabContent` and `SceneFileInfoPanel` are `PatchComponent`s, but
  `SceneDetailPanel`, `SceneHistoryPanel` and `QueueViewer` are plain FCs and
  cannot be patched by name. The sanctioned API does not cover what this needs.
- **Filters stays its own mode, never a card.** `SceneVideoFilterPanel` paints
  to a canvas and misbehaves when revealed from a hidden state.
- **Edit stays its own mode.** It is the one pane with `mountOnEnter`, so it is
  genuinely absent until selected.
- **Only the identity header is persistent; everything else is Browse-only
  again, matching stash's original tab behavior.** Studio Logo/Code, Title,
  Original Title and Toolbar stay visible in every mode. Metadata, Details,
  Tags, Performers, Custom Fields, File, History, Groups and Galleries are
  all Browse-only, replaced by the Markers/Filters/Edit panel when another
  mode is selected. An earlier version of this plugin made Metadata/
  Details/Tags persistent too (visible in every mode, not just Browse) —
  deliberately reverted, so don't quietly reintroduce it. The visibility
  split is a `[data-jl-mode="browse"]` selector distinction, not a DOM-order
  one: Original Title is exempt from the Browse-only rule only because it's
  tagged solely by `.stash-original-title`, never `[data-jl-item^=
  "scenedetails"]` — see tagPanes() in scene-dashboard.js. The mode switcher
  (`order: 55`) sits within the persistent header block, right after
  Toolbar — the same slot stash's own `.nav-tabs` occupied — which is
  positional and independent of the visibility split above.
- **Toolbar sits below Original Title, order: 50**, not immediately after
  Title as in the original mockup ordering. Toolbar (rating, O-counter,
  organized flag) is the highest-frequency interaction on the page, so it's
  kept close to the top of the header rather than pushed further down.
  (Subheader — date/fps/resolution — is native and visible again, its own
  strip between Original Title and Toolbar; see the reverted-mirroring note
  in the Layout section above. This parenthetical previously described the
  mirrored-into-Metadata version, which was reverted.)
- **Performer page: aliases brightened, not muted, on explicit request.**
  A performer-page review initially recommended dimming `.alias-head` to
  match Original Title's muted treatment on the scene page — rejected: "for
  the aliases line, let's actually increase the visibility a bit - this is
  very useful info" (searchable data, not filler). `.alias-head` is set to
  `var(--jl-muted)` (originally in `performer-dashboard.css`, now
  `entity-dashboard.css`), `!important` because the
  native color comes from a *different*, separately-installed `dracula-theme`
  plugin (`.detail-header .alias-head { color: var(--smoke_white) }`,
  `--smoke_white: #4f4f4f` — confirmed via `CSS.getMatchedStylesForNode`,
  not this plugin's own CSS) whose load order relative to this plugin isn't
  under this plugin's control. Same reasoning applies to `.performer-name`
  (set to `var(--jl-lilac)`, matching scene Title's color exactly) and to
  `.detail-item-title`/`.detail-item-value`'s monospace override — all three
  fight the same third-party plugin, all three need `!important`.
- **Performer page's detail-item monospace treatment copies scene
  Metadata's `<h6>` row styling exactly** (source-code-pro stack, 12px/800
  muted label, 13px/400 bright value) rather than inventing a new scale —
  see `[data-jl-item^="scenedetails"]` in scene-dashboard.css for the
  original. Performer's `.detail-item-title`/`.detail-item-value` are
  already real, separate elements, unlike Metadata's `::before`-rendered
  labels (needed there only because Director's text node isn't safe to
  restructure) — so this is a direct style with no JS text-node work.
- **`.detail-group` boxing as a collapsible `.jl-card`, tried and reverted.**
  An earlier version of `performer-dashboard.*` gave `.detail-group` the
  same dark-well/bordered/collapsible-head treatment as scene's Metadata
  card (`.jl-card` class + `ensureHead()`, identical function copy-pasted
  from `scene-dashboard.js`). Explicit feedback after seeing it live: "it
  feels forced." Reverted outright, not just made non-collapsible — the
  whole boxed-card idea, not only the collapse affordance, was the problem.
  `.detail-group` is back to native flex-row/wrap layout and background;
  only the label:value monospace font (above) survives from that pass. If
  a boxed treatment is ever revisited here, this is not a blank slate —
  it was already tried once and rejected on this exact page.
- **The mode bar's pill row gets `margin-top: 15px`, not just the original
  `margin-bottom: 14px`, to sit visually centered between the performer
  header block above and the filtered-list toolbar below.** The two
  neighboring gaps aren't equal by default: CDP-measured (not eyeballed),
  the header block (photo/name/detail-group/edit-buttons) sits only 21px
  above the pill row natively, while 36px separates the pill row from the
  scene-grid's own `.filtered-list-toolbar` below (14px from this row's
  own margin-bottom plus the toolbar's own native top spacing). Adding
  15px of margin-top brings the top gap to 21+15=36px, matching the
  bottom exactly — confirmed live post-change (both gaps measured 36px).
  Chosen as margin-top on the row itself rather than adjusting the bottom
  margin down to meet a smaller top, since 36px already matches File/
  History's established card-gap rhythm elsewhere in this plugin and
  didn't need to shrink.

## Upstream reference

Everything above was checked against `stashapp/stash` @ `develop`,
`ui/v2.5/src/`:

- `components/Scenes/SceneDetails/Scene.tsx` — page shell, tab list, pane list,
  Mousetrap bindings, which panes get `mountOnEnter`
- `components/Scenes/SceneDetails/SceneDetailPanel.tsx` — the two-row structure
  the Details-pane flattening rules in `scene-dashboard.css` reach into
- `components/Scenes/SceneDetails/{SceneFileInfoPanel,SceneHistoryPanel,QueueViewer,SceneMarkersPanel,SceneGroupPanel,SceneGalleriesPanel,SceneVideoFilterPanel}.tsx` — the class names `SIGNATURES` matches on
- `patch.tsx`, `pluginApi.tsx` — what is and is not patchable
- `docs/en/Manual/Plugins.md` — manifest format

Re-fetch with:

```bash
curl -s https://raw.githubusercontent.com/stashapp/stash/develop/ui/v2.5/src/components/Scenes/SceneDetails/Scene.tsx
```

If a pane stops being tagged after a stash upgrade, diff the relevant panel
component's root `className` against `SIGNATURES` first — that is the most
likely breakage by a wide margin.
