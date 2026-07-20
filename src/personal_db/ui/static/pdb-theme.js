// Color theme system: auto (OS-follows) / light / dark / morandi ---------
//
// Persistence is a single localStorage key ("pdb-theme"); the effective
// choice is applied via `data-theme` on <html>, which pins (or leaves OS-
// following) `color-scheme` -- see style.css's `:root` light-dark() token
// block and its `:root[data-theme="..."]` overrides. That's the whole
// mechanism: every existing `var(--token)` already resolves to the right
// palette with no second set of rules to maintain. base.html's inline
// <head> script (which runs before the stylesheet link) replays the
// persisted choice pre-paint so there's no flash of the wrong scheme; this
// file owns the public API plus the Settings-page picker.
//
// AG Charts renders to <canvas> and can't read CSS custom properties live,
// and AG Grid's theme is selected via a CSS *class* rather than vars alone
// -- both pdb-chart.js and pdb-data.js need to know whether the EFFECTIVE
// scheme is dark right now (including morandi's own OS-follows split), so
// `isDark()` resolves the computed `--bg` token rather than re-deriving
// the auto/morandi logic a second time.
(function () {
  "use strict";

  var STORAGE_KEY = "pdb-theme";
  var VALID_THEMES = ["auto", "light", "dark", "morandi"];
  var CHANGE_EVENT = "pdb-theme-change";

  function get() {
    try {
      var stored = localStorage.getItem(STORAGE_KEY);
      if (stored && VALID_THEMES.indexOf(stored) !== -1) return stored;
    } catch (e) {
      // localStorage can throw (private browsing, disabled storage) --
      // fall through to the "auto" default.
    }
    return "auto";
  }

  function set(theme) {
    if (VALID_THEMES.indexOf(theme) === -1) return;
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch (e) {
      // Choice still applies for the rest of this page load, it just won't
      // persist across reloads.
    }
    if (theme === "auto") {
      delete document.documentElement.dataset.theme;
    } else {
      document.documentElement.dataset.theme = theme;
    }
    window.dispatchEvent(new CustomEvent(CHANGE_EVENT));
  }

  function parseRgb(value) {
    var match = /rgba?\(\s*([\d.]+)[,\s]+([\d.]+)[,\s]+([\d.]+)/.exec(value || "");
    if (!match) return null;
    return [Number(match[1]), Number(match[2]), Number(match[3])];
  }

  function relativeLuminance(rgb) {
    var srgb = rgb.map(function (channel) {
      var v = channel / 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * srgb[0] + 0.7152 * srgb[1] + 0.0722 * srgb[2];
  }

  // Resolves the *effective* rendered scheme (not just the stored
  // preference) by reading the computed `--bg` token, which every theme
  // (including morandi's OS-follows split) already resolves via
  // light-dark(). Nothing is cached -- callers get a fresh read each time.
  function isDark() {
    var raw = getComputedStyle(document.documentElement).getPropertyValue("--bg");
    var rgb = parseRgb(raw);
    if (!rgb) return false;
    return relativeLuminance(rgb) < 0.5;
  }

  function onChange(cb) {
    if (typeof cb !== "function") return;
    window.addEventListener(CHANGE_EVENT, cb);
    // Auto/morandi track the OS setting without ever calling set(), so a
    // raw OS scheme flip needs its own listener to reach the same callers.
    if (window.matchMedia) {
      var query = window.matchMedia("(prefers-color-scheme: dark)");
      if (query.addEventListener) {
        query.addEventListener("change", cb);
      } else if (query.addListener) {
        // Safari < 14 / older WebKit fallback.
        query.addListener(cb);
      }
    }
  }

  window.PDBTheme = { get: get, set: set, isDark: isDark, onChange: onChange };

  // ---- Settings page picker (setup.html's "Appearance" section) ---------
  // Guarded on the element existing -- a no-op everywhere else.

  function syncPicker(root) {
    var current = get();
    root.querySelectorAll("[data-theme-option]").forEach(function (btn) {
      var active = btn.getAttribute("data-theme-option") === current;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function initPicker() {
    var root = document.querySelector("[data-theme-picker]");
    if (root) syncPicker(root);
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    var btn = target && target.closest("[data-theme-option]");
    if (!btn) return;
    var root = btn.closest("[data-theme-picker]");
    if (!root) return;
    set(btn.getAttribute("data-theme-option"));
    syncPicker(root);
  });

  // Keeps the picker's active state in sync if the theme changes some other
  // way (e.g. this same page open in two tabs, or an OS scheme flip while
  // "Auto"/"Morandi" is selected).
  onChange(function () {
    var root = document.querySelector("[data-theme-picker]");
    if (root) syncPicker(root);
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initPicker);
  } else {
    initPicker();
  }
  // pdb-nav.js's client-side page swap replaces `.content > main` -- re-sync
  // against whatever fresh picker markup (or lack of one) just landed.
  document.addEventListener("pdb:navigate", initPicker);
})();
