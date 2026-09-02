// ==StashScript==
// name Title Scrubber
// version 1.0.0
// description Strips quality/release-group clutter (4K, censorship/leak/
//             subtitle labels, bracketed suffixes, redundant dash-joined
//             prefixes) from scene titles on the scenes grid and the
//             scene detail page.
// ==/StashScript==
;(() => {
  'use strict';

  console.log('[TitleScrubber] v1.0.0 loaded');

  /* ============================
   *  TITLE FILTER WORDS
   *  Terms to strip from scene titles. Matched case-insensitively.
   *  Bracketed variants like [4K] are handled automatically — just list
   *  the word without brackets.
   * ============================ */
  const TITLE_FILTER_WORDS = [
    '4k', 'HD', 'FHD', 'javplayer', 'decensored', 'uncensored',
    'censored', 'leaked', 'subtitled', 'Lada',
  ];

  // Build a single regex from the filter list.
  // Matches: [word], (word), or bare word — surrounded by whitespace or string boundaries.
  const TITLE_FILTER_RX = TITLE_FILTER_WORDS.length
    ? new RegExp(
        '(?:\\s*[\\[(](?:' + TITLE_FILTER_WORDS.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')[\\])]|(?:^|\\s)(?:' + TITLE_FILTER_WORDS.map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|') + ')(?=\\s|$))',
        'gi'
      )
    : null;

  const RX_PREFIX   = /^.+?\s+-\s+/;
  const RX_BRACKETS = /\s*\[[^\]]+\]\s*$/;
  const RX_DASHES   = /\s+-+\s+/g;

  function cleanTitleText(raw) {
    let out = (raw || '').trim();
    out = out.replace(RX_PREFIX, '');
    out = out.replace(RX_BRACKETS, '').trim();
    out = out.replace(RX_DASHES, ' ').trim();
    if (TITLE_FILTER_RX) out = out.replace(TITLE_FILTER_RX, ' ').trim();
    out = out.replace(/\s{2,}/g, ' ').trim();
    return out;
  }

  /* ============================
   *  TARGETS
   *  Both write via .textContent, guarded by an equality check — a plain
   *  unconditional write is a childList mutation the observer below also
   *  watches for, so writing the same cleaned value back on every pass
   *  would self-trigger it forever. Idempotent for the same reason
   *  copy-buttons.js's own functions are: safe to call repeatedly from a
   *  MutationObserver, and each one no-ops once the title is already
   *  clean.
   * ============================ */

  // Scenes grid: every card's own title, wherever `.card-section-title`
  // renders one — scene cards only (the structure copy-buttons.js and
  // other stash card types use for their own title/name markup differs,
  // so this selector doesn't reach them).
  function cleanCardTitles() {
    document.querySelectorAll('.card-section-title .TruncatedText:not(.scene-card__description)').forEach(titleEl => {
      const cleaned = cleanTitleText(titleEl.textContent);
      if (titleEl.textContent !== cleaned) titleEl.textContent = cleaned;
    });
  }

  // Scene detail page's own header title.
  function cleanDetailPageTitle() {
    const titleEl = document.querySelector('.scene-header-container .scene-header .TruncatedText');
    if (!titleEl) return;
    const cleaned = cleanTitleText(titleEl.textContent);
    if (titleEl.textContent !== cleaned) titleEl.textContent = cleaned;
  }

  /* ============================
   *  MUTATION OBSERVER
   *  One body-wide observer, rAF-debounced, re-running both idempotent
   *  functions on every batch — same pattern as copy-buttons.js.
   * ============================ */
  let scheduled = false;
  const observer = new MutationObserver(() => {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      try {
        cleanCardTitles();
        cleanDetailPageTitle();
      } catch (e) {
        console.error('[TitleScrubber]', e);
      }
    });
  });

  observer.observe(document.body, { childList: true, subtree: true });
  cleanCardTitles();
  cleanDetailPageTitle();
})();
