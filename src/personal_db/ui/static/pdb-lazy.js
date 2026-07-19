// Fragment loader: fills in the `data-viz-src` placeholders the dashboard/
// tracker/viz pages leave in place of a synchronous render (see http.py's
// default, non-?full=1 GET /, /t/<tracker>, /v/<slug> handlers, and the
// viz_pending macro in _macros.html that emits the placeholder markup).
//
// Runs on first load (DOMContentLoaded) and again on every pdb-nav swap
// (the 'pdb:navigate' event pdb-nav.js's applySwap dispatches) so viz
// blocks on a freshly-swapped page load the same way. Every unfilled
// placeholder is fetched concurrently -- fast trackers fill in immediately,
// a slow one doesn't hold up the rest. The block's *header* (title link,
// slug tooltip, sync button) was already server-rendered by the page; this
// only replaces the placeholder body in place with the fetched fragment.
//
// Loop safety: each successful/failed fetch marks its placeholder
// `data-loaded`, then dispatches 'pdb:navigate' itself (batched, see
// scheduleDispatch) so pdb-grid.js/pdb-chart.js/pdb-app-state.js re-scan and
// mount whatever just landed. That dispatch re-enters this module's own
// 'pdb:navigate' listener, but querySelectorAll('[data-viz-src]:not([data-loaded])')
// is a no-op by then -- every placeholder that dispatch fired for is already
// marked loaded, and any still-in-flight fetch is separately guarded by
// `data-loading` (loadOne bails out early rather than double-fetching).
(function () {
  "use strict";

  var dispatchScheduled = false;

  // Batches re-dispatch into one 'pdb:navigate' per animation frame rather
  // than once per fragment -- a burst of N concurrent fragments landing
  // close together (the common case) triggers pdb-grid/pdb-chart's re-scan
  // once, not N times.
  function scheduleDispatch() {
    if (dispatchScheduled) return;
    dispatchScheduled = true;
    window.requestAnimationFrame(function () {
      dispatchScheduled = false;
      document.dispatchEvent(new CustomEvent("pdb:navigate"));
    });
  }

  function showError(el) {
    el.innerHTML = "";
    var p = document.createElement("p");
    p.className = "meta viz-fragment-error";
    p.appendChild(document.createTextNode("Couldn't load this — "));
    var retry = document.createElement("a");
    retry.href = "#";
    retry.textContent = "retry";
    retry.addEventListener("click", function (event) {
      event.preventDefault();
      el.removeAttribute("data-loaded");
      loadOne(el);
    });
    p.appendChild(retry);
    el.appendChild(p);
  }

  function loadOne(el) {
    if (!el || el.hasAttribute("data-loaded") || el.hasAttribute("data-loading")) return;
    var src = el.getAttribute("data-viz-src");
    if (!src) return;
    el.setAttribute("data-loading", "1");
    fetch(src, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("pdb-lazy: fragment fetch failed (" + response.status + ")");
        return response.text();
      })
      .then(function (html) {
        el.removeAttribute("data-loading");
        el.innerHTML = html;
        el.setAttribute("data-loaded", "1");
        scheduleDispatch();
      })
      .catch(function () {
        el.removeAttribute("data-loading");
        // Terminal for automatic rescans (marked data-loaded so the next
        // pdb:navigate-triggered scan doesn't keep hammering a broken
        // endpoint) -- the retry link clears the attribute and re-fetches.
        el.setAttribute("data-loaded", "1");
        showError(el);
      });
  }

  function scanAndLoad() {
    document.querySelectorAll("[data-viz-src]:not([data-loaded])").forEach(loadOne);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", scanAndLoad);
  } else {
    scanAndLoad();
  }
  document.addEventListener("pdb:navigate", scanAndLoad);
})();
