// Progressive enhancement for the .pdb-sync-form / .pdb-sync-btn pair
// rendered by the refresh_btn macro (_macros.html). Without this script the
// form still works as a plain POST /sync/<tracker> + full-page redirect
// (see routes/sync.py's `post_sync`); with it, the button fetch()es the
// JSON API instead (POST /api/v1/sync/<tracker>) and reloads on success, so
// the page doesn't flash through an extra redirect hop.
//
// Also handles the page-level "Sync all due" button (sync_all_btn macro,
// [data-sync-all]) on /health, which POSTs /api/v1/sync_due -- see
// handleSyncAll below.
(function () {
  "use strict";

  const MAX_ERROR_LEN = 140;
  let statusId = 0;

  // Tauri's WebKit view can defer painting a click handler's DOM changes until
  // the next task.  A large local sync (notably a first iMessage import) can
  // immediately put enough pressure on the machine for that next paint to be
  // noticeably delayed. Yield after setting the pending state so the user
  // sees feedback before the request starts.
  function waitForPaint() {
    return new Promise((resolve) => {
      requestAnimationFrame(() => setTimeout(resolve, 0));
    });
  }

  // The inline error span (.pdb-sync-error) is inserted as the button's next
  // sibling within the form on failure, and removed on the next submit
  // attempt (cleared eagerly) or successful sync (page reloads anyway, but
  // clearing keeps state consistent if that ever changes).
  function clearError(button) {
    const span = button.nextElementSibling;
    if (span && span.classList.contains("pdb-sync-error")) {
      span.remove();
    }
  }

  function showError(button, message) {
    const truncated =
      message.length > MAX_ERROR_LEN
        ? `${message.slice(0, MAX_ERROR_LEN)}…`
        : message;
    let span = button.nextElementSibling;
    if (!span || !span.classList.contains("pdb-sync-error")) {
      span = document.createElement("span");
      span.className = "pdb-sync-error";
      button.insertAdjacentElement("afterend", span);
    }
    span.textContent = truncated;
    span.title = message;
  }

  function clearStatus(button) {
    const form = button.closest("form");
    const status = form && form.querySelector(".pdb-sync-status");
    if (status) status.remove();
    if (form) {
      form.classList.remove("is-syncing");
      form.removeAttribute("aria-busy");
    }
    button.classList.remove("is-loading");
    button.removeAttribute("aria-busy");
    button.removeAttribute("aria-describedby");
  }

  async function showPending(button, message) {
    const form = button.closest("form");
    if (form) {
      form.classList.add("is-syncing");
      form.setAttribute("aria-busy", "true");
    }
    button.disabled = true;
    button.classList.add("is-loading");
    button.setAttribute("aria-busy", "true");

    const status = document.createElement("span");
    status.className = "pdb-sync-status";
    status.id = `pdb-sync-status-${++statusId}`;
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    status.textContent = message;
    button.insertAdjacentElement("afterend", status);
    button.setAttribute("aria-describedby", status.id);

    await waitForPaint();
  }

  // "Sync all due" (health.html, [data-sync-all] via the sync_all_btn macro)
  // -- POSTs the daemon's sync-everything-due route directly, since (unlike
  // single-tracker sync) there's no plain-form browser route for it. In-flight
  // state is "Syncing all…"; on success the page reloads and the tracker rows
  // themselves show the per-tracker outcome, so there's no separate summary
  // to render here.
  async function handleSyncAll(form) {
    const button = form.querySelector("[data-sync-all]");
    if (!button) return;

    const originalLabel = button.textContent;
    button.textContent = "Syncing all…";
    button.classList.remove("error");
    button.removeAttribute("title");
    clearError(button);
    await showPending(button, "Syncing all due trackers…");

    try {
      const r = await fetch("/api/v1/sync_due", { method: "POST" });
      let body = {};
      try {
        body = await r.json();
      } catch {
        // non-JSON error body -- fall through to the status-based message below
      }
      if (!r.ok) {
        throw new Error((body && body.detail) || `sync all failed (${r.status})`);
      }
      window.location.reload();
    } catch (err) {
      button.disabled = false;
      button.textContent = originalLabel;
      clearStatus(button);
      button.classList.add("error");
      const message = (err && err.message) || "sync all failed";
      button.title = message;
      showError(button, message);
    }
  }

  async function handleSubmit(event) {
    const syncAllForm = event.target.closest("[data-sync-all]");
    if (syncAllForm) {
      event.preventDefault();
      handleSyncAll(syncAllForm);
      return;
    }

    const form = event.target.closest(".pdb-sync-form");
    if (!form) return; // not a sync form submit -- ignore
    const tracker = form.dataset.tracker;
    const button = form.querySelector(".pdb-sync-btn");
    if (!tracker || !button) return; // fall back to the plain form POST

    event.preventDefault();
    const originalLabel = button.textContent;
    button.textContent = "Syncing…";
    button.classList.remove("error");
    button.removeAttribute("title");
    clearError(button);
    await showPending(button, `Syncing ${tracker}…`);

    try {
      const r = await fetch(`/api/v1/sync/${encodeURIComponent(tracker)}`, {
        method: "POST",
      });
      let body = {};
      try {
        body = await r.json();
      } catch {
        // non-JSON error body -- fall through to the status-based message below
      }
      if (!r.ok || !body.ok) {
        throw new Error(body.detail || `sync failed (${r.status})`);
      }
      window.location.reload();
    } catch (err) {
      button.disabled = false;
      button.textContent = originalLabel;
      clearStatus(button);
      button.classList.add("error");
      const message = (err && err.message) || "sync failed";
      button.title = message;
      showError(button, message);
    }
  }

  // Delegated on `document` (rather than bound per-form) so sync buttons
  // rendered into a page swapped in by pdb-nav.js still work without
  // needing to be re-bound on every navigation -- the "submit" event
  // bubbles, so this fires for forms that didn't exist when this script
  // first ran.
  document.addEventListener("submit", handleSubmit);
})();
