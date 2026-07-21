// Native PersonalDB-app actions exposed on Settings when the macOS menu-bar
// icon is hidden in the system's overflow area.  The buttons are rendered
// only by a bundled app (`app_managed`), then call the same Rust dispatcher
// as the tray items instead of reimplementing privileged work in the page.
(function () {
  "use strict";

  function invoke(action) {
    var tauri = window.__TAURI__;
    if (!tauri || !tauri.core || typeof tauri.core.invoke !== "function") {
      return Promise.reject(
        new Error("This control is available in the PersonalDB desktop app.")
      );
    }
    return tauri.core.invoke("run_tray_action", { action: action });
  }

  function statusElement(button) {
    var section = button.closest("[data-app-actions]");
    return section && section.querySelector("[data-shell-action-status]");
  }

  function labelFor(action) {
    if (action === "sync_now") return "Sync started. You’ll receive a notification when it finishes.";
    if (action === "install_cli") return "Installing the command line tool…";
    if (action.indexOf("connect_") === 0) return "Connecting AI app…";
    if (action === "check_updates") return "Checking for updates…";
    return "Done.";
  }

  function refreshStartAtLogin() {
    var button = document.querySelector('[data-shell-action="toggle_start_at_login"]');
    if (!button) return;
    invoke("start_at_login_status")
      .then(function (result) {
        if (!result || typeof result.startAtLogin !== "boolean") return;
        button.textContent = result.startAtLogin ? "✓ Start at Login" : "Start at Login";
        button.setAttribute("aria-pressed", result.startAtLogin ? "true" : "false");
      })
      .catch(function () {
        // The button remains usable and will report a useful error if clicked.
      });
  }

  document.addEventListener("click", function (event) {
    var target = event.target instanceof Element ? event.target.closest("[data-shell-action]") : null;
    if (!target) return;

    var action = target.getAttribute("data-shell-action");
    if (!action) return;
    if (action === "quit" && !window.confirm("Quit PersonalDB? Background sync will stop until you reopen it.")) {
      return;
    }

    var originalLabel = target.textContent;
    var status = statusElement(target);
    target.disabled = true;
    if (status) status.textContent = "Working…";

    invoke(action)
      .then(function (result) {
        if (action === "toggle_start_at_login" && result && typeof result.startAtLogin === "boolean") {
          target.textContent = result.startAtLogin ? "✓ Start at Login" : "Start at Login";
          target.setAttribute("aria-pressed", result.startAtLogin ? "true" : "false");
          if (status) status.textContent = result.startAtLogin ? "PersonalDB will start at login." : "PersonalDB will not start at login.";
        } else if (status) {
          status.textContent = labelFor(action);
        }
      })
      .catch(function (error) {
        target.textContent = originalLabel;
        if (status) status.textContent = (error && error.message) || String(error);
      })
      .finally(function () {
        // `quit` normally terminates before this runs; restore other controls
        // once their background native work has been scheduled.
        target.disabled = false;
      });
  });

  refreshStartAtLogin();
  document.addEventListener("pdb:navigate", refreshStartAtLogin);
})();
