// Passive native update checks are intentionally silent.  When the desktop
// shell finds one, it retains a tiny status payload and emits an event; this
// script turns that into a persistent, accessible sidebar action.  The state
// query on load is essential: dashboard navigation/reloads may happen after
// the native event was emitted.
(function () {
  "use strict";

  var button = document.getElementById("pdb-update-ready");
  if (!button) return;

  function tauriInvoke(command) {
    var tauri = window.__TAURI__;
    if (!tauri || !tauri.core || typeof tauri.core.invoke !== "function") {
      return Promise.reject(new Error("Native app bridge is unavailable."));
    }
    return tauri.core.invoke(command);
  }

  function render(status) {
    var available = Boolean(status && status.available);
    var version = available && status.version ? String(status.version) : "";
    button.hidden = !available;
    button.dataset.version = version;
    button.setAttribute("aria-hidden", available ? "false" : "true");
    button.setAttribute(
      "aria-label",
      version ? "Update " + version + " ready to install" : "Update ready to install"
    );
    button.title = version ? "Install PersonalDB " + version : "Install available PersonalDB update";
    button.querySelector("[data-update-ready-label]").textContent = version
      ? "Update " + version + " ready"
      : "Update ready";
  }

  function refresh() {
    tauriInvoke("get_update_status").then(render).catch(function () {
      // A normal browser/CLI dashboard deliberately has no native bridge;
      // the button stays hidden and does not leave a failed-control artifact.
      render(null);
    });
  }

  button.addEventListener("click", function () {
    if (button.disabled) return;
    var label = button.querySelector("[data-update-ready-label]");
    var original = label.textContent;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    label.textContent = "Opening update…";
    tauriInvoke("start_update_install")
      .catch(function () {
        // Keep the indicator available when the shell declined the request
        // (for example, a manual update check is already running).
      })
      .finally(function () {
        button.disabled = false;
        button.removeAttribute("aria-busy");
        label.textContent = original;
      });
  });

  refresh();
  document.addEventListener("pdb:navigate", refresh);

  // Update an already-open dashboard immediately. `listen` is optional so
  // the regular browser dashboard remains a fully functional, quiet page.
  var events = window.__TAURI__ && window.__TAURI__.event;
  if (events && typeof events.listen === "function") {
    events.listen("pdb://update-ready", function (event) {
      render(event && event.payload);
    }).catch(function () {});
  }
})();
