// Dashboard config editor: the "Edit dashboard" button (dashboard.html's
// .page-head) toggles a server-rendered panel (#pdb-dash-edit-panel) listing
// every non-auto viz -- currently-enabled ones first (dashboard order),
// then everything else grouped by tracker (see http.py's
// _dashboard_edit_panel and the dash_edit_row macro in _macros.html).
//
// Reorder mechanism: drag-to-reorder is out of scope, so each row gets
// Up/Down buttons (.dash-edit-move) that swap the row with its nearest
// sibling *row* (group-header dividers are skipped, see moveRow below).
// On Save, every checked row's slug is collected top-to-bottom -- rows
// render enabled-first then disabled-grouped-by-tracker, so newly-checked
// rows land at the end of the order unless the user moves them with the
// arrows first.
//
// All handlers are delegated on `document` (click/submit) rather than bound
// to specific elements, so they keep working after pdb-nav.js swaps
// `.content > main` for a fresh copy of dashboard.html -- this script itself
// lives in base.html's <head> and only runs once per full page load, but
// the elements it looks up by id are re-queried on every event.
(function () {
  "use strict";

  const PANEL_ID = "pdb-dash-edit-panel";
  const TOGGLE_ID = "pdb-dash-edit-toggle";
  const ROWS_ID = "pdb-dash-edit-rows";
  const CANCEL_ID = "pdb-dash-edit-cancel";
  const ERROR_ID = "pdb-dash-edit-error";

  // Snapshot of the rows panel's HTML taken when it's opened, restored
  // verbatim on Cancel so an in-progress (unsaved) reorder/check doesn't
  // stick around the next time the panel is opened.
  let savedRowsHTML = null;

  function panelEl() {
    return document.getElementById(PANEL_ID);
  }

  function rowsEl() {
    return document.getElementById(ROWS_ID);
  }

  function hideError() {
    const err = document.getElementById(ERROR_ID);
    if (err) {
      err.hidden = true;
      err.textContent = "";
    }
  }

  function showError(message) {
    const err = document.getElementById(ERROR_ID);
    if (!err) return;
    err.hidden = false;
    err.textContent = message;
  }

  function openPanel(toggle) {
    const panel = panelEl();
    const rows = rowsEl();
    if (!panel || !rows) return;
    savedRowsHTML = rows.innerHTML;
    panel.hidden = false;
    panel.scrollIntoView({ block: "nearest" });
    if (toggle) toggle.setAttribute("aria-expanded", "true");
    hideError();
  }

  function closePanel(toggle) {
    const panel = panelEl();
    if (!panel) return;
    panel.hidden = true;
    if (toggle) toggle.setAttribute("aria-expanded", "false");
  }

  // Swap `row` with the nearest preceding/following element that is itself
  // a row (data-dash-edit-row) -- skips over .dash-edit-group-head divider
  // elements so arrows always land on another checkbox row, never a label.
  function moveRow(row, direction) {
    if (direction === "up") {
      let prev = row.previousElementSibling;
      while (prev && !prev.hasAttribute("data-dash-edit-row")) {
        prev = prev.previousElementSibling;
      }
      if (prev) row.parentNode.insertBefore(row, prev);
    } else if (direction === "down") {
      let next = row.nextElementSibling;
      while (next && !next.hasAttribute("data-dash-edit-row")) {
        next = next.nextElementSibling;
      }
      if (next) row.parentNode.insertBefore(next, row);
    }
  }

  function collectSlugs() {
    const rows = rowsEl();
    if (!rows) return [];
    const slugs = [];
    rows.querySelectorAll("[data-dash-edit-row]").forEach(function (row) {
      const check = row.querySelector("[data-dash-edit-check]");
      if (check && check.checked) slugs.push(row.getAttribute("data-slug"));
    });
    return slugs;
  }

  async function save(form) {
    hideError();
    const saveBtn = form.querySelector('button[type="submit"]');
    const originalLabel = saveBtn ? saveBtn.textContent : null;
    if (saveBtn) {
      saveBtn.disabled = true;
      saveBtn.textContent = "Saving…";
    }

    try {
      const r = await fetch("/api/v1/dashboard", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ viz: collectSlugs() }),
      });
      let body = {};
      try {
        body = await r.json();
      } catch {
        // non-JSON error body -- fall through to the status-based message below
      }
      if (!r.ok) {
        throw new Error((body && body.detail) || `save failed (${r.status})`);
      }
      window.location.reload();
    } catch (err) {
      if (saveBtn) {
        saveBtn.disabled = false;
        saveBtn.textContent = originalLabel;
      }
      showError((err && err.message) || "save failed");
    }
  }

  document.addEventListener("click", function (event) {
    const toggle = event.target.closest("#" + TOGGLE_ID);
    if (toggle) {
      const panel = panelEl();
      if (panel && !panel.hidden) {
        closePanel(toggle);
      } else {
        openPanel(toggle);
      }
      return;
    }

    const cancel = event.target.closest("#" + CANCEL_ID);
    if (cancel) {
      const rows = rowsEl();
      if (rows && savedRowsHTML !== null) rows.innerHTML = savedRowsHTML;
      closePanel(document.getElementById(TOGGLE_ID));
      return;
    }

    const moveBtn = event.target.closest(".dash-edit-move");
    if (moveBtn) {
      const row = moveBtn.closest("[data-dash-edit-row]");
      if (row) moveRow(row, moveBtn.dataset.move);
    }
  });

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("[data-dash-edit-form]");
    if (!form) return;
    event.preventDefault();
    save(form);
  });
})();
