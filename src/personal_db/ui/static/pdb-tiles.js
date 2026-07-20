// Tile gallery rotation: each tile on the "/" dashboard (dashboard_tiles.html)
// cycles through its tracker's headline metrics (see services/ui/tiles.py's
// metrics contract -- up to 4 of {label, value, detail, delta, good}).
//
// Hydration: tile metrics are embedded server-side as a JSON blob
// (#pdb-tiles-data, one entry per tile keyed by `slug`) so no extra fetch is
// needed on first paint -- the DOM tiles already show their first metric
// (server-rendered), this just wires up rotation over the rest.
//
// CRITICAL (explicit product requirement): tiles must NOT change in unison.
// Each tile gets a phase offset AND a slightly different period
// (BASE_PERIOD_MS + index * PERIOD_STEP_MS) so the gallery ripples rather
// than flashing all at once. The phase offset is spread across the full
// base period via modulo ((index * PHASE_STEP_MS) % BASE_PERIOD_MS) rather
// than a straight linear ramp -- with a large gallery (20+ tiles) a plain
// `index * PHASE_STEP_MS` delay would bunch every early tile's first tick
// into the first few seconds while the last tiles sit idle for the better
// part of a minute before their first rotation.
//
// Pauses on hover (per-tile flag) and whenever the tab is hidden (checked at
// tick time via document.hidden -- no per-tile visibilitychange listener,
// so nothing leaks across pdb-nav.js page swaps). Clicking a dot jumps
// straight to that metric. Under prefers-reduced-motion, auto-rotation
// never starts; the dots still work as a manual pager.
(function () {
  "use strict";

  var GALLERY_SELECTOR = "[data-tile-gallery]";
  var TILE_SELECTOR = "[data-tile]";
  var BASE_PERIOD_MS = 9000;
  var PERIOD_STEP_MS = 400;
  var PHASE_STEP_MS = 1500;
  var FADE_MS = 400;

  // Value-length breakpoints for shrinking long text-valued metrics (a
  // domain, contact name, event title, ...) so they fit inside the tile
  // instead of overflowing. Mirrored in the Jinja shell
  // (dashboard_tiles.html) for the server-rendered first metric, and in the
  // CSS classes of the same names (style.css) -- keep all three in sync.
  var VALUE_SM_THRESHOLD = 20;
  var VALUE_MD_THRESHOLD = 12;

  function sizeClassForValue(value) {
    var len = (value == null ? "" : String(value)).length;
    if (len > VALUE_SM_THRESHOLD) return "tile-value-sm";
    if (len > VALUE_MD_THRESHOLD) return "tile-value-md";
    return "";
  }

  var reduceMotion =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Every interval/timeout id this module has scheduled, so a re-hydrate
  // (pdb:navigate, after pdb-nav.js swaps in a fresh copy of this page)
  // tears down the previous page's timers instead of leaking them.
  var scheduled = [];

  function clearScheduled() {
    scheduled.forEach(function (id) {
      window.clearInterval(id);
      window.clearTimeout(id);
    });
    scheduled = [];
  }

  function readTilesData() {
    var el = document.getElementById("pdb-tiles-data");
    if (!el) return null;
    try {
      var parsed = JSON.parse(el.textContent || "[]");
      return Array.isArray(parsed) ? parsed : null;
    } catch (e) {
      return null;
    }
  }

  function deltaClass(good) {
    if (good === true) return "tile-delta-good";
    if (good === false) return "tile-delta-warn";
    return "tile-delta-neutral";
  }

  function renderMetric(tileEl, metric) {
    var valueEl = tileEl.querySelector("[data-tile-value]");
    var labelEl = tileEl.querySelector("[data-tile-label]");
    var deltaEl = tileEl.querySelector("[data-tile-delta]");
    if (valueEl) {
      var value = metric.value == null ? "" : String(metric.value);
      valueEl.textContent = value;
      valueEl.title = value;
      valueEl.className = ("tile-value " + sizeClassForValue(value)).trim();
    }
    if (labelEl) labelEl.textContent = metric.label;
    if (deltaEl) {
      if (metric.delta) {
        deltaEl.hidden = false;
        deltaEl.textContent = metric.delta;
        deltaEl.className = "tile-delta " + deltaClass(metric.good);
      } else {
        deltaEl.hidden = true;
        deltaEl.textContent = "";
        deltaEl.className = "tile-delta";
      }
    }
  }

  function setActiveDot(tileEl, index) {
    var dots = tileEl.querySelectorAll("[data-tile-dot]");
    for (var i = 0; i < dots.length; i++) {
      dots[i].classList.toggle("is-active", i === index);
    }
  }

  function showIndex(tileEl, metrics, index) {
    var metric = metrics[index];
    if (!metric) return;
    var metricEl = tileEl.querySelector("[data-tile-metric]");
    if (metricEl) {
      metricEl.classList.add("tile-metric-fade-out");
      window.setTimeout(function () {
        renderMetric(tileEl, metric);
        metricEl.classList.remove("tile-metric-fade-out");
      }, FADE_MS);
    } else {
      renderMetric(tileEl, metric);
    }
    setActiveDot(tileEl, index);
  }

  function setupTile(tileEl, metrics, phaseIndex) {
    if (!metrics || metrics.length <= 1) return;

    var current = 0;
    var paused = false;
    var period = BASE_PERIOD_MS + phaseIndex * PERIOD_STEP_MS;

    function tick() {
      if (paused || document.hidden) return;
      current = (current + 1) % metrics.length;
      showIndex(tileEl, metrics, current);
    }

    function start() {
      var intervalId = window.setInterval(tick, period);
      scheduled.push(intervalId);
    }

    if (!reduceMotion) {
      var initialDelay = (phaseIndex * PHASE_STEP_MS) % BASE_PERIOD_MS;
      var timeoutId = window.setTimeout(start, initialDelay);
      scheduled.push(timeoutId);
    }

    tileEl.addEventListener("mouseenter", function () {
      paused = true;
    });
    tileEl.addEventListener("mouseleave", function () {
      paused = false;
    });

    var dots = tileEl.querySelectorAll("[data-tile-dot]");
    for (var i = 0; i < dots.length; i++) {
      dots[i].addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        var idx = parseInt(this.getAttribute("data-index"), 10);
        if (Number.isNaN(idx) || idx === current) return;
        current = idx;
        showIndex(tileEl, metrics, current);
      });
    }
  }

  function hydrate() {
    var gallery = document.querySelector(GALLERY_SELECTOR);
    if (!gallery) return;
    clearScheduled();
    var data = readTilesData();
    if (!data) return;
    var bySlug = {};
    data.forEach(function (tile) {
      bySlug[tile.slug] = tile;
    });
    var tiles = gallery.querySelectorAll(TILE_SELECTOR);
    for (var i = 0; i < tiles.length; i++) {
      var tileEl = tiles[i];
      var tile = bySlug[tileEl.getAttribute("data-slug")];
      if (!tile || !tile.metrics) continue;
      setupTile(tileEl, tile.metrics, i);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hydrate);
  } else {
    hydrate();
  }
  document.addEventListener("pdb:navigate", hydrate);
})();
