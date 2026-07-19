// App shell chrome: narrow-window sidebar toggle (off-canvas below 720px).
// No framework, no build step -- see CLAUDE.md conventions for this repo.
(function () {
  "use strict";

  function ready(fn) {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", fn);
    } else {
      fn();
    }
  }

  ready(function () {
    var toggle = document.getElementById("pdb-sidebar-toggle");
    var scrim = document.getElementById("pdb-sidebar-scrim");
    var sidebar = document.getElementById("pdb-sidebar");
    if (!toggle || !sidebar) return;

    function open() {
      document.body.classList.add("sidebar-open");
    }
    function close() {
      document.body.classList.remove("sidebar-open");
    }
    function toggleOpen() {
      document.body.classList.toggle("sidebar-open");
    }

    toggle.addEventListener("click", toggleOpen);
    if (scrim) scrim.addEventListener("click", close);

    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape") close();
    });

    sidebar.addEventListener("click", function (e) {
      if (e.target && e.target.tagName === "A") close();
    });
  });
})();
