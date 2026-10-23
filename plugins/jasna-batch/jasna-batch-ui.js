// jasna-batch-ui.js -- the browser half of the jasna-batch plugin: one link.
//
// The queue lives on stash-jasna-bridge, which serves it as a page at
// <bridge>/queue. Stash's plugin settings cannot hold a link, so this script
// puts one into the "Queue page" setting's description on Settings > Plugins.
// The address is the plugin's queuePageUrl setting when set, else the usual
// reverse-proxied bridge at <stash-origin>/jasna/queue (same auto-detect rule
// as jasna-switch).
(function () {
  "use strict";
  const PLUGIN_ID = "jasna-batch";
  const SETTING_HEADING = "Queue page";
  const LINK_CLASS = "jasna-batch-queue-link";
  let queueUrl = null;

  async function loadQueueUrl() {
    let configured = "";
    try {
      const resp = await fetch("/graphql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ query: "{ configuration { plugins } }" }),
      });
      const json = await resp.json();
      const mine = (json.data && json.data.configuration && json.data.configuration.plugins || {})[PLUGIN_ID] || {};
      if (typeof mine.queuePageUrl === "string") configured = mine.queuePageUrl.trim();
    } catch (err) {
      console.log("[Jasna Batch] could not read plugin settings: " + err.message);
    }
    if (configured) {
      queueUrl = configured.startsWith("/") ? window.location.origin + configured : configured;
    } else {
      queueUrl = window.location.origin + "/jasna/queue";
    }
  }

  function placeLink() {
    if (!queueUrl) return;
    if (!window.location.pathname.startsWith("/settings")) return;
    // Stash may render the panel more than once (one copy hidden per
    // breakpoint), so every "Queue page" row gets the link, once each.
    const headings = document.querySelectorAll(".plugin-settings .setting h3");
    for (const h3 of headings) {
      if ((h3.textContent || "").trim() !== SETTING_HEADING) continue;
      const setting = h3.closest(".setting");
      const sub = setting && setting.querySelector(".sub-heading");
      if (!sub || sub.querySelector("." + LINK_CLASS)) continue;
      const line = document.createElement("div");
      line.className = LINK_CLASS;
      line.style.marginTop = "6px";
      const a = document.createElement("a");
      a.href = queueUrl;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = "Open the Jasna queue";
      line.appendChild(a);
      line.appendChild(document.createTextNode("  (" + queueUrl + ")"));
      sub.appendChild(line);
    }
  }

  // The settings page is React-rendered and the plugin cards are collapsed by
  // default; watch the DOM so the link appears whenever the setting does.
  const observer = new MutationObserver(() => placeLink());
  loadQueueUrl().then(() => {
    placeLink();
    observer.observe(document.body, { childList: true, subtree: true });
  });
})();
