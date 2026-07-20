// "Build this connector" card on /setup/<slug>?created=1 (setup_tracker.html,
// see the `.connector-build-card` block gated on `just_created`). Three
// actions, all delegated to `document` so the card survives any future
// pdb:navigate innerHTML swap the way pdb-discreet.js's toggle does:
//   - data-connector-build-agent: fetch the create_connector prompt for this
//     slug and hand it to window.pdbAgent.ask() (opens the drawer + submits).
//   - data-connector-copy-prompt: same fetch, copy to clipboard instead.
//   - data-connector-enable-agent: POST the settings toggle, then reload --
//     pdb-agent-terminal.js's mount() only runs once at page load, so a
//     freshly-enabled drawer needs a real reload to appear.
(function () {
  "use strict";

  function requestJson(url, options) {
    if (window.pdbApp && typeof window.pdbApp.requestJson === "function") {
      return window.pdbApp.requestJson(url, options);
    }
    var opts = Object.assign({}, options);
    if (opts && Object.prototype.hasOwnProperty.call(opts, "data")) {
      opts.method = opts.method || "POST";
      opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
      opts.body = JSON.stringify(opts.data);
      delete opts.data;
    }
    return fetch(url, opts).then(function (response) {
      return response.text().then(function (text) {
        var body = null;
        if (text) {
          try {
            body = JSON.parse(text);
          } catch (e) {
            body = text;
          }
        }
        if (!response.ok) {
          var detail = body && typeof body === "object" && body.detail ? body.detail : body;
          throw new Error(detail || "Request failed (" + response.status + ")");
        }
        return body;
      });
    });
  }

  function card(el) {
    return el.closest("[data-connector-setup]");
  }

  function slugOf(el) {
    var c = card(el);
    return c ? c.getAttribute("data-connector-slug") : "";
  }

  function statusEl(el) {
    var c = card(el);
    return c ? c.querySelector("[data-connector-prompt-status]") : null;
  }

  function setStatus(el, text, isError) {
    var status = statusEl(el);
    if (!status) return;
    status.textContent = text;
    status.hidden = !text;
    status.classList.toggle("fail", Boolean(isError));
  }

  function fetchPrompt(slug) {
    return requestJson("/api/v1/agent/connector-prompt?slug=" + encodeURIComponent(slug));
  }

  function handleBuildWithAgent(button) {
    var slug = slugOf(button);
    if (!slug) return;
    button.disabled = true;
    setStatus(button, "fetching connector prompt…", false);
    fetchPrompt(slug)
      .then(function (result) {
        var prompt = result && result.prompt;
        if (!prompt) throw new Error("empty prompt");
        if (!window.pdbAgent || typeof window.pdbAgent.ask !== "function") {
          throw new Error("agent terminal not available on this page");
        }
        setStatus(button, "sent to agent terminal", false);
        return window.pdbAgent.ask(prompt);
      })
      .catch(function (error) {
        setStatus(button, "failed: " + (error.message || error), true);
      })
      .finally(function () {
        button.disabled = false;
      });
  }

  function handleCopyPrompt(button) {
    var slug = slugOf(button);
    if (!slug) return;
    var original = button.textContent;
    button.disabled = true;
    fetchPrompt(slug)
      .then(function (result) {
        var prompt = result && result.prompt;
        if (!prompt) throw new Error("empty prompt");
        return navigator.clipboard.writeText(prompt);
      })
      .then(function () {
        button.textContent = "copied";
        window.setTimeout(function () {
          button.textContent = original;
        }, 1500);
      })
      .catch(function (error) {
        setStatus(button, "failed: " + (error.message || error), true);
      })
      .finally(function () {
        button.disabled = false;
      });
  }

  function handleEnableAgent(button) {
    button.disabled = true;
    setStatus(button, "enabling…", false);
    requestJson("/api/v1/settings/agent-terminal", { data: { enabled: true } })
      .then(function () {
        setStatus(button, "enabled — reloading…", false);
        window.location.reload();
      })
      .catch(function (error) {
        setStatus(button, "failed: " + (error.message || error), true);
        button.disabled = false;
      });
  }

  function init() {
    document.addEventListener("click", function (event) {
      var target = event.target instanceof Element ? event.target : null;
      if (!target) return;
      var buildBtn = target.closest("[data-connector-build-agent]");
      if (buildBtn) {
        event.preventDefault();
        handleBuildWithAgent(buildBtn);
        return;
      }
      var copyBtn = target.closest("[data-connector-copy-prompt]");
      if (copyBtn) {
        event.preventDefault();
        handleCopyPrompt(copyBtn);
        return;
      }
      var enableBtn = target.closest("[data-connector-enable-agent]");
      if (enableBtn) {
        event.preventDefault();
        handleEnableAgent(enableBtn);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
