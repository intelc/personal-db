// Data browser: table picker + Prev/Next paging for /t/<tracker>/data
// (data_browser.html). Listens on `document` for delegated clicks, so it
// keeps working across pdb-nav.js's innerHTML swaps with no re-wiring on
// 'pdb:navigate' -- the listener itself never goes away, it just finds
// whatever [data-data-browser] happens to be in the live DOM at click time.
//
// Each table switch / page turn fetches GET /api/v1/data/<tracker>/<table>
// and rebuilds the same AG Grid payload markup personal_db.ui.aggrid.grid()
// would have server-rendered (matching field naming: c0, c1, ...), then
// dispatches 'pdb:navigate' so pdb-grid.js's existing mount scan -- which
// already listens for that event -- picks up the fresh [data-pdb-grid]
// element. That reuses pdb-grid.js's tested mounting code instead of
// duplicating AG Grid setup here, and handles the empty <-> non-empty
// transition uniformly (always a fresh element, never a stale grid API
// reference to juggle across swaps).
(function () {
  "use strict";

  var gridIdCounter = 0;

  function findRoot(el) {
    return el.closest("[data-data-browser]");
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function buildEmptyHtml() {
    return (
      '<div class="empty-state"><p class="meta">No data yet.</p>' +
      '<p class="empty-state-hint">' +
      escapeHtml("This table is empty — sync this tracker to collect data.") +
      "</p></div>"
    );
  }

  function buildGridHtml(columns, rows, limit) {
    gridIdCounter += 1;
    var gridId = "pdb-grid-data-" + gridIdCounter;
    var columnDefs = columns.map(function (name, i) {
      return { field: "c" + i, headerName: name, sortable: true, filter: true, resizable: true };
    });
    var rowData = rows.map(function (row) {
      var obj = {};
      row.forEach(function (v, i) {
        obj["c" + i] = v === null ? "" : v;
      });
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
      '" class="pdb-grid ag-theme-quartz-auto-dark" data-pdb-grid style="height:' +
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

  function render(root, payload) {
    var mount = root.querySelector("[data-data-browser-mount]");
    var meta = root.querySelector("[data-data-browser-meta]");
    var rows = payload.rows || [];
    if (mount) {
      mount.innerHTML = rows.length ? buildGridHtml(payload.columns || [], rows, payload.limit) : buildEmptyHtml();
    }
    if (meta) {
      meta.textContent = rows.length
        ? payload.total + " rows · showing " + (payload.offset + 1) + "–" + (payload.offset + rows.length)
        : "0 rows";
    }
    root.dataset.offset = String(payload.offset);
    root.dataset.total = String(payload.total);
    root.dataset.limit = String(payload.limit);
    document.dispatchEvent(new CustomEvent("pdb:navigate"));
  }

  function load(root, table, offset) {
    var tracker = root.dataset.tracker;
    var limit = parseInt(root.dataset.limit, 10) || 100;
    if (!tracker || !table) return;
    setBusy(root, true);
    var url =
      "/api/v1/data/" +
      encodeURIComponent(tracker) +
      "/" +
      encodeURIComponent(table) +
      "?limit=" +
      limit +
      "&offset=" +
      Math.max(0, offset);
    fetch(url, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) throw new Error("pdb-data: fetch failed (" + response.status + ")");
        return response.json();
      })
      .then(function (payload) {
        root.dataset.table = table;
        render(root, payload);
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
      load(root, table, 0);
      return;
    }

    var prevBtn = event.target.closest("[data-data-prev]");
    if (prevBtn && !prevBtn.disabled) {
      var prevRoot = findRoot(prevBtn);
      if (!prevRoot) return;
      var prevLimit = parseInt(prevRoot.dataset.limit, 10) || 100;
      var prevOffset = Math.max(0, (parseInt(prevRoot.dataset.offset, 10) || 0) - prevLimit);
      load(prevRoot, prevRoot.dataset.table, prevOffset);
      return;
    }

    var nextBtn = event.target.closest("[data-data-next]");
    if (nextBtn && !nextBtn.disabled) {
      var nextRoot = findRoot(nextBtn);
      if (!nextRoot) return;
      var nextLimit = parseInt(nextRoot.dataset.limit, 10) || 100;
      var nextOffset = (parseInt(nextRoot.dataset.offset, 10) || 0) + nextLimit;
      load(nextRoot, nextRoot.dataset.table, nextOffset);
    }
  });
})();
