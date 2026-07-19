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
//
// Chrome (sidebar aria-current, topbar title, document title) updates
// *optimistically* the instant a navigation starts -- see optimisticActivate
// -- rather than waiting for the fetch, so a click feels instant even before
// any response has arrived. If the fetch is still pending after
// SKELETON_DELAY_MS, <main> gets a lightweight skeleton (buildSkeleton)
// until the real content lands; applySwap (unchanged) is the source of
// truth that corrects both once the response is in.
(function () {
  "use strict";

  // How long a navigation is allowed to sit "pending" before we replace
  // <main> with a skeleton (see buildSkeleton/runNavigation below). Chosen
  // to be imperceptible for the common fast-fetch case but short enough that
  // a slow dashboard render doesn't leave the page looking dead.
  var SKELETON_DELAY_MS = 200;

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

  // The sidebar footer (#sidebar-status) holds the Health status dot/label
  // and the Setup gear -- both server-rendered from the same `active` /
  // `nav_failing` vars as the rest of the sidebar. Rather than re-deriving
  // failing-tracker state client-side, just replace the whole footer's
  // markup with the freshly-fetched version, same approach as updateSidebar.
  function updateSidebarStatus(newDoc) {
    var live = document.getElementById("sidebar-status");
    var next = newDoc.getElementById("sidebar-status");
    if (live && next) live.innerHTML = next.innerHTML;
  }

  function applySwap(html) {
    var doc = new DOMParser().parseFromString(html, "text/html");
    var newMain = doc.querySelector(".content > main");
    var liveMain = currentMain();
    if (!newMain || !liveMain) throw new Error("pdb-nav: no <main> found to swap");
    liveMain.innerHTML = newMain.innerHTML;
    document.title = doc.title;
    updateSidebar(doc);
    updateSidebarStatus(doc);
    updateTopbarTitle(doc);
    executeScripts(liveMain);
    document.dispatchEvent(new CustomEvent("pdb:navigate"));
  }

  async function fetchHtml(url) {
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
    return response.text();
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

  // Best-effort label for a link: prefer an explicit accessible name
  // (aria-label/title -- the sidebar's Health status row and Setup gear only
  // have one of these, not link text that matches the page title) and fall
  // back to visible text otherwise.
  function anchorLabel(anchor) {
    if (!anchor) return "";
    var label = anchor.getAttribute("aria-label") || anchor.getAttribute("title");
    if (label) return label.trim();
    var text = anchor.textContent && anchor.textContent.trim();
    return text || "";
  }

  // Fires synchronously the instant a navigation starts (well before the
  // fetch resolves) so a sidebar click feels instant instead of dead: mark
  // the matching sidebar link(s) current and swap the topbar/document title
  // to a best-effort guess. This is deliberately approximate -- `updateSidebar`
  // / `updateTopbarTitle` (run from applySwap once the real response lands)
  // are the source of truth and silently correct any mismatch, e.g. a body
  // link with no sidebar counterpart just leaves the sidebar as it was until
  // then.
  function optimisticActivate(url, sourceAnchor) {
    var path = url.pathname + url.search;
    var sidebarLinks = document.querySelectorAll("#pdb-sidebar a[href]");
    var match = null;
    sidebarLinks.forEach(function (a) {
      if (a.getAttribute("href") === path) match = a;
    });
    sidebarLinks.forEach(function (a) {
      if (a === match) a.setAttribute("aria-current", "page");
      else a.removeAttribute("aria-current");
    });
    var label = anchorLabel(sourceAnchor) || anchorLabel(match);
    if (label) {
      var topbarTitle = document.querySelector(".topbar-title");
      if (topbarTitle) topbarTitle.textContent = label;
      document.title = label + " · personal_db";
    }
  }

  // Lightweight placeholder shown in <main> once a navigation has been
  // pending for SKELETON_DELAY_MS. Built in JS (styled in style.css's
  // .nav-skeleton rules) rather than shipped as server markup -- it's pure
  // client-side loading chrome, never part of a real response.
  function buildSkeleton() {
    var wrap = document.createElement("div");
    wrap.className = "nav-skeleton";
    wrap.setAttribute("role", "status");
    wrap.setAttribute("aria-label", "Loading");
    var spinner = document.createElement("div");
    spinner.className = "nav-skeleton-spinner";
    wrap.appendChild(spinner);
    for (var i = 0; i < 3; i++) {
      var card = document.createElement("div");
      card.className = "nav-skeleton-card";
      ["nav-skeleton-line-h", "", "nav-skeleton-line-short"].forEach(function (extra) {
        var line = document.createElement("div");
        line.className = extra ? "nav-skeleton-line " + extra : "nav-skeleton-line";
        card.appendChild(line);
      });
      wrap.appendChild(card);
    }
    return wrap;
  }

  // Shared by click-driven and popstate-driven navigation: optimistic chrome
  // update happens synchronously, then the fetch races the skeleton timer.
  // `onFailure` is the caller's fallback (full navigation vs. reload) --
  // this never rejects, it always resolves once the outcome (success or
  // fallback) has been handled.
  function runNavigation(targetUrl, sourceAnchor, onFailure) {
    optimisticActivate(targetUrl, sourceAnchor);
    var liveMain = currentMain();
    var timer = liveMain
      ? window.setTimeout(function () {
          liveMain.innerHTML = "";
          liveMain.appendChild(buildSkeleton());
        }, SKELETON_DELAY_MS)
      : null;
    return fetchHtml(targetUrl.href)
      .then(function (html) {
        if (timer) window.clearTimeout(timer);
        applySwap(html);
      })
      .catch(function (error) {
        if (timer) window.clearTimeout(timer);
        onFailure(error);
      });
  }

  function navigateTo(url, anchor) {
    var targetUrl;
    try {
      targetUrl = new URL(url, window.location.href);
    } catch (_error) {
      window.location.href = url;
      return;
    }
    saveScrollIntoCurrentEntry();
    // pushState happens immediately, before the fetch resolves -- if it
    // ultimately fails, onFailure below does a real navigation to the same
    // URL, which reloads regardless of the history entry already pointing
    // there.
    window.history.pushState({ scrollTop: 0 }, "", targetUrl.href);
    runNavigation(targetUrl, anchor, function () {
      window.location.href = targetUrl.href;
    }).then(function () {
      var container = contentContainer();
      if (container) container.scrollTop = 0;
    });
  }

  document.addEventListener("click", function (event) {
    var anchor = findAnchor(event.target);
    if (!shouldIntercept(event, anchor)) return;
    event.preventDefault();
    navigateTo(anchor.href, anchor);
  });

  window.addEventListener("popstate", function (event) {
    var state = event.state || {};
    var targetUrl = new URL(window.location.href);
    runNavigation(targetUrl, null, function () {
      window.location.reload();
    }).then(function () {
      var container = contentContainer();
      if (container) container.scrollTop = state.scrollTop || 0;
    });
  });

  // Give the initial entry a state object so the first Back press has a
  // scrollTop to restore, same as pushed entries.
  if (!window.history.state) {
    window.history.replaceState({ scrollTop: 0 }, "", window.location.href);
  }
})();
