// Settings manage-list filter tabs + search (setup.html) + marketplace
// search (setup_browse.html). All pure client-side, no server round-trip:
//
//   - setup.html: each installed-source row carries data-status="ready" or
//     "attention" (see setup_runner.py's status_class -> the "● Ready" /
//     "Needs setup" chip already computed server-side) and
//     data-search-text="<title> <description>" (lowercased server-side).
//     The All/Ready/Needs attention tabs and the search box both filter the
//     same row list and compose (AND, not either/or) -- a row must match
//     the active status tab AND the current search query to stay visible.
//     Module-level `_sourceStatusFilter`/`_sourceSearchQuery` hold the
//     combined state since the tab click handler and the search input
//     handler each only know about their own half.
//   - setup_browse.html: a search input filters the card grid by matching
//     against each card's data-search-text against the query, lowercased
//     here. No debounce -- it's a client-side array filter over ~20 cards,
//     not a network call.
//
// IIFE + delegated document-level listeners (consistent with
// pdb-connector-setup.js / pdb-discreet.js) so both features survive
// pdb-nav.js's innerHTML swap on navigation without a fresh
// addEventListener call leaking a duplicate handler -- re-init on
// `pdb:navigate` just re-applies the current (reset) filter state to
// whatever fresh DOM just landed.
(function () {
  "use strict";

  var _sourceStatusFilter = "all";
  var _sourceSearchQuery = "";

  function applySourceFilters() {
    var list = document.querySelector("[data-source-manage-list]");
    if (!list) return;
    var rows = list.querySelectorAll("[data-status]");
    var visibleCount = 0;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var statusOk =
        _sourceStatusFilter === "all" ||
        row.getAttribute("data-status") === _sourceStatusFilter;
      var haystack = row.getAttribute("data-search-text") || "";
      var searchOk =
        !_sourceSearchQuery || haystack.indexOf(_sourceSearchQuery) !== -1;
      var show = statusOk && searchOk;
      row.hidden = !show;
      if (show) visibleCount++;
    }
    var empty = document.querySelector("[data-source-filter-empty]");
    if (empty) empty.hidden = visibleCount !== 0;
  }

  function updateSourceTabsUI(tabsEl) {
    var tabs = tabsEl.querySelectorAll("[data-filter]");
    for (var j = 0; j < tabs.length; j++) {
      var isActive = tabs[j].getAttribute("data-filter") === _sourceStatusFilter;
      tabs[j].classList.toggle("is-active", isActive);
      tabs[j].setAttribute("aria-selected", isActive ? "true" : "false");
    }
  }

  function applyMarketplaceSearch(query) {
    var grid = document.querySelector("[data-marketplace-grid]");
    if (!grid) return;
    var needle = (query || "").trim().toLowerCase();
    var cards = grid.querySelectorAll("[data-marketplace-card]");
    var visibleCount = 0;
    for (var i = 0; i < cards.length; i++) {
      var card = cards[i];
      var haystack = card.getAttribute("data-search-text") || "";
      var show = !needle || haystack.indexOf(needle) !== -1;
      card.hidden = !show;
      if (show) visibleCount++;
    }
    var empty = document.querySelector("[data-marketplace-empty]");
    if (empty) empty.hidden = visibleCount !== 0;
  }

  function init() {
    // Reset any stale filter state a previous page swap left behind.
    _sourceStatusFilter = "all";
    _sourceSearchQuery = "";

    var tabsEl = document.querySelector("[data-source-filter-tabs]");
    if (tabsEl) {
      updateSourceTabsUI(tabsEl);
      applySourceFilters();
    }

    var sourceSearchEl = document.querySelector("[data-source-search]");
    if (sourceSearchEl) sourceSearchEl.value = "";

    var searchEl = document.querySelector("[data-marketplace-search]");
    if (searchEl) {
      searchEl.value = "";
      applyMarketplaceSearch("");
    }
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    var tabBtn = target.closest("[data-filter]");
    if (!tabBtn) return;
    var tabsEl = tabBtn.closest("[data-source-filter-tabs]");
    if (!tabsEl) return;
    _sourceStatusFilter = tabBtn.getAttribute("data-filter");
    updateSourceTabsUI(tabsEl);
    applySourceFilters();
  });

  document.addEventListener("input", function (event) {
    var target = event.target instanceof Element ? event.target : null;
    if (!target) return;
    if (target.matches("[data-source-search]")) {
      _sourceSearchQuery = (target.value || "").trim().toLowerCase();
      applySourceFilters();
      return;
    }
    if (target.matches("[data-marketplace-search]")) {
      applyMarketplaceSearch(target.value);
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
  document.addEventListener("pdb:navigate", init);
})();
