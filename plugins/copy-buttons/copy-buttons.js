// ==StashScript==
// name Copy Buttons
// version 1.0.1
// description Click-to-copy buttons for performer names, performer
//             disambiguations, and studio codes.
// ==/StashScript==
;(() => {
  'use strict';

  console.log('[CopyButtons] v1.0.1 loaded');

  /* ============================
   *  CLIPBOARD
   *  navigator.clipboard exists only in a secure context — https, or
   *  localhost/127.0.0.1. Stash is routinely reached over plain http via a
   *  LAN IP, where it is simply undefined (reported live 2026-09-04: "only
   *  works with https hosts"). The fallback is the classic hidden-textarea
   *  + document.execCommand('copy') — deprecated, still shipped by every
   *  current browser, and the only thing that works in an insecure
   *  context. Three details make it actually work everywhere:
   *    - iOS Safari ignores textarea.select(); it needs an explicit
   *      setSelectionRange over the whole value, and the element must be
   *      readonly so the keyboard doesn't pop.
   *    - execCommand returns false instead of throwing when the copy is
   *      refused (no user activation, unfocused document), so the return
   *      value is checked and turned into a rejection — the button then
   *      shows a failure instead of a false green check.
   *    - Focus moves to the textarea for the copy; it is handed back to
   *      whatever had it, so keyboard users don't lose their place.
   *  A rejected navigator.clipboard.writeText (permission denied, page
   *  not focused) falls through to the same fallback rather than failing.
   * ============================ */
  async function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
      try {
        await navigator.clipboard.writeText(text);
        return;
      } catch (e) { /* fall through to execCommand */ }
    }
    const prevFocus = document.activeElement;
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.setAttribute('aria-hidden', 'true');
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;padding:0;border:0;opacity:0;pointer-events:none;';
    document.body.appendChild(ta);
    let ok = false;
    try {
      ta.focus({ preventScroll: true });
      ta.select();
      ta.setSelectionRange(0, text.length);
      ok = document.execCommand('copy');
    } finally {
      document.body.removeChild(ta);
      if (prevFocus && typeof prevFocus.focus === 'function') {
        try { prevFocus.focus({ preventScroll: true }); } catch (e) { /* detached */ }
      }
    }
    if (!ok) throw new Error('copy command was refused by the browser');
  }

  function makeCopyButton(className, label, getText) {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = `stash-copy-btn ${className}`;
    btn.title = label;
    btn.setAttribute('aria-label', label);
    const icon = document.createElement('i');
    icon.className = 'fa-solid fa-copy';
    btn.appendChild(icon);

    btn.addEventListener('click', e => {
      e.stopPropagation();
      e.preventDefault();
      const value = getText();
      if (!value) return;
      const flash = (cls, iconCls) => {
        btn.classList.remove('copied', 'copy-failed');
        btn.classList.add(cls);
        icon.className = `fa-solid ${iconCls}`;
        clearTimeout(btn._copiedTimeout);
        btn._copiedTimeout = setTimeout(() => {
          btn.classList.remove('copied', 'copy-failed');
          icon.className = 'fa-solid fa-copy';
        }, 1200);
      };
      copyText(value).then(() => flash('copied', 'fa-check')).catch(err => {
        console.error('[CopyButtons] copy failed', err);
        flash('copy-failed', 'fa-xmark');
      });
    });

    return btn;
  }

  /* ============================
   *  PERFORMER NAME / DISAMBIGUATION
   *  Works on performer-cards (grid) and the performer detail page header
   *  (.performer-head), both of which render the same `.performer-name` /
   *  `.performer-disambiguation` markup — pure native structure, no
   *  dependency on any other plugin.
   *
   *  Idempotent: safe to call repeatedly (e.g. from a MutationObserver)
   *  since it only inserts a button if one isn't already sitting next to
   *  the target element. This also lets it recover if React ever
   *  re-renders the name/disambiguation spans and wipes out a
   *  previously-injected button.
   * ============================ */
  function addPerformerCopyButtons(scope) {
    if (!scope) return;

    const nameEl = scope.querySelector('.performer-name');
    if (nameEl && !nameEl.nextElementSibling?.classList.contains('performer-name-copy')) {
      const btn = makeCopyButton('performer-name-copy', 'Copy performer name', () =>
        nameEl.textContent.trim()
      );
      nameEl.after(btn);
    }

    const disambigEl = scope.querySelector('.performer-disambiguation');
    if (disambigEl && !disambigEl.nextElementSibling?.classList.contains('performer-disambiguation-copy')) {
      const btn = makeCopyButton('performer-disambiguation-copy', 'Copy disambiguation', () =>
        disambigEl.textContent.trim().replace(/^\(\s*/, '').replace(/\s*\)$/, '')
      );
      disambigEl.after(btn);
    }
  }

  function enhancePerformerCard(card) {
    addPerformerCopyButtons(card);
  }

  function enhancePerformerDetailHeader() {
    addPerformerCopyButtons(document.querySelector('.performer-head'));
  }

  /* ============================
   *  STUDIO CODE (scene detail page)
   *  Two possible positions, checked in this order:
   *
   *  1. `.studio-code-text` — dracula-layout (or any plugin using the same
   *     contract) relocates the native "Studio Code:" field out of the
   *     Details list into its own header element, `.studio-code` >
   *     `.studio-code-text`. When present, the button is appended inside
   *     that same `.studio-code` element, after the text — this plugin
   *     only ever looks for that element and no-ops if it isn't there;
   *     nothing here depends on that other plugin being installed, and
   *     nothing there depends on this plugin either.
   *  2. The native "Studio Code: XXX" line inside `.scene-details h6` —
   *     used when nothing has relocated it. A real React-owned node
   *     (constraint: append only, never restructure its existing
   *     children) — the button is appended as a new child at the end of
   *     the h6, safe because appendChild never touches what's already
   *     there.
   *
   *  Both checks run on every pass (idempotent: each only touches its own
   *  case's element and only when it doesn't already have a copy button),
   *  since which one applies can change — the relocating plugin removes
   *  and rebuilds `.studio-code` on every scene, so a copy button
   *  attached to a previous instance is gone along with it and needs
   *  re-attaching each time.
   * ============================ */
  function addStudioCodeCopyButton() {
    const relocated = document.querySelector('.studio-code-text');
    if (relocated) {
      if (!relocated.nextElementSibling?.classList.contains('studio-code-copy')) {
        const btn = makeCopyButton('studio-code-copy', 'Copy studio code', () =>
          relocated.textContent.trim()
        );
        relocated.after(btn);
      }
      return;
    }

    const detailHeadings = document.querySelectorAll('.scene-details h6');
    for (const h6 of detailHeadings) {
      const text = h6.textContent.trim();
      if (!text.startsWith('Studio Code:')) continue;
      if (h6.querySelector('.studio-code-copy')) return;
      const btn = makeCopyButton('studio-code-copy', 'Copy studio code', () => {
        const raw = h6.textContent.trim();
        return raw.startsWith('Studio Code:') ? raw.slice('Studio Code:'.length).trim() : raw;
      });
      h6.appendChild(btn);
      return;
    }
  }

  /* ============================
   *  MUTATION OBSERVER
   *  No per-mutation "what changed" filtering — every function above is
   *  already idempotent and cheap (a handful of querySelector calls), so
   *  a body-wide observer just re-runs all three on a debounced pass,
   *  same pattern as collection-colors.js's own main observer.
   * ============================ */
  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      try {
        document.querySelectorAll('.performer-card').forEach(enhancePerformerCard);
        enhancePerformerDetailHeader();
        addStudioCodeCopyButton();
      } catch (e) {
        console.error('[CopyButtons]', e);
      }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
  document.querySelectorAll('.performer-card').forEach(enhancePerformerCard);
  enhancePerformerDetailHeader();
  addStudioCodeCopyButton();
})();
