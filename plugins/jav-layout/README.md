# JAV Layout — stash UI plugin

Dracula card styling plus the **Option C dashboard**: stash's native
nine-tab bar (scene pages) and tabbed relations list (performer/studio/
group/tag pages) replaced by a compact mode/pill switcher in the same
slot. On the scene page, a persistent identity header (Studio Logo/Code,
Title, Original Title, Toolbar) stays visible in every mode; everything
else swaps in below the switcher per mode, same as stash's own tabs. A
site-wide button restyle and a self-hosted Quicksand font round out the
look everywhere else in the app.

Formerly published as "Dracula Layout" — renamed 2026-09-02, same code,
same author. See `CLAUDE.md`'s own renaming note for what changed
internally (every `dl-`/`data-dl-`/`--dl-` reference became `jl-`/
`data-jl-`/`--jl-`).

## Files

| File | What it is |
| --- | --- |
| `jav-layout.yml` | Plugin manifest. The filename sets the plugin ID. |
| `fonts.css` | Self-hosted Quicksand SemiBold (600), embedded as base64 — no external font request. |
| `base-theme.css` | The whole-app palette — dracula-for-stash's stylesheet, vendored (MIT), every color as a CSS variable. Replaces the separately-installed `dracula-theme` plugin. |
| `themes.css` / `theme.js` | Every color the plugin paints, as `--jl-*` tokens, plus each selectable theme's overrides of both those tokens and `base-theme.css`'s variables. `theme.js` applies the **Color theme** setting and adds its dropdown to Settings › Plugins. See Themes below. |
| `clean-cards.css` / `.js` | Scene card restyle (pink code, cyan performers, dynamic title scaling), studio-code relocation, popover reordering, watched badge, performer hover popup. |
| `buttons.css` | Site-wide native-button restyle — every `.btn-primary`/`.btn-secondary`/`.btn-danger` in the app, not just this plugin's own UI. |
| `scene-dashboard.css` / `.js` | The scene page's own Option C dashboard. |
| `entity-dashboard.css` / `.js` | The same mode-switcher treatment for performer/studio/group/tag pages — one config-driven module for all four. |

Load order is set in the manifest: `fonts` → `base-theme` → `themes` →
`clean-cards` → `buttons` → `scene-dashboard` → `entity-dashboard`, with
`theme.js` first among the scripts.

Two related features used to live in this plugin and are now separate,
standalone plugins in this same repo — install them too if you want them:
**[copy-buttons](../copy-buttons)** (click-to-copy for performer names/
disambiguations/studio codes) and **[title-scrubber](../title-scrubber)**
(strips quality/release-group clutter from scene titles).

## Install

Add this repo as a plugin source — **Settings → Plugins → Sources**:

```
https://org0ne.github.io/stash-plugins/stable/index.yml
```

JAV Layout will show up in the list, ready to install with one click.

Or install manually — stash reads plugins from the `plugins` directory
next to its `config.yml` (`$HOME/.stash/plugins` for a native install, or
wherever you have that mounted in Docker):

```bash
cp -r jav-layout /path/to/stash/config/plugins/
```

For development, symlink instead so edits land immediately:

```bash
ln -s /path/to/jav-layout /path/to/stash/config/plugins/jav-layout
```

Then in stash: **Settings → Plugins → Reload Plugins**, and refresh the
browser. CSS and JS changes need only a browser refresh after that;
adding or renaming a file needs another Reload Plugins.

If you had the community **dracula-theme** plugin installed, disable it:
JAV Layout ships its own copy of that stylesheet (`base-theme.css`) and
themes it, so the external one is redundant.

If you're migrating from the old "Dracula Layout" plugin, remove that
one first (different plugin ID, so stash treats them as unrelated) — and
if you were previously running the pre-plugin `customJavaScript.js`
directly, disable **Settings → Interface → Custom JavaScript** (the
checkbox, not just clearing the textbox), or everything runs twice.

## How the scene dashboard works

`react-bootstrap` 1.6 wraps every `Tab.Pane` in `<Fade>` without
`unmountOnExit`, so every panel except `scene-edit-panel` is already
mounted and merely hidden. The plugin therefore **never moves a DOM
node**. It only:

1. tags each pane with `data-jl-pane` by fingerprinting the markup its
   panel component emits (`.scene-details`, `.play-history`,
   `.scene-file-info`, …), because react-bootstrap strips `eventKey`
   before render and stash's `Tab.Container` has no `id`, so panes carry
   no readable key;
2. inserts a mode bar and hides `.nav-tabs`;
3. appends a header button to File/History/Groups/Galleries, and tags
   the Details pane's un-classed headings (Original Title, Tags,
   Performer, …) so CSS can address them individually.

Everything else is CSS. Reordering across disconnected parts of the page
(the header, deep inside the Details pane, the mode bar) uses
`display: contents` on every wrapper in between — it un-boxes a wrapper
without moving it, so its children can become orderable flex items of
the outer sidebar without a single DOM node ever changing parents.

Everything is gated on `[data-jl-mode]`, which is set only after the
mode bar is built. **If the script fails, stash renders exactly as it
does today** — the tab bar reappears and no layout rules apply.

Mode buttons click stash's own nav links rather than touching React
state, so `Tab.Container` stays the single source of truth for which
pane is active. The performer/studio/group/tag pages
(`entity-dashboard.js`) work the same way, driving stash's own
`data-rb-event-key` nav links rather than duplicating React state.

## Modes (scene page)

| Mode | Contains |
| --- | --- |
| Browse | Details + Tags + Performers + Metadata + Custom Fields + File + History + Groups + Galleries |
| Queue | Queue |
| Markers | Markers |
| Filters | Video filters |
| Edit | Edit |

The mode switcher sits right after the header (Studio/Title/Toolbar) —
the same spot stash's own tab bar occupied before this plugin hid it.
Only the header above it (Studio Logo/Code, Title, Original Title,
Toolbar) stays visible in every mode; everything in the table above is
Browse-only and swaps out for the selected mode's panel, same as stash's
own tabs always worked. This is a `[data-jl-mode="browse"]` CSS
distinction, not a DOM-order one — moving the switcher around wouldn't
by itself change what's persistent.

Stash's own shortcuts still work. `a`, `i` and `h` all land inside
Browse, so the bar stays on Browse; `q`, `k` and `e` switch modes.

## Performer / studio / group / tag pages

Each of these pages' own native tabbed relations list (Scenes/Galleries/
Images/…) gets the same pill-switcher treatment, replacing stash's
default tab strip — one shared, config-driven engine
(`entity-dashboard.js`) rather than four near-identical files. The
detail-list above it (Gender/Age/Aliases/…/Stash IDs on a performer,
URLs/Parent Studios on a studio, etc.) gets a lower-visual-hierarchy
pass: indented, tightened to match the scene sidebar's own File-tab
rhythm, and — on a performer's Stash ID row specifically — a long UUID
value truncates with an ellipsis instead of forcing its own row's label
to wrap.

## Action rows and control rows

Stash places its page-level actions (Edit / Auto tag / Merge / Delete,
Save / Cancel) somewhere different on every page type. This plugin
gives them one rule:

- **Viewing**: the actions sit in a pill directly under the identity
  block, page-centered, styled like the mode switcher, 14px above it.
- **Editing**: the actions become a bar pinned to the bottom of whatever
  scrolls (the page on performer/studio/group/tag, the sidebar on
  scene/gallery/image), so Save is always in reach on a long form.

The filtered-list toolbar above every list gets the same pill treatment
as the mode switcher: same fill and outline, same 36px height, ghost
controls with the accent as the only "on" color, and one 14px rhythm
between the action pill, the mode switcher and the toolbar on the
entity pages.

## Layout (scene page)

```
+-----------------------------------+
|            Studio Logo            |
|  [CODE 📋]          [DATE]        |  <- Studio Code + release date,
|  Performer Name · Performer Name  |     one joined pill (below Logo)
|                                    |
|  Title                            |
|  Original Title                   |
|  Toolbar (rating, O-count, …)     |
+-----------------------------------+
|  [ Browse | Markers | Filters | … ]  <- mode switcher: the same slot
+-----------------------------------+     stash's own .nav-tabs occupied
|  DETAILS                        v |
|  (description prose)              |
+-----------------------------------+
|  TAGS                           v |
|  (chip cloud)                     |
+-----------------------------------+
|  Performers                       |
+-----------------------------------+
|  METADATA                       v |
|  Director / Created / Updated /   |
|  Resolution / Framerate           |
+-----------------------------------+
|  FILE                           v |
|  HISTORY                        v |
|  GROUPS                         v |
|  GALLERIES                      v |
|  CUSTOM FIELDS                  v |
+-----------------------------------+
```

Details / Tags / Metadata look like File/History-style cards but aren't
built the same way — their content is scattered sibling elements sharing
a parent with other content that must stay out of the card (Original
Title shares Metadata's parent; Performers/Custom Fields share Details/
Tags' parent). Every member gets the card's background/border
individually and is pulled flush against its neighbours; there's no
single wrapping element. See `CLAUDE.md`'s "Scattered-group cards"
section for the mechanics.

Subheader (date) is shown as-is, native and unmodified, sharing the
Studio Code line; fps/resolution are mirrored into Metadata instead.

Everything is placed with flexbox `order` on `.scene-tabs` itself
(already a `flex-direction: column` box in stash's own CSS); pieces from
deep inside the Details pane reach that level via `display: contents` on
every wrapper in between. A hidden card (empty Groups, a scene with no
galleries) just closes the gap — nothing pins a fixed slot.

There's no width-based column switch: stash pins the sidebar to exactly
`450px` on desktop with no way to widen it, so an earlier two-column
grid version of this dashboard never had room to show two columns in
practice. This is a single column at every width, with a handful of
rules (the sidebar performer grid, the mobile edit-toolbar centering)
that specifically adapt at narrow/mobile viewports.

## Themes

**Settings → Plugins → JAV Layout → Color theme** is a dropdown (the
plugin replaces stash's free-text field for this setting) with:

| Theme | Palette |
| --- | --- |
| Dracula (default) | The look this plugin was built on. |
| Catppuccin Mocha | pink / sky / lavender accents on Catppuccin's text ramp. |
| Rosé Pine Moon | love / foam / iris. Rosé Pine's accent is also its only red, so Save and Delete share a hue. |
| Kanagawa Wave | sakura pink / spring blue on paper-white text. |

The choice applies immediately, is saved in the plugin's settings, and is
cached in `localStorage` under `jl.theme` so later page loads paint in
the right theme before the settings round trip completes. A missing or
unknown value means Dracula.

A theme recolors the **whole app**: the base palette (nav bar, lists,
cards, forms, modals — everything stash itself draws) comes from
`base-theme.css`, a vendored copy of the community dracula-for-stash
stylesheet with every color as a CSS variable, and each theme redefines
those variables alongside the plugin's own `--jl-*` tokens. **If you
have the separate `dracula-theme` plugin installed, disable it** — this
plugin now contains it, and there is no reason to load it twice.

Adding a theme is three edits: two `html[data-jl-theme="…"]` blocks in
`themes.css` (the `--jl-*` tokens, and the base-app palette — each
`:root`-level list names the slots), and an entry in `THEMES` in
`theme.js`. Palette attributions live in `THIRD-PARTY-NOTICES.md`.

## Customising

- Card titles: `CARD_TITLES` at the top of `scene-dashboard.js`. They
  are hard-coded rather than read from stash's locale bundle, so change
  them if you run a non-English UI.
- Which panels appear under Browse: `BROWSE_PANES`, plus the matching
  `order` rules in `scene-dashboard.css`.
- Reading order: the `order` values in `scene-dashboard.css`'s "Reading
  order" block. Numbers are spaced by 10 to leave room for inserting
  items.
- Button colors/sizing: the tier-1 (color, always-on) and tier-2
  (padding/size, opt-in per container) rules in `buttons.css`.
- Colors anywhere: change the `--jl-*` tokens in `themes.css`, never a
  hex value in the other stylesheets — they no longer contain any.

Collapsed cards persist in `localStorage` under `jl.collapsedCards`.

## Known limitations

- **Pane fingerprinting is the fragile part.** If upstream renames
  `.scene-details`, `.play-history`, `.scene-file-info`,
  `.scene-galleries`, `.scene-video-filter`, `.queue-controls`,
  `.scene-markers-panel` or `.edit-buttons-container`, the matching card
  silently stops being tagged. `SIGNATURES` in `scene-dashboard.js` is
  the one place to fix that.
- `SceneGroupPanel` renders a bare `div.row.justify-content-center` with
  no distinguishing class, so Groups is identified last, by shape.
- Panes revealed in Browse keep react-bootstrap's `aria-hidden="true"`
  until the next sync corrects it, so a screen reader can briefly miss
  them.
- Filters stays its own mode on purpose: `SceneVideoFilterPanel` paints
  to a canvas and does not behave well when revealed from a hidden
  state.
- The Details pane's `<h6>` headings (Original Title aside) are matched
  by their leading text ("Details", "Tags", "Performer"), not a class —
  same hard-coded-English tradeoff as `CARD_TITLES`. Revisit if the UI
  language changes.

## Verified against

`stashapp/stash` @ `develop`, `ui/v2.5` — `Scene.tsx`,
`SceneDetailPanel.tsx`, `SceneFileInfoPanel.tsx`, `SceneHistoryPanel.tsx`,
`QueueViewer.tsx`, `patch.tsx`, `pluginApi.tsx`; `react-bootstrap` 1.6.6
`TabPane` and `AbstractNavItem`.

Layout, mode switching and persistent-context visibility across Browse/
Markers/Edit were verified against a live stash instance (headless
Chrome screenshots of real scene, performer, studio and tag pages).

## Thanks

The whole-app palette in `base-theme.css` is
[dracula-for-stash](https://github.com/UncertainMongoose/dracula-for-stash)
by **UncertainMongoose**, carried in here under its MIT license with a
handful of documented changes. Every stash page this plugin themes
stands on that stylesheet, and the Dracula look this plugin grew up on
was theirs first — thank you. Thanks as well to
[Zeno Rocha](https://github.com/zenorocha) and
[Lucas de França](https://github.com/luxonauta) for the
[Dracula theme](https://draculatheme.com) itself, and to the
Catppuccin, Rosé Pine and Kanagawa projects for the palettes behind the
other themes.

## License

MIT — see [LICENSE](LICENSE). Palette attributions for the selectable
themes are in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
