// Shared inline-JS behavior for the per-tracker settings page
// (setup_tracker.html) and the step-per-page first-run wizard
// (setup_wizard.html): copy-the-redirect-URI button, install_hooks /
// verify_hooks / action (TrackerActionStep) steps.
//
// Extracted out of setup_tracker.html's inline <script> so both templates
// share one implementation instead of hand-copying it. Loaded via a plain
// `<script src="...">` tag (not `defer`/module) from inside each template's
// content block -- pdb-nav.js's executeScripts() re-creates *every* <script>
// element found in a freshly-swapped <main> (including ones with a `src`
// attribute, which the browser re-fetches from cache and re-executes), so
// this file's bootstrap calls (the two `document.querySelectorAll(...)
// .forEach(...)` calls at the bottom) re-run on every same-origin
// client-side navigation into a page that includes this tag, exactly like
// the inline version they replaced.
//
// Redeclaring these functions and re-running the bootstrap calls on repeat
// execution is safe and idempotent -- see pdb-nav.js's executeScripts()
// comment for why that's the documented contract inline <script>s on these
// pages rely on.

async function copyRedirectUri(button, uri) {
  const original = button.textContent;
  try {
    await navigator.clipboard.writeText(uri);
    button.textContent = "copied";
  } catch (err) {
    button.textContent = "copy failed";
  } finally {
    window.setTimeout(() => { button.textContent = original; }, 1500);
  }
}

async function installHooks(button, tracker) {
  button.disabled = true;
  const out = button.parentElement.querySelector('.action-output');
  out.hidden = false;
  out.textContent = "installing…";
  out.classList.remove("error");   // reset stale error styling on retry
  try {
    const r = await fetch(`/api/v1/trackers/${tracker}/actions/install_hooks`, {method: "POST"});
    const body = await r.json();
    out.textContent = body.message || JSON.stringify(body);
    if (!body.ok) out.classList.add("error");
    document.querySelectorAll(`[data-step-type="verify_hooks"]`).forEach(refreshHookStatus);
  } catch (err) {
    out.textContent = `Daemon not reachable — run \`personal-db daemon install\``;
    out.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

async function refreshHookStatus(el) {
  const tracker = el.dataset.tracker;
  const badge = el.querySelector(".hook-status-badge");
  if (!badge) return;
  try {
    const r = await fetch(`/api/v1/trackers/${tracker}/actions/verify_hooks`, {method: "POST"});
    const body = await r.json();
    badge.textContent = body.ours_present ? "✓ installed" : "✗ not installed";
    badge.className = `hook-status-badge ${body.ours_present ? "ok" : "warn"}`;
  } catch {
    badge.textContent = "daemon unreachable";
    badge.className = "hook-status-badge error";
  }
}

document.querySelectorAll(`[data-step-type="verify_hooks"]`).forEach(refreshHookStatus);

function summarizeActionStatus(body) {
  if (Number.isInteger(body.item_count)) {
    const label = body.item_count === 1 ? "1 linked Item" : `${body.item_count} linked Items`;
    const backups = Number.isInteger(body.backup_count) ? ` · ${body.backup_count} backups` : "";
    const names = Array.isArray(body.items)
      ? body.items.map((item) => item.institution_name || item.institution_id || item.item_id).filter(Boolean)
      : [];
    if (body.item_count > 0) {
      return `✓ ${label}${backups}${names.length ? " · " + names.join(", ") : ""}`;
    }
    return `— no linked Items yet${backups}`;
  }
  return body.message || JSON.stringify(body);
}

async function refreshTrackerActionStatus(el) {
  const tracker = el.dataset.tracker;
  const statusAction = el.dataset.statusAction;
  const badge = el.querySelector(".tracker-action-status");
  if (!badge || !statusAction) return;
  try {
    const r = await fetch(`/api/v1/trackers/${tracker}/actions/${statusAction}`, {method: "POST"});
    const body = await r.json();
    badge.textContent = summarizeActionStatus(body);
    badge.className = `tracker-action-status ${body.ok ? "ok" : "warn"}`;
  } catch {
    badge.textContent = "daemon unreachable";
    badge.className = "tracker-action-status error";
  }
}

async function runTrackerAction(button, tracker, action) {
  button.disabled = true;
  const section = button.closest(".setup-step");
  const out = section.querySelector('.action-output');
  out.hidden = false;
  out.textContent = "running…";
  out.classList.remove("error");
  try {
    const r = await fetch(`/api/v1/trackers/${tracker}/actions/${action}`, {method: "POST"});
    const body = await r.json();
    out.textContent = body.message || JSON.stringify(body, null, 2);
    if (body.url) window.open(body.url, "_blank", "noopener");
    if (!body.ok) out.classList.add("error");
    await refreshTrackerActionStatus(section);
  } catch {
    out.textContent = `Daemon not reachable — run \`personal-db daemon install\``;
    out.classList.add("error");
  } finally {
    button.disabled = false;
  }
}

document.querySelectorAll(`[data-step-type="action"]`).forEach(refreshTrackerActionStatus);
