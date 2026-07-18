// Progressive enhancement for the .pdb-sync-form / .pdb-sync-btn pair
// rendered by the refresh_btn macro (_macros.html). Without this script the
// form still works as a plain POST /sync/<tracker> + full-page redirect
// (see routes/sync.py's `post_sync`); with it, the button fetch()es the
// JSON API instead (POST /api/v1/sync/<tracker>) and reloads on success, so
// the page doesn't flash through an extra redirect hop.
(function () {
  "use strict";

  async function handleSubmit(event) {
    const form = event.target.closest(".pdb-sync-form");
    if (!form) return; // not a sync form submit -- ignore
    const tracker = form.dataset.tracker;
    const button = form.querySelector(".pdb-sync-btn");
    if (!tracker || !button) return; // fall back to the plain form POST

    event.preventDefault();
    const originalLabel = button.textContent;
    button.disabled = true;
    button.textContent = "Syncing…";
    button.classList.remove("error");
    button.removeAttribute("title");

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
      button.classList.add("error");
      button.title = (err && err.message) || "sync failed";
    }
  }

  // Delegated on `document` (rather than bound per-form) so sync buttons
  // rendered into a page swapped in by pdb-nav.js still work without
  // needing to be re-bound on every navigation -- the "submit" event
  // bubbles, so this fires for forms that didn't exist when this script
  // first ran.
  document.addEventListener("submit", handleSubmit);
})();
