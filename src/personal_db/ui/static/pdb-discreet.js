// Discreet mode: a sidebar-footer eye toggle that blurs every element
// marked `.pdb-sensitive` (dashboard tile values/deltas for sensitive
// metrics, finance app stat-card values, plaid/monarch balance & amount
// table columns -- see the metrics contract note in services/ui/tiles.py
// and the `html.pdb-discreet .pdb-sensitive` rule in style.css).
//
// base.html's inline <head> script (runs synchronously, before this
// deferred file loads) already replays the persisted choice onto
// <html class="pdb-discreet"> before first paint, so there is no flash of
// sensitive values on load/reload -- this file only owns the click handler,
// the eye/eye-off icon swap, and persisting future toggles.
//
// The toggle button lives in the sidebar footer (`#pdb-sidebar`), which
// pdb-nav.js's client-side navigation never replaces (only `.content >
// main` is swapped -- see pdb-nav.js), so this only needs to bind once on
// initial load, no `pdb:navigate` re-hydration required.
(function () {
  "use strict";

  var STORAGE_KEY = "pdb_discreet";

  function isDiscreet() {
    return document.documentElement.classList.contains("pdb-discreet");
  }

  function applyIcons(button, discreet) {
    var eye = button.querySelector(".pdb-discreet-icon-eye");
    var eyeOff = button.querySelector(".pdb-discreet-icon-eye-off");
    if (eye) eye.style.display = discreet ? "none" : "";
    if (eyeOff) eyeOff.style.display = discreet ? "" : "none";
    button.setAttribute("aria-pressed", discreet ? "true" : "false");
  }

  function setDiscreet(discreet) {
    document.documentElement.classList.toggle("pdb-discreet", discreet);
    try {
      localStorage.setItem(STORAGE_KEY, discreet ? "1" : "0");
    } catch (e) {
      // localStorage can throw (private browsing, disabled storage) --
      // the toggle still works for the rest of this page load, it just
      // won't persist across reloads.
    }
    var button = document.getElementById("pdb-discreet-toggle");
    if (button) applyIcons(button, discreet);
  }

  function init() {
    var button = document.getElementById("pdb-discreet-toggle");
    if (!button) return;
    applyIcons(button, isDiscreet());
    button.addEventListener("click", function () {
      setDiscreet(!isDiscreet());
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
