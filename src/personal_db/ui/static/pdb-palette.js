// Cmd+K / Ctrl+K navigation palette.
//
// The index is built entirely from the DOM (the sidebar already lists every
// app/tracker link -- see #pdb-sidebar in base.html / _nav_context in
// services/daemon/http.py) rather than a server-emitted data blob, so this
// file is a pure addition: no other template needs to change for the
// palette to stay in sync with whatever trackers/apps are installed.
//
// Re-scanned on every open (buildIndex is cheap and the sidebar can change
// between opens via pdb-nav.js swaps), so there's no stale-index risk from
// caching it once at load.
(function () {
  "use strict";

  var backdrop = null;
  var panel = null;
  var input = null;
  var list = null;
  var emptyState = null;
  var allEntries = [];
  var lastFocused = null;
  var selectedNavIndex = 0;

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function collapsedText(el) {
    var text = el.textContent || "";
    return text.replace(/\s+/g, " ").trim();
  }

  // Prefers an explicit accessible name (the footer's Health/Setup links
  // carry a title/aria-label rather than static link text -- Health's text
  // content is a live status string like "3 failing", not a stable label).
  function accessibleLabel(el) {
    var label = el.getAttribute("aria-label") || el.getAttribute("title");
    if (label) return label.trim();
    return collapsedText(el);
  }

  // Walks the sidebar's rendered links to build the palette index: apps,
  // trackers (plus synthesized "<Title> · data" / "<Title> · setup" entries
  // per tracker), and the fixed Dashboard / All apps / Health / Setup pages.
  function buildIndex() {
    var seen = Object.create(null);
    var entries = [];

    function add(label, href, group) {
      if (!href || !label || seen[href]) return;
      seen[href] = true;
      entries.push({ label: label, href: href, group: group });
    }

    var sidebar = document.getElementById("pdb-sidebar");
    if (!sidebar) return entries;

    var dashLink = sidebar.querySelector(".sidebar-header .sidebar-nav a[href]");
    if (dashLink) {
      add(collapsedText(dashLink) || "Dashboard", dashLink.getAttribute("href"), "Pages");
    }

    var scrollLinks = sidebar.querySelectorAll(".sidebar-scroll .sidebar-nav a[href]");
    scrollLinks.forEach(function (a) {
      var href = a.getAttribute("href");
      if (!href) return;
      var label = collapsedText(a);
      if (!label) return;
      if (href === "/a") {
        add("All apps", href, "Pages");
      } else if (href.indexOf("/a/") === 0) {
        add(label, href, "Apps");
      } else if (href.indexOf("/t/") === 0) {
        add(label, href, "Trackers");
        var slug = href.slice(3);
        if (slug) {
          add(label + " · data", "/t/" + slug + "/data", "Trackers");
          add(label + " · setup", "/setup/" + slug, "Trackers");
        }
      }
    });

    var healthLink = sidebar.querySelector('#sidebar-status a[href="/health"]');
    if (healthLink) {
      add(accessibleLabel(healthLink) || "Health", healthLink.getAttribute("href"), "Pages");
    }
    var setupLink = sidebar.querySelector(".sidebar-gear[href]");
    if (setupLink) {
      add(accessibleLabel(setupLink) || "Setup", setupLink.getAttribute("href"), "Pages");
    }

    return entries;
  }

  // Ranks a candidate label against the query. Returns null on no match,
  // else { tier, pos, indices } where tier 0 = prefix match, 1 =
  // word-boundary match, 2 = subsequence match (lower tier = better rank).
  // `indices` are the character offsets to highlight in the label.
  function matchEntry(query, label) {
    var lower = label.toLowerCase();
    var q = query.toLowerCase();
    var idx = lower.indexOf(q);
    if (idx === 0) {
      return { tier: 0, pos: 0, indices: range(0, q.length) };
    }
    if (idx > 0) {
      var before = label.charAt(idx - 1);
      if (/[^a-z0-9]/i.test(before)) {
        return { tier: 1, pos: idx, indices: range(idx, idx + q.length) };
      }
    }
    var indices = [];
    var ti = 0;
    for (var qi = 0; qi < q.length; qi++) {
      var found = -1;
      for (; ti < lower.length; ti++) {
        if (lower.charAt(ti) === q.charAt(qi)) {
          found = ti;
          ti++;
          break;
        }
      }
      if (found === -1) return null;
      indices.push(found);
    }
    return { tier: 2, pos: indices[0], indices: indices };
  }

  function range(start, end) {
    var out = [];
    for (var i = start; i < end; i++) out.push(i);
    return out;
  }

  // Builds the flat render list: grouped headers + all entries when the
  // query is empty, or a single rank-sorted list of matches otherwise.
  function buildDisplayItems(query) {
    if (!query) {
      var groups = ["Apps", "Trackers", "Pages"];
      var items = [];
      groups.forEach(function (group) {
        var groupEntries = allEntries.filter(function (e) { return e.group === group; });
        if (!groupEntries.length) return;
        items.push({ type: "header", text: group });
        groupEntries.forEach(function (e) {
          items.push({ type: "entry", entry: e, indices: [] });
        });
      });
      return items;
    }

    var matches = [];
    allEntries.forEach(function (e) {
      var m = matchEntry(query, e.label);
      if (m) matches.push({ entry: e, tier: m.tier, pos: m.pos, indices: m.indices });
    });
    matches.sort(function (a, b) {
      if (a.tier !== b.tier) return a.tier - b.tier;
      if (a.pos !== b.pos) return a.pos - b.pos;
      return a.entry.label.localeCompare(b.entry.label);
    });
    return matches.map(function (m) {
      return { type: "entry", entry: m.entry, indices: m.indices };
    });
  }

  function highlightLabel(label, indices) {
    if (!indices.length) return escapeHtml(label);
    var marked = Object.create(null);
    indices.forEach(function (i) { marked[i] = true; });
    var out = "";
    var i = 0;
    while (i < label.length) {
      if (marked[i]) {
        var start = i;
        while (i < label.length && marked[i]) i++;
        out += "<mark>" + escapeHtml(label.slice(start, i)) + "</mark>";
      } else {
        out += escapeHtml(label.charAt(i));
        i++;
      }
    }
    return out;
  }

  var currentDisplayItems = [];
  var currentNavPositions = []; // indices into currentDisplayItems that are entries

  function render(query) {
    currentDisplayItems = buildDisplayItems(query);
    currentNavPositions = [];
    currentDisplayItems.forEach(function (item, i) {
      if (item.type === "entry") currentNavPositions.push(i);
    });

    if (selectedNavIndex >= currentNavPositions.length) selectedNavIndex = 0;
    if (selectedNavIndex < 0) selectedNavIndex = Math.max(0, currentNavPositions.length - 1);

    if (!currentNavPositions.length) {
      list.innerHTML = "";
      emptyState.hidden = false;
      input.removeAttribute("aria-activedescendant");
      return;
    }
    emptyState.hidden = true;

    var html = "";
    var navPos = -1;
    currentDisplayItems.forEach(function (item) {
      if (item.type === "header") {
        html += '<li class="pdb-palette-group" role="presentation">' + escapeHtml(item.text) + "</li>";
        return;
      }
      navPos++;
      var isSelected = navPos === selectedNavIndex;
      html +=
        '<li role="option" id="pdb-palette-opt-' + navPos + '"' +
        ' class="pdb-palette-row' + (isSelected ? " is-selected" : "") + '"' +
        ' aria-selected="' + (isSelected ? "true" : "false") + '"' +
        ' data-href="' + escapeHtml(item.entry.href) + '"' +
        ' data-nav-index="' + navPos + '">' +
        '<span class="pdb-palette-row-label">' + highlightLabel(item.entry.label, item.indices) + "</span>" +
        '<span class="pdb-palette-row-href">' + escapeHtml(item.entry.href) + "</span>" +
        "</li>";
    });
    list.innerHTML = html;
    input.setAttribute("aria-activedescendant", "pdb-palette-opt-" + selectedNavIndex);

    var selectedEl = list.querySelector(".is-selected");
    if (selectedEl && selectedEl.scrollIntoView) {
      selectedEl.scrollIntoView({ block: "nearest" });
    }
  }

  function selectedHref() {
    var pos = currentNavPositions[selectedNavIndex];
    if (pos === undefined) return null;
    var item = currentDisplayItems[pos];
    return item ? item.entry.href : null;
  }

  function moveSelection(delta) {
    var count = currentNavPositions.length;
    if (!count) return;
    selectedNavIndex = (selectedNavIndex + delta + count) % count;
    render(input.value.trim());
  }

  function ensureDom() {
    if (backdrop) return;

    backdrop = document.createElement("div");
    backdrop.id = "pdb-palette-backdrop";
    backdrop.className = "pdb-palette-backdrop";
    backdrop.hidden = true;

    panel = document.createElement("div");
    panel.className = "pdb-palette";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "Navigation");

    input = document.createElement("input");
    input.type = "text";
    input.className = "pdb-palette-input";
    input.setAttribute("aria-label", "Search navigation");
    input.setAttribute("autocomplete", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "pdb-palette-list");
    input.placeholder = "Jump to…";

    list = document.createElement("ul");
    list.id = "pdb-palette-list";
    list.className = "pdb-palette-list";
    list.setAttribute("role", "listbox");

    emptyState = document.createElement("div");
    emptyState.className = "pdb-palette-empty";
    emptyState.textContent = "No matches";
    emptyState.hidden = true;

    panel.appendChild(input);
    panel.appendChild(list);
    panel.appendChild(emptyState);
    backdrop.appendChild(panel);
    document.body.appendChild(backdrop);

    backdrop.addEventListener("mousedown", function (event) {
      if (event.target === backdrop) close();
    });
    panel.addEventListener("mousedown", function (event) {
      event.stopPropagation();
    });

    input.addEventListener("input", function () {
      selectedNavIndex = 0;
      render(input.value.trim());
    });
    input.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "ArrowDown") {
        event.preventDefault();
        moveSelection(1);
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        moveSelection(-1);
      } else if (event.key === "Enter") {
        event.preventDefault();
        navigate(selectedHref());
      }
    });

    list.addEventListener("mousemove", function (event) {
      var row = event.target.closest ? event.target.closest(".pdb-palette-row") : null;
      if (!row) return;
      var navIndex = parseInt(row.getAttribute("data-nav-index"), 10);
      if (!isNaN(navIndex) && navIndex !== selectedNavIndex) {
        selectedNavIndex = navIndex;
        render(input.value.trim());
      }
    });
    list.addEventListener("click", function (event) {
      var row = event.target.closest ? event.target.closest(".pdb-palette-row") : null;
      if (!row) return;
      navigate(row.getAttribute("data-href"));
    });
  }

  function navigate(href) {
    if (!href) return;
    close();
    window.location.assign(href);
  }

  function isOpen() {
    return !!backdrop && !backdrop.hidden;
  }

  function open() {
    ensureDom();
    if (isOpen()) return;
    allEntries = buildIndex();
    lastFocused = document.activeElement;
    selectedNavIndex = 0;
    input.value = "";
    backdrop.hidden = false;
    render("");
    // Focus after the element is visible so mobile Safari/VoiceOver picks
    // up the field reliably.
    window.setTimeout(function () {
      input.focus();
    }, 0);
  }

  function close() {
    if (!isOpen()) return;
    backdrop.hidden = true;
    if (lastFocused && typeof lastFocused.focus === "function") {
      lastFocused.focus();
    }
    lastFocused = null;
  }

  document.addEventListener("keydown", function (event) {
    var key = (event.key || "").toLowerCase();
    if (key !== "k" || !(event.metaKey || event.ctrlKey)) return;
    event.preventDefault();
    if (isOpen()) {
      close();
    } else {
      open();
    }
  });
})();
