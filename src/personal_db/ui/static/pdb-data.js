// Data navigator: table picker, paging, server-side sort/search, and a
// row-detail panel for /t/<tracker>/data (data_browser.html). Listens on
// `document` for delegated clicks/input/keydown, so it keeps working across
// pdb-nav.js's innerHTML swaps with no re-wiring on 'pdb:navigate' -- the
// listeners themselves never go away, they just find whatever
// [data-data-browser] happens to be in the live DOM at event time.
//
// Table switches / page turns / sort changes / searches all fetch
// GET /api/v1/data/<tracker>/<table>?... and rebuild the same AG Grid
// payload markup personal_db.ui.aggrid.grid() would have server-rendered
// (matching field naming: c0, c1, ...), then dispatch 'pdb:navigate' so
// pdb-grid.js's existing mount scan picks up the fresh [data-pdb-grid]
// element. Sort is driven by AG Grid's own header UI: we listen for the
// grid's 'sortChanged' event (via the 'pdb-grid-ready' hook pdb-grid.js
// dispatches once a grid's API is available) and refetch server-side rather
// than trusting AG Grid's client-side reorder of whatever page happens to be
// loaded.
//
// Row detail: each row's SQLite `rowid` rides along as a hidden `_rowid`
// field on the grid's row data (see routes/data.py's `_read_table` /
// `tracker_data_page`). Clicking a cell (via AG Grid's 'cellClicked' event)
// opens a right-side panel fetched from the row endpoint. The panel element
// lives outside <main> (appended to <body>, like pdb-agent-terminal.js's
// drawer) so it survives pdb-nav.js's innerHTML swap of `.content > main`;
// it's explicitly closed on every 'pdb:navigate' since the data underneath
// it just changed.
(function () {
  "use strict";

  var gridIdCounter = 0;
  var SEARCH_DEBOUNCE_MS = 300;

  function findRoot(el) {
    return el.closest("[data-data-browser]");
  }

  // AG Grid's theme is a CSS class, not just vars -- pick "ag-theme-quartz"
  // vs "ag-theme-quartz-dark" from PDBTheme.isDark() (rather than the OS-
  // only "ag-theme-quartz-auto-dark") so a forced light/dark/morandi choice
  // from the Settings picker is respected.
  function gridThemeClass() {
    return window.PDBTheme && window.PDBTheme.isDark() ? "ag-theme-quartz-dark" : "ag-theme-quartz";
  }

  // aggrid.py's server-rendered grids (used all over the app, not just the
  // /data browser) still emit the OS-only "ag-theme-quartz-auto-dark" --
  // the server can't know a localStorage-only theme choice at render time.
  // Normalize those onto the same explicit class this file already picks
  // for its own client-rebuilt grids, client-side, once they land in the
  // DOM. No-op if PDBTheme never loaded: those grids just keep following
  // the OS via ag-theme-quartz-auto-dark, same as before this existed.
  function normalizeGridThemeClasses() {
    if (!window.PDBTheme) return;
    var next = gridThemeClass();
    document.querySelectorAll(".pdb-grid.ag-theme-quartz-auto-dark").forEach(function (el) {
      el.classList.remove("ag-theme-quartz-auto-dark");
      el.classList.add(next);
    });
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function debounce(fn, wait) {
    var timer = null;
    return function () {
      var args = arguments;
      var self = this;
      if (timer) window.clearTimeout(timer);
      timer = window.setTimeout(function () {
        timer = null;
        fn.apply(self, args);
      }, wait);
    };
  }

  function buildEmptyHtml() {
    return (
      '<div class="empty-state"><p class="meta">No data yet.</p>' +
      '<p class="empty-state-hint">' +
      escapeHtml("This table is empty — sync this tracker to collect data.") +
      "</p></div>"
    );
  }

  // ---- table metadata (columns/semantic/time_column) --------------------
  // Read once per render from the SSR-embedded JSON blob (see
  // data_browser.html's [data-data-browser-tables] script tag) -- avoids an
  // extra round trip to /api/v1/data/<tracker> on every table switch.

  function getTablesMeta(root) {
    var script = root.querySelector("[data-data-browser-tables]");
    if (!script) return [];
    try {
      return JSON.parse(script.textContent || "[]");
    } catch (e) {
      return [];
    }
  }

  function getTableMeta(root, table) {
    var tables = getTablesMeta(root);
    for (var i = 0; i < tables.length; i++) {
      if (tables[i].name === table) return tables[i];
    }
    return null;
  }

  function headerTooltip(colMetaEntry) {
    if (!colMetaEntry) return undefined;
    if (colMetaEntry.semantic) return (colMetaEntry.type || "?") + " — " + colMetaEntry.semantic;
    if (colMetaEntry.type) return colMetaEntry.type;
    return undefined;
  }

  function buildGridHtml(columns, rows, rowids, limit, colMeta, sortField, sortDir) {
    gridIdCounter += 1;
    var gridId = "pdb-grid-data-" + gridIdCounter;
    var metaByName = {};
    (colMeta || []).forEach(function (c) {
      metaByName[c.name] = c;
    });
    var columnDefs = columns.map(function (name, i) {
      var def = { field: "c" + i, headerName: name, sortable: true, filter: true, resizable: true };
      var tooltip = headerTooltip(metaByName[name]);
      if (tooltip) def.headerTooltip = tooltip;
      if (name === sortField) def.sort = sortDir === "asc" ? "asc" : "desc";
      return def;
    });
    var rowData = rows.map(function (row, idx) {
      var obj = {};
      row.forEach(function (v, i) {
        obj["c" + i] = v === null ? "" : v;
      });
      obj._rowid = rowids && rowids[idx] != null ? rowids[idx] : null;
      return obj;
    });
    var height = Math.min(560, Math.max(220, 92 + Math.min(rowData.length, limit || 15) * 34));
    var options = {
      columnDefs: columnDefs,
      rowData: rowData,
      theme: "legacy",
      pagination: false,
      domLayout: "normal",
      grouped: false,
    };
    var payload = JSON.stringify(options).replace(/<\//g, "<\\/");
    return (
      '<div class="pdb-grid-wrap">' +
      '<div id="' +
      gridId +
      '" class="pdb-grid ' +
      gridThemeClass() +
      '" data-pdb-grid style="height:' +
      height +
      'px"></div>' +
      '<script type="application/json" data-pdb-grid-options="' +
      gridId +
      '">' +
      payload +
      "<\/script>" +
      "</div>"
    );
  }

  // Prev/Next enabled-ness is always *derived* from `root.dataset` (the
  // single source of truth for offset/total/limit), never snapshotted --
  // that way re-enabling the buttons after a fetch (success *or* failure)
  // can't clobber a state that render() already moved on from.
  function updatePagingButtons(root) {
    var offset = parseInt(root.dataset.offset, 10) || 0;
    var total = parseInt(root.dataset.total, 10) || 0;
    var limit = parseInt(root.dataset.limit, 10) || 100;
    var prevBtn = root.querySelector("[data-data-prev]");
    var nextBtn = root.querySelector("[data-data-next]");
    if (prevBtn) prevBtn.disabled = offset <= 0;
    if (nextBtn) nextBtn.disabled = offset + limit >= total;
  }

  function setBusy(root, busy) {
    root.querySelectorAll("[data-data-table-btn]").forEach(function (btn) {
      btn.disabled = busy;
    });
    if (busy) {
      var prevBtn = root.querySelector("[data-data-prev]");
      var nextBtn = root.querySelector("[data-data-next]");
      if (prevBtn) prevBtn.disabled = true;
      if (nextBtn) nextBtn.disabled = true;
    } else {
      updatePagingButtons(root);
    }
  }

  function formatStats(payload, tableMeta) {
    var colCount = tableMeta && tableMeta.columns ? tableMeta.columns.length : payload.columns.length;
    var parts = [];
    var timeRange = tableMeta && tableMeta.time_range;
    if (timeRange) parts.push(timeRange.min + " → " + timeRange.max);
    parts.push(colCount + " column" + (colCount === 1 ? "" : "s"));
    return parts.join(" · ");
  }

  function render(root, payload, table) {
    var mount = root.querySelector("[data-data-browser-mount]");
    var meta = root.querySelector("[data-data-browser-meta]");
    var stats = root.querySelector("[data-data-browser-stats]");
    var rows = payload.rows || [];
    var tableMeta = getTableMeta(root, table);
    if (mount) {
      mount.innerHTML = rows.length
        ? buildGridHtml(
            payload.columns || [],
            rows,
            payload.rowids,
            payload.limit,
            tableMeta ? tableMeta.columns : null,
            root.dataset.sort || null,
            root.dataset.dir || "desc"
          )
        : buildEmptyHtml();
    }
    if (meta) {
      meta.textContent = rows.length
        ? payload.total + " rows · showing " + (payload.offset + 1) + "–" + (payload.offset + rows.length)
        : "0 rows";
    }
    if (stats) stats.textContent = formatStats(payload, tableMeta);
    root.dataset.offset = String(payload.offset);
    root.dataset.total = String(payload.total);
    root.dataset.limit = String(payload.limit);
    root.dataset.columns = JSON.stringify(payload.columns || []);
    document.dispatchEvent(new CustomEvent("pdb:navigate"));
  }

  function buildUrl(root, table, offset) {
    var tracker = root.dataset.tracker;
    var limit = parseInt(root.dataset.limit, 10) || 100;
    var params = new URLSearchParams();
    params.set("limit", String(limit));
    params.set("offset", String(Math.max(0, offset)));
    if (root.dataset.sort) {
      params.set("sort", root.dataset.sort);
      params.set("dir", root.dataset.dir === "asc" ? "asc" : "desc");
    }
    if (root.dataset.q) params.set("q", root.dataset.q);
    return (
      "/api/v1/data/" + encodeURIComponent(tracker) + "/" + encodeURIComponent(table) + "?" + params.toString()
    );
  }

  function load(root, table, offset) {
    var tracker = root.dataset.tracker;
    if (!tracker || !table) return;
    setBusy(root, true);
    fetch(buildUrl(root, table, offset), { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("pdb-data: fetch failed (" + response.status + ")");
        return response.json();
      })
      .then(function (payload) {
        root.dataset.table = table;
        render(root, payload, table);
      })
      .catch(function () {
        var meta = root.querySelector("[data-data-browser-meta]");
        if (meta) meta.textContent = "Couldn't load this table.";
      })
      .finally(function () {
        // Derives Prev/Next from root.dataset -- updated by render() on
        // success, left untouched (so still correct) if the fetch failed.
        setBusy(root, false);
      });
  }

  function switchTable(root, table) {
    var tableMeta = getTableMeta(root, table);
    root.dataset.sort = (tableMeta && tableMeta.time_column) || "";
    root.dataset.dir = "desc";
    root.dataset.columns = "";
    var search = root.querySelector("[data-data-search]");
    if (search) search.value = "";
    root.dataset.q = "";
    closePanel();
    load(root, table, 0);
  }

  // The DOM already carries the current table's raw column order (c0..cN ->
  // name) in whatever grid happens to be mounted -- SSR on first paint,
  // load()/render() afterwards. Recovering it from there (rather than a
  // dedicated data attribute the server would also have to populate) means
  // the sortChanged handler below has a name to map a clicked column's colId
  // back to before the first AJAX fetch ever runs.
  function syncColumnsFromDom(root) {
    if (root.dataset.columns) return;
    var script = root.querySelector("[data-data-browser-mount] script[data-pdb-grid-options]");
    if (!script) {
      root.dataset.columns = "[]";
      return;
    }
    try {
      var raw = JSON.parse(script.textContent || "{}");
      var cols = (raw.columnDefs || []).map(function (c) {
        return c.headerName;
      });
      root.dataset.columns = JSON.stringify(cols);
    } catch (e) {
      root.dataset.columns = "[]";
    }
  }

  function initAll() {
    document.querySelectorAll("[data-data-browser]").forEach(syncColumnsFromDom);
    // Catches the race described above wireGrid()'s definition: a grid that
    // finished initializing (and dispatched 'pdb-grid-ready') before this
    // file's own listener for that event was registered. wireGrid() is a
    // no-op for anything already wired.
    document.querySelectorAll("[data-data-browser] [data-pdb-grid]").forEach(function (el) {
      if (el.__pdbGridApi) wireGrid(el);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initAll);
    document.addEventListener("DOMContentLoaded", normalizeGridThemeClasses);
  } else {
    initAll();
    normalizeGridThemeClasses();
  }
  document.addEventListener("pdb:navigate", initAll);
  // pdb-nav.js's client-side page swap brings in fresh SSR markup (e.g. a
  // freshly-rendered aggrid.py grid on the newly-loaded page), which still
  // needs normalizing the same as the very first page load did.
  document.addEventListener("pdb:navigate", normalizeGridThemeClasses);

  // A theme-picker choice (or an OS scheme flip while on Auto/Morandi) needs
  // every already-mounted grid's theme class swapped -- a class swap is
  // sufficient for AG Grid's CSS-driven theme, no grid recreate needed. This
  // covers both this file's own "ag-theme-quartz"/"-dark" grids and, via
  // normalizeGridThemeClasses(), any aggrid.py "ag-theme-quartz-auto-dark"
  // grids that arrived since the last normalize pass.
  if (window.PDBTheme && typeof window.PDBTheme.onChange === "function") {
    window.PDBTheme.onChange(function () {
      var next = gridThemeClass();
      document.querySelectorAll(".pdb-grid.ag-theme-quartz, .pdb-grid.ag-theme-quartz-dark").forEach(function (el) {
        el.classList.remove("ag-theme-quartz", "ag-theme-quartz-dark");
        el.classList.add(next);
      });
      normalizeGridThemeClasses();
    });
  }

  // ---- row detail panel ---------------------------------------------------

  var PANEL_ID = "pdb-data-row-panel";

  function ensurePanel() {
    var panel = document.getElementById(PANEL_ID);
    if (panel) return panel;
    panel = document.createElement("aside");
    panel.id = PANEL_ID;
    panel.className = "data-row-panel";
    panel.innerHTML =
      '<div class="data-row-panel-head">' +
      '<strong>Row detail</strong>' +
      '<button type="button" class="data-row-panel-close" data-data-row-close aria-label="Close">&times;</button>' +
      "</div>" +
      '<div class="data-row-panel-body" data-data-row-body></div>';
    document.body.appendChild(panel);
    return panel;
  }

  function isJsonLike(value) {
    if (typeof value !== "string") return false;
    var s = value.trim();
    return (s.charAt(0) === "{" && s.charAt(s.length - 1) === "}") || (s.charAt(0) === "[" && s.charAt(s.length - 1) === "]");
  }

  function renderRowBody(body, columns, row) {
    var html = "";
    columns.forEach(function (name, i) {
      var value = row[i];
      var display;
      if (value === null || value === undefined) {
        display = '<span class="data-row-panel-null">null</span>';
      } else if (isJsonLike(value)) {
        var pretty = value;
        try {
          pretty = JSON.stringify(JSON.parse(value), null, 2);
        } catch (e) {
          pretty = value;
        }
        display = "<pre>" + escapeHtml(pretty) + "</pre>";
      } else {
        display = "<pre>" + escapeHtml(String(value)) + "</pre>";
      }
      html +=
        '<div class="data-row-panel-field">' +
        '<div class="data-row-panel-field-name">' +
        escapeHtml(name) +
        "</div>" +
        display +
        "</div>";
    });
    body.innerHTML = html;
  }

  function openPanel(tracker, table, rowid) {
    var panel = ensurePanel();
    var body = panel.querySelector("[data-data-row-body]");
    if (body) body.innerHTML = '<p class="meta">Loading…</p>';
    document.documentElement.classList.add("pdb-data-row-open");
    var url =
      "/api/v1/data/" +
      encodeURIComponent(tracker) +
      "/" +
      encodeURIComponent(table) +
      "/row?rowid=" +
      encodeURIComponent(rowid);
    fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("pdb-data: row fetch failed (" + response.status + ")");
        return response.json();
      })
      .then(function (payload) {
        if (body) renderRowBody(body, payload.columns || [], payload.row || []);
      })
      .catch(function () {
        if (body) body.innerHTML = '<p class="meta">Couldn’t load this row.</p>';
      });
  }

  function closePanel() {
    document.documentElement.classList.remove("pdb-data-row-open");
  }

  // ---- AG Grid hookup: server-side sort + row-click detail ---------------
  // pdb-grid.js dispatches 'pdb-grid-ready' (bubbles) on the mount element
  // once its AG Grid API is ready (el.__pdbGridApi). Listening for that here
  // -- rather than trying to pass function callbacks through the JSON
  // options blob, which can't serialize functions -- lets us register
  // 'sortChanged'/'cellClicked' on whatever grid instance is currently live,
  // including ones rebuilt by our own load()/render() above.
  //
  // The very first, server-rendered grid is a special case: pdb-grid.js's
  // own top-level code runs its initAll() *immediately* rather than waiting
  // for 'DOMContentLoaded' (by the time any deferred script executes,
  // `document.readyState` is already 'interactive', not 'loading' -- see
  // pdb-grid.js). Since pdb-grid.js is <script>'d before this file, that
  // first 'pdb-grid-ready' can fire before this file has even registered the
  // listener below. wireGrid() is therefore also called directly from
  // initAll() (further down), which re-scans the DOM for any grid that's
  // already ready but not yet wired -- covers that race without depending on
  // <script> tag order.
  function wireGrid(el) {
    if (el.dataset.pdbDataWired === "1") return;
    var root = el.closest && findRoot(el);
    if (!root) return;
    var api = el.__pdbGridApi;
    if (!api || typeof api.addEventListener !== "function") return;
    el.dataset.pdbDataWired = "1";

    api.addEventListener("sortChanged", function () {
      var state = typeof api.getColumnState === "function" ? api.getColumnState() : [];
      var sorted = state.filter(function (s) {
        return s.sort;
      })[0];
      var columns = JSON.parse(root.dataset.columns || "[]");
      if (!sorted) {
        root.dataset.sort = "";
        root.dataset.dir = "desc";
      } else {
        var idx = parseInt(String(sorted.colId).replace(/^c/, ""), 10);
        var name = columns[idx];
        if (!name) return;
        root.dataset.sort = name;
        root.dataset.dir = sorted.sort === "asc" ? "asc" : "desc";
      }
      load(root, root.dataset.table, 0);
    });

    api.addEventListener("cellClicked", function (params) {
      var rowid = params.data ? params.data._rowid : null;
      if (rowid === null || rowid === undefined) return;
      openPanel(root.dataset.tracker, root.dataset.table, rowid);
    });
  }

  document.addEventListener("pdb-grid-ready", function (event) {
    if (event.target && event.target.nodeType === 1) wireGrid(event.target);
  });

  document.addEventListener("click", function (event) {
    var tableBtn = event.target.closest("[data-data-table-btn]");
    if (tableBtn) {
      var root = findRoot(tableBtn);
      if (!root) return;
      var table = tableBtn.getAttribute("data-data-table-btn");
      if (table === root.dataset.table) return;
      root.querySelectorAll("[data-data-table-btn]").forEach(function (btn) {
        var active = btn === tableBtn;
        btn.classList.toggle("is-active", active);
        if (active) btn.setAttribute("aria-current", "true");
        else btn.removeAttribute("aria-current");
      });
      switchTable(root, table);
      return;
    }

    var prevBtn = event.target.closest("[data-data-prev]");
    if (prevBtn && !prevBtn.disabled) {
      var prevRoot = findRoot(prevBtn);
      if (!prevRoot) return;
      var prevLimit = parseInt(prevRoot.dataset.limit, 10) || 100;
      var prevOffset = Math.max(0, (parseInt(prevRoot.dataset.offset, 10) || 0) - prevLimit);
      closePanel();
      load(prevRoot, prevRoot.dataset.table, prevOffset);
      return;
    }

    var nextBtn = event.target.closest("[data-data-next]");
    if (nextBtn && !nextBtn.disabled) {
      var nextRoot = findRoot(nextBtn);
      if (!nextRoot) return;
      var nextLimit = parseInt(nextRoot.dataset.limit, 10) || 100;
      var nextOffset = (parseInt(nextRoot.dataset.offset, 10) || 0) + nextLimit;
      closePanel();
      load(nextRoot, nextRoot.dataset.table, nextOffset);
      return;
    }

    if (event.target.closest("[data-data-row-close]") || event.target.id === PANEL_ID + "-backdrop") {
      closePanel();
      return;
    }
    // Click outside the open panel (and outside anything that opens it)
    // closes it -- mirrors the agent-terminal drawer's outside-click rule.
    if (document.documentElement.classList.contains("pdb-data-row-open")) {
      var panel = document.getElementById(PANEL_ID);
      if (panel && !panel.contains(event.target) && !event.target.closest(".ag-row")) {
        closePanel();
      }
    }
  });

  document.addEventListener(
    "input",
    debounce(function (event) {
      var search = event.target.closest("[data-data-search]");
      if (!search) return;
      var root = findRoot(search);
      if (!root) return;
      root.dataset.q = search.value || "";
      load(root, root.dataset.table, 0);
    }, SEARCH_DEBOUNCE_MS)
  );

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape" && document.documentElement.classList.contains("pdb-data-row-open")) {
      closePanel();
    }
  });
})();
