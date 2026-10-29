// jasna-batch-ui.js -- the browser half of the jasna-batch plugin: the links.
//
// The queue lives on stash-jasna-bridge, which serves it as a page at
// <bridge>/queue. Stash's plugin settings cannot hold a link, so this script
// puts links into the "Queue page" setting's description on Settings >
// Plugins: one page showing every node (the first node's page with
// "?nodes=name = /path; ..." listing them all, each with its Pause / Resume
// controls), plus one per node. A node's browser address is its entry in the
// queuePageUrl setting ("name = url; ..."), else the same-named node in the
// jasna-switch plugin's Bridge URL (those are browser routes already), else
// for the first node the usual reverse-proxied bridge at <stash-origin>/jasna
// (same auto-detect rule as jasna-switch), else its Bridge URL when that is
// not a loopback address (the plugin's bridgeUrl is server-side; 127.0.0.1
// means nothing here).
(function () {
  "use strict";
  const PLUGIN_ID = "jasna-batch";
  const SETTING_HEADING = "Queue page";
  const LINK_CLASS = "jasna-batch-queue-link";
  let links = [];   // [{name, url}] or [{name, missing: true}]; url is the node's bridge address (no /queue)
  let allUrl = "";  // the first node's queue page showing every node

  // "name = value; name2 = value2" or a single bare value (name "").
  // Same entry syntax as the plugin's Bridge URL setting; options after a
  // URL (speed=, only=) are dropped here.
  function parseEntries(text) {
    const out = [];
    for (const raw of String(text || "").split(/[;\n]/)) {
      const entry = raw.trim();
      if (!entry) continue;
      let name = "", rest = entry;
      const m = entry.match(/^([^=]+?)\s*=\s*(\S.*)$/);
      if (m && !/:\/\/|^\//.test(m[1].trim())) { name = m[1].trim(); rest = m[2].trim(); }
      const url = rest.split(/\s+/)[0].replace(/\/+$/, "");
      if (!/:\/\/|^\//.test(url)) continue;
      out.push({ name, url });
    }
    return out;
  }

  function absolute(url) {
    return url.startsWith("/") ? window.location.origin + url : url;
  }

  function isLoopback(url) {
    try { return /^(127\.|localhost$|\[?::1\]?$)/.test(new URL(absolute(url)).hostname); } catch (e) { return false; }
  }

  async function loadLinks() {
    let mine = {}, switchSettings = {};
    try {
      const resp = await fetch("/graphql", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ query: "{ configuration { plugins } }" }),
      });
      const json = await resp.json();
      const all = json.data && json.data.configuration && json.data.configuration.plugins || {};
      mine = all[PLUGIN_ID] || {};
      switchSettings = all["jasna-switch"] || {};
    } catch (err) {
      console.log("[Jasna Batch] could not read plugin settings: " + err.message);
    }
    const nodes = parseEntries(mine.bridgeUrl);
    if (!nodes.length) nodes.push({ name: "", url: "" });
    const pages = parseEntries(mine.queuePageUrl);
    const byName = {}, bySwitch = {};
    for (const p of pages) byName[(p.name || "").toLowerCase()] = p.url;
    for (const p of parseEntries(switchSettings.bridgeUrl)) bySwitch[(p.name || "").toLowerCase()] = p.url;
    links = nodes.map((node, i) => {
      const name = node.name || (nodes.length > 1 ? (() => { try { return new URL(absolute(node.url)).hostname; } catch (e) { return "node " + (i + 1); } })() : "");
      // An unnamed page entry belongs to the first node. A page entry may
      // be the bridge's address or its /queue page; either way, keep the
      // bridge address.
      const page = byName[name.toLowerCase()] || (i === 0 ? byName[""] : undefined) || bySwitch[name.toLowerCase()];
      if (page) return { name, url: absolute(page).replace(/\/queue$/, "") };
      if (i === 0) return { name, url: window.location.origin + "/jasna" };
      if (node.url && !isLoopback(node.url)) return { name, url: absolute(node.url) };
      return { name, missing: true };
    });
    const known = links.filter((l) => !l.missing);
    if (known.length > 1) {
      // Same-origin nodes by path (the page fetches them without CORS);
      // another origin by its absolute URL (its cors_origins must allow this one).
      const entry = (l) => l.name + " = " + (l.url.startsWith(window.location.origin) ? l.url.slice(window.location.origin.length) : l.url);
      allUrl = known[0].url + "/queue?nodes=" + encodeURIComponent(known.map(entry).join("; "));
    }
  }

  function placeLinks() {
    if (!links.length) return;
    if (!window.location.pathname.startsWith("/settings")) return;
    // Stash may render the panel more than once (one copy hidden per
    // breakpoint), so every "Queue page" row gets the links, once each.
    const headings = document.querySelectorAll(".plugin-settings .setting h3");
    for (const h3 of headings) {
      if ((h3.textContent || "").trim() !== SETTING_HEADING) continue;
      const setting = h3.closest(".setting");
      const sub = setting && setting.querySelector(".sub-heading");
      if (!sub || sub.querySelector("." + LINK_CLASS)) continue;
      const rows = allUrl ? [{ name: "all nodes", url: allUrl, all: true }] : [];
      for (const link of rows.concat(links)) {
        const line = document.createElement("div");
        line.className = LINK_CLASS;
        line.style.marginTop = "6px";
        const label = "Open the Jasna queue" + (link.name ? " · " + link.name : "");
        if (link.missing) {
          line.appendChild(document.createTextNode(label + ": no browser address known (its Bridge URL is a loopback address; add a \"" + link.name + " = url\" entry above)"));
        } else {
          const a = document.createElement("a");
          a.href = link.all ? link.url : link.url + "/queue";
          a.target = "_blank";
          a.rel = "noopener";
          a.textContent = label;
          line.appendChild(a);
          if (!link.all) line.appendChild(document.createTextNode("  (" + link.url + "/queue)"));
        }
        sub.appendChild(line);
      }
    }
  }

  // The settings page is React-rendered and the plugin cards are collapsed by
  // default; watch the DOM so the links appear whenever the setting does.
  const observer = new MutationObserver(() => placeLinks());
  loadLinks().then(() => {
    placeLinks();
    observer.observe(document.body, { childList: true, subtree: true });
  });
})();
