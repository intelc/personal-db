// No-full-reload navigation: intercept same-origin link clicks, fetch the
// target page, and swap its <main> into the live document instead of doing
// a full browser navigation. This keeps chrome that lives outside <main>
// (the agent-terminal drawer, in particular -- see pdb-agent-terminal.js)
// mounted and running across page changes.
//
// The server still renders full HTML documents for every route (see
// base.html) -- X-PDB-Nav is advisory only, nothing server-side changes.
// We just parse out the bits we need from the full response.
//
// Any failure here (network error, missing <main>, auth redirect, parse
// error) falls back to a normal browser navigation so this is purely
// progressive enhancement.
(function () {
  "use strict";

  function contentContainer() {
    return document.querySelector(".content");
  }

  function currentMain() {
    return document.querySelector(".content > main");
  }

  function isExcludedPath(pathname) {
    if (pathname === "/auth" || pathname.indexOf("/auth/") === 0) return true;
    if (pathname === "/api" || pathname.indexOf("/api/") === 0) return true;
    if (pathname === "/static" || pathname.indexOf("/static/") === 0) return true;
    return false;
  }

  function sameOrigin(url) {
    try {
      return url.origin === window.location.origin;
    } catch (_error) {
      return false;
    }
  }

  function findAnchor(node) {
    while (node && node !== document.body && node.nodeType === 1) {
      if (node.tagName === "A") return node;
      node = node.parentElement;
    }
    return null;
  }

  function isSamePageFragment(url) {
    return (
      url.hash !== "" &&
      url.pathname === window.location.pathname &&
      url.search === window.location.search
    );
  }

  function shouldIntercept(event, anchor) {
    if (event.defaultPrevented) return false;
    if (event.button !== 0) return false;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (!anchor) return false;
    if (anchor.hasAttribute("download") || anchor.hasAttribute("data-no-swap")) return false;
    var target = anchor.getAttribute("target");
    if (target && target !== "_self") return false;
    var href = anchor.getAttribute("href");
    if (!href) return false;
    var url;
    try {
      url = new URL(anchor.href, window.location.href);
    } catch (_error) {
      return false;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    if (!sameOrigin(url)) return false;
    if (isSamePageFragment(url)) return false;
    if (isExcludedPath(url.pathname)) return false;
    return true;
  }

  // Scripts inside a fetched HTML document never auto-execute when dropped
  // in via innerHTML. setup_tracker.html relies on an inline <script> block
  // (installHooks/runTrackerAction/Plaid+Monarch+crypto loaders) that both
  // defines globals and runs bootstrap calls immediately -- redeclaring
  // those functions and re-running the bootstrap calls is safe (the
  // bootstrap calls just re-query whatever is currently in the DOM), so we
  // recreate+execute any plain JS <script> found in the swapped-in <main>.
  // Data-only script blocks (type="application/json", used by pdb-grid.js /
  // pdb-chart.js) are left untouched -- they're inert and just need to be
  // present in the DOM for those mounters to read.
  var EXECUTABLE_SCRIPT_TYPES = ["", "text/javascript", "application/javascript", "module"];

  function executeScripts(root) {
    var scripts = root.querySelectorAll("script");
    scripts.forEach(function (old) {
      var type = (old.getAttribute("type") || "").toLowerCase();
      if (EXECUTABLE_SCRIPT_TYPES.indexOf(type) === -1) return;
      var next = document.createElement("script");
      for (var i = 0; i < old.attributes.length; i++) {
        var attr = old.attributes[i];
        next.setAttribute(attr.name, attr.value);
      }
      next.textContent = old.textContent;
      old.replaceWith(next);
    });
  }

  // Sidebar aria-current is computed server-side from the `active` template
  // var (see base.html). Rather than re-deriving that logic client-side,
  // copy each link's aria-current straight from the freshly-fetched
  // document -- that's always consistent with whatever the server decided.
  function updateSidebar(newDoc) {
    var liveLinks = document.querySelectorAll("#pdb-sidebar a[href]");
    var newLinks = newDoc.querySelectorAll("#pdb-sidebar a[href]");
    var byHref = new Map();
    newLinks.forEach(function (a) {
      byHref.set(a.getAttribute("href"), a);
    });
    liveLinks.forEach(function (a) {
      var match = byHref.get(a.getAttribute("href"));
      if (match && match.hasAttribute("aria-current")) {
        a.setAttribute("aria-current", match.getAttribute("aria-current"));
      } else {
        a.removeAttribute("aria-current");
      }
    });
  }

  // The topbar title (shown in the narrow/off-canvas-sidebar layout, see
  // style.css) lives in `.content` alongside <main>, not inside it -- swap
  // it too, or it'll go stale after navigating.
  function updateTopbarTitle(newDoc) {
    var live = document.querySelector(".topbar-title");
    var next = newDoc.querySelector(".topbar-title");
    if (live && next) live.textContent = next.textContent;
  }

  function applySwap(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var newMain = doc.querySelector(".content > main");
    var liveMain = currentMain();
    if (!newMain || !liveMain) throw new Error("pdb-nav: no <main> found to swap");
    liveMain.innerHTML = newMain.innerHTML;
    document.title = doc.title;
    updateSidebar(doc);
    updateTopbarTitle(doc);
    executeScripts(liveMain);
    document.dispatchEvent(new CustomEvent("pdb:navigate"));
  }

  async function fetchAndSwap(url) {
    var response = await fetch(url, {
      headers: { "X-PDB-Nav": "swap" },
      credentials: "same-origin",
    });
    if (!response.ok) {
      throw new Error("pdb-nav: fetch failed (" + response.status + ")");
    }
    var finalUrl = new URL(response.url, window.location.href);
    if (finalUrl.pathname === "/auth" || finalUrl.pathname.indexOf("/auth/") === 0) {
      throw new Error("pdb-nav: redirected to auth");
    }
    var html = await response.text();
    applySwap(html);
  }

  function saveScrollIntoCurrentEntry() {
    var container = contentContainer();
    var scrollTop = container ? container.scrollTop : 0;
    var state = window.history.state && typeof window.history.state === "object" ? window.history.state : {};
    window.history.replaceState(
      Object.assign({}, state, { scrollTop: scrollTop }),
      "",
      window.location.href
    );
  }

  async function navigateTo(url) {
    try {
      saveScrollIntoCurrentEntry();
      await fetchAndSwap(url);
      window.history.pushState({ scrollTop: 0 }, "", url);
      var container = contentContainer();
      if (container) container.scrollTop = 0;
    } catch (_error) {
      window.location.href = url;
    }
  }

  document.addEventListener("click", function (event) {
    var anchor = findAnchor(event.target);
    if (!shouldIntercept(event, anchor)) return;
    event.preventDefault();
    navigateTo(anchor.href);
  });

  window.addEventListener("popstate", function (event) {
    var state = event.state || {};
    fetchAndSwap(window.location.href)
      .then(function () {
        var container = contentContainer();
        if (container) container.scrollTop = state.scrollTop || 0;
      })
      .catch(function () {
        window.location.reload();
      });
  });

  // Give the initial entry a state object so the first Back press has a
  // scrollTop to restore, same as pushed entries.
  if (!window.history.state) {
    window.history.replaceState({ scrollTop: 0 }, "", window.location.href);
  }
})();
