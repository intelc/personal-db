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
// The click handler is DELEGATED to `document`, not bound to the button:
// pdb-nav.js's `updateSidebarStatus` replaces the sidebar footer's markup
// (`#sidebar-status`, which contains this button) via innerHTML on every
// client-side navigation, which silently destroys any listener bound to
// the button node itself -- the v0.1.4 bug where the eye went dead after
// the first nav. Delegation survives the swap; the `pdb:navigate` listener
// below re-applies the icon/aria state that the server-rendered
// replacement markup resets (the server can't know the localStorage-only
// discreet choice).
(function () {
  "use strict";

  var STORAGE_KEY = "pdb_discreet";
  var BUTTON_ID = "pdb-discreet-toggle";

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

  /// Re-applies icon + aria-pressed onto whatever button node currently
  /// exists -- safe to call after any sidebar-footer markup swap.
  function syncButton() {
    var button = document.getElementById(BUTTON_ID);
    if (button) applyIcons(button, isDiscreet());
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
    syncButton();
  }

  function init() {
    syncButton();
    document.addEventListener("click", function (event) {
      var button = event.target instanceof Element
        ? event.target.closest("#" + BUTTON_ID)
        : null;
      if (button) setDiscreet(!isDiscreet());
    });
    // pdb-nav.js dispatches this after every client-side swap (the same
    // hook pdb-chart.js/pdb-app-state.js re-hydrate on).
    document.addEventListener("pdb:navigate", syncButton);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
