//! Client for the personal_db daemon's HTTP API, mirroring
//! `services/daemon/client.py` on the Python side. Kept deliberately small:
//! this shell only needs health, the OTC browser bootstrap, and sync_due.
//!
//! Security note: the daemon token is read from disk and used exclusively
//! from Rust (reqwest, never fetch() in a WebView page). The one thing that
//! ever reaches the WebView is the single-use, 30-second OTC bootstrap URL
//! minted by `mint_otc` -- the long-lived token itself never enters JS-land
//! or a page's address bar.

use std::env;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::Duration;

use serde::Deserialize;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_notification::NotificationExt;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const DEFAULT_BASE_URL: &str = "http://127.0.0.1:8765";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(300);
const WINDOW_LABEL: &str = "main";
/// Name passed to `app.shell().sidecar(..)` -- must match the *basename* of
/// `bundle.externalBin`'s entry in tauri.conf.json (the target-triple suffix
/// is resolved by Tauri itself, and the full relative path is only needed
/// in tauri.conf.json / capabilities/default.json's `shell:allow-execute`
/// permission, not here).
const SIDECAR_NAME: &str = "personal-db-daemon";
/// How long to wait for a just-spawned sidecar to answer `/api/v1/health`
/// before giving up and falling back to the guidance page. Cold-starting a
/// frozen CPython + importing every tracker's deps is slower than a plain
/// process spawn but should still land well under this on any machine that
/// can run the app at all.
const SIDECAR_START_TIMEOUT: Duration = Duration::from_secs(20);
const SIDECAR_POLL_INTERVAL: Duration = Duration::from_millis(500);
/// Tray icon id (see `main.rs`'s `TrayIconBuilder::with_id`) -- shares the
/// string "main" with `WINDOW_LABEL` below by coincidence only; tray ids and
/// window labels are separate Tauri namespaces.
const TRAY_ID: &str = "main";
/// How often `main.rs`'s setup-time poll loop calls `poll_health_status`.
pub const HEALTH_POLL_INTERVAL: Duration = Duration::from_secs(120);
/// Cap on how many failing tracker names get spelled out in the tray
/// tooltip before collapsing the rest into a "+N more" suffix.
const MAX_TOOLTIP_NAMES: usize = 4;

/// Holds the sidecar daemon this app spawned (if any), so the `CommandChild`
/// isn't silently dropped (that wouldn't kill the process -- Tauri's
/// `CommandChild` mirrors `std::process::Child` in not killing on drop --
/// but holding it keeps the option open for a future explicit shutdown, and
/// its presence is what stops `try_start_sidecar` from spawning a second
/// daemon on top of one that's still coming up).
#[derive(Default)]
pub struct SidecarState(pub Mutex<Option<CommandChild>>);

/// Last-known daemon reachability, as observed by `poll_health_status`.
/// `None` means "no poll has completed yet"; `Some(true)`/`Some(false)`
/// record the last observed state so a poll that finds the daemon
/// unreachable can tell a fresh down-transition (worth auto-restarting and
/// notifying about) from "still down since last time" (nothing new to do --
/// avoids re-spawning a sidecar or re-popping the guidance window every
/// poll tick while the daemon stays down).
#[derive(Default)]
pub struct HealthState(pub Mutex<Option<bool>>);

/// The daemon `app_version` (as reported by the last health poll) that a
/// stale-daemon restart has already been *attempted* for -- see
/// `check_version_drift`. Guards against restart-looping: once we've tried
/// once for a given stale version string, later polls that still see that
/// same version only log, they don't try again. A version-drift *toward a
/// newer* observed version (e.g. the restart worked, or the daemon updated
/// itself independently) naturally clears this guard's relevance because the
/// comparison is keyed on the exact version string, not a boolean latch.
#[derive(Default)]
pub struct VersionDriftState(pub Mutex<Option<String>>);

/// Records a non-current frozen sidecar for which this shell has already
/// attempted recovery. Unlike `VersionDriftState`, the v0.1.14 incident can
/// report the *same* version as the new shell, so its executable provenance
/// (and listener PID) is the recovery key.
#[derive(Default)]
pub struct ProvenanceRecoveryState(pub Mutex<Option<String>>);

#[derive(Debug, Deserialize)]
struct OtcResponse {
    otc: String,
}

/// `GET /api/v1/health` response shape this client cares about. Every field
/// is `#[serde(default)]` so an older daemon build (or a health payload from
/// before this field existed) deserializes fine with empty/default values
/// instead of failing the whole poll.
#[derive(Debug, Deserialize, Default)]
struct HealthPayload {
    #[serde(default)]
    app_version: String,
    #[serde(default)]
    repeated_sync_failures: Vec<String>,
}

/// Mirrors `services/daemon/client.py::base_url()`: `PERSONAL_DB_DAEMON_URL`
/// if set (the whole base URL, not just a port), else the default loopback
/// address on the daemon's default port.
fn base_url() -> String {
    env::var("PERSONAL_DB_DAEMON_URL").unwrap_or_else(|_| DEFAULT_BASE_URL.to_string())
}

/// Mirrors `services/daemon/client.py::_root()` / `cli.state.get_root()`:
/// `PERSONAL_DB_ROOT` if set, else `~/personal_db`.
fn root_dir() -> PathBuf {
    if let Ok(root) = env::var("PERSONAL_DB_ROOT") {
        return PathBuf::from(root);
    }
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("personal_db")
}

/// Extracts the port `base_url()` resolved to (default 8765, or whatever
/// `PERSONAL_DB_DAEMON_URL` specifies) so a spawned sidecar binds the same
/// port the rest of this client will then poll/talk to.
fn daemon_port(base: &str) -> u16 {
    url::Url::parse(base)
        .ok()
        .and_then(|u| u.port_or_known_default())
        .unwrap_or(8765)
}

fn token_path() -> PathBuf {
    root_dir().join("state").join("daemon.token")
}

/// Reads `<root>/state/daemon.token`, mirroring `core/daemon_token.py::read_token`.
fn read_token() -> Option<String> {
    let text = std::fs::read_to_string(token_path()).ok()?;
    let trimmed = text.trim();
    if trimmed.is_empty() {
        None
    } else {
        Some(trimmed.to_string())
    }
}

fn http_client(timeout: Duration) -> Result<reqwest::Client, String> {
    reqwest::Client::builder()
        .timeout(timeout)
        .build()
        .map_err(|e| format!("failed to build http client: {e}"))
}

/// `GET /api/v1/health` -- exempt from token auth on the daemon side (see
/// `services/daemon/auth.py::EXEMPT_ROUTES`), so no Authorization header is
/// needed here, matching the plan's "GETs /api/v1/health" step.
///
/// On success, also best-effort parses the body for `app_version` and
/// returns it. Reachability and version are deliberately kept as one
/// `Result`, not two: every existing caller (`try_start_sidecar`'s poll
/// loop, `wait_until_unreachable`) only ever asked `.is_ok()`/`.is_err()` of
/// this, which still works unchanged against `Result<Option<String>, _>`. A
/// body that fails to parse (or a pre-handshake daemon that never sent
/// `app_version` at all) yields `Ok(None)` rather than failing the health
/// check outright -- version is orthogonal to reachability, and
/// `open_page`'s version gate (the one caller that inspects the payload) is
/// what turns a `None`/mismatched version into user-facing guidance instead
/// of a failed health check.
async fn check_health(base: &str) -> Result<Option<String>, String> {
    let client = http_client(HEALTH_TIMEOUT)?;
    let resp = client
        .get(format!("{base}/api/v1/health"))
        .send()
        .await
        .map_err(|e| format!("daemon unreachable at {base}: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("daemon at {base} returned HTTP {}", resp.status()));
    }
    let version = resp
        .json::<HealthPayload>()
        .await
        .ok()
        .map(|payload| payload.app_version)
        .filter(|v| !v.is_empty());
    Ok(version)
}

/// `POST /api/v1/auth/otc` with the daemon token -- mints a single-use,
/// 30-second code (see `services/daemon/otc.py`) so the long-lived token
/// itself never has to appear in a URL the WebView navigates to.
async fn mint_otc(base: &str, token: &str) -> Result<String, String> {
    let client = http_client(REQUEST_TIMEOUT)?;
    let resp = client
        .post(format!("{base}/api/v1/auth/otc"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| format!("otc request failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("otc request returned HTTP {}", resp.status()));
    }
    let parsed: OtcResponse = resp
        .json()
        .await
        .map_err(|e| format!("otc response was not valid JSON: {e}"))?;
    Ok(parsed.otc)
}

fn close_existing_window(app: &AppHandle) {
    if let Some(existing) = app.get_webview_window(WINDOW_LABEL) {
        let _ = existing.close();
    }
}

fn show_external(app: &AppHandle, url: &str) -> Result<(), String> {
    let parsed = url::Url::parse(url).map_err(|e| format!("bad url {url}: {e}"))?;
    // Reuse an existing window instead of close-and-recreate: recreating
    // raced through a zero-window state (see the ExitRequested handler in
    // main.rs) and flickered. Navigation failure falls through to rebuild.
    if let Some(existing) = app.get_webview_window(WINDOW_LABEL) {
        if existing.navigate(parsed.clone()).is_ok() {
            let _ = existing.show();
            let _ = existing.set_focus();
            return Ok(());
        }
        let _ = existing.close();
    }
    WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::External(parsed))
        .title("PersonalDB")
        .inner_size(1180.0, 820.0)
        .min_inner_size(720.0, 480.0)
        .build()
        .map_err(|e| format!("failed to open window: {e}"))?;
    Ok(())
}

/// Bundled fallback page (`dist/daemon-down.html`) shown when the health
/// check fails, with the base URL and failure reason passed as query params
/// so the page can render them without talking back to Rust.
fn show_daemon_down(app: &AppHandle, base: &str, reason: &str) -> Result<(), String> {
    close_existing_window(app);
    let query = format!(
        "daemon-down.html?daemon_url={}&reason={}",
        url::form_urlencoded::byte_serialize(base.as_bytes()).collect::<String>(),
        url::form_urlencoded::byte_serialize(reason.as_bytes()).collect::<String>(),
    );
    WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::App(PathBuf::from(query)))
        .title("PersonalDB isn't running")
        .inner_size(560.0, 460.0)
        .resizable(true)
        .build()
        .map_err(|e| format!("failed to open fallback window: {e}"))?;
    Ok(())
}

/// Bundled version-mismatch guidance page (`dist/version-mismatch.html`),
/// shown by `open_page`'s version gate when `check_health` succeeds (some
/// personal_db daemon answered) but it isn't running this build's exact
/// version -- see the long comment on that gate for the incident this
/// exists for. Follows the same query-param, no-Rust-round-trip mechanism
/// `show_daemon_down` uses for `daemon-down.html`.
fn show_version_mismatch(
    app: &AppHandle,
    base: &str,
    daemon_version: Option<&str>,
    next_path: &str,
) -> Result<(), String> {
    close_existing_window(app);

    // Pre-formatted so the page's JS can drop these straight into the DOM
    // with no version-vs-fallback branching of its own.
    let daemon_version_label = match daemon_version {
        Some(v) => format!("v{v}"),
        None => "pre-0.1.5, no version".to_string(),
    };
    let app_version_label = format!("v{SHELL_VERSION}");

    // The "Continue anyway" link's target is the *manual* `/auth` page, not
    // an OTC bootstrap URL: an OTC is single-use and expires in 30 seconds
    // (see `mint_otc`'s doc comment), so baking one into a link the user
    // might sit on this page before clicking would just be dead by the time
    // they click it. The manual auth page has no such expiry.
    let next = url::form_urlencoded::byte_serialize(next_path.as_bytes()).collect::<String>();
    let continue_url = format!("{base}/auth?next={next}");

    let query = format!(
        "version-mismatch.html?daemon_url={}&daemon_version={}&app_version={}&continue_url={}",
        url::form_urlencoded::byte_serialize(base.as_bytes()).collect::<String>(),
        url::form_urlencoded::byte_serialize(daemon_version_label.as_bytes()).collect::<String>(),
        url::form_urlencoded::byte_serialize(app_version_label.as_bytes()).collect::<String>(),
        url::form_urlencoded::byte_serialize(continue_url.as_bytes()).collect::<String>(),
    );
    WebviewWindowBuilder::new(app, WINDOW_LABEL, WebviewUrl::App(PathBuf::from(query)))
        .title("PersonalDB version mismatch")
        .inner_size(620.0, 560.0)
        .resizable(true)
        .build()
        .map_err(|e| format!("failed to open version-mismatch window: {e}"))?;
    Ok(())
}

/// Environment variables that must never leak from the shell's own
/// environment into the frozen daemon sidecar: Python interpreter overrides
/// (a dev checkout's PYTHONPATH/venv would shadow the bundle's own
/// site-packages -- see the incident note at the spawn site) and TLS trust
/// overrides that `requests`/curl honor over the bundle's certifi. PYTHONHOME
/// is also stripped here even though the spawn re-sets it explicitly --
/// belt-and-suspenders so the explicit value is the only one that can win.
const SIDECAR_ENV_DENYLIST: &[&str] = &[
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "PYTHONEXECUTABLE",
    "VIRTUAL_ENV",
    "__PYVENV_LAUNCHER__",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
];

fn env_var_is_sidecar_safe(key: &str) -> bool {
    !SIDECAR_ENV_DENYLIST.contains(&key)
}

/// The parent process's environment minus `SIDECAR_ENV_DENYLIST` -- what the
/// sidecar spawn rebuilds its child env from (after `env_clear()`), so PATH/
/// HOME/locale survive but developer-Python and TLS overrides never do.
/// Non-UTF-8 entries are dropped (`env::vars` skips them); nothing the
/// daemon needs is non-UTF-8.
fn sanitized_parent_env() -> Vec<(String, String)> {
    env::vars()
        .filter(|(k, _)| env_var_is_sidecar_safe(k))
        .collect()
}

/// Attempts to spawn the frozen daemon payload (`packaging/freeze-daemon.sh`,
/// wired in as `bundle.externalBin` in tauri.conf.json) when nothing answers
/// health at `base` yet. Returns `true` once the spawned daemon answers
/// `/api/v1/health` reporting *exactly* this shell's own `SHELL_VERSION`,
/// `false` if there's no sidecar for this build (e.g. a `tauri dev` run
/// without the payload at the expected relative path -- not an error, just
/// "nothing to spawn"), if it's already been spawned once this session, or
/// if it never reported a matching version within `SIDECAR_START_TIMEOUT`.
///
/// The success check used to accept ANY successful `check_health` -- but
/// that's exactly what let the v0.1.8 -> v0.1.9 self-update incident slip
/// past this function: the OLD sidecar (spawned by the pre-update shell
/// process, still holding the port after the update relaunched the shell but
/// not the daemon) answered health just fine, so a restart attempt that
/// "spawned" on top of it read as success even though nothing new was
/// actually listening and the foreign, stale process was the one still
/// serving traffic. Requiring the exact version match closes that hole: only
/// a daemon that is truly this build's own freshly-spawned process can
/// satisfy it, so callers (this function's own poll loop, plus
/// `restart_stale_daemon`) can no longer mistake "something on the port
/// answered" for "our spawn worked".
///
/// Callers treat `false` the same as "daemon down" and fall back to the
/// guidance page.
async fn try_start_sidecar(app: &AppHandle, base: &str) -> bool {
    let state = app.state::<SidecarState>();
    {
        let guard = state.0.lock().unwrap();
        if guard.is_some() {
            // Already spawned once this session -- if it's still starting
            // up (or died), stacking a second daemon on the same port would
            // just fail to bind; let the caller show the guidance page
            // (whose Retry button re-runs this whole flow) instead.
            return false;
        }
    }

    let sidecar = match app.shell().sidecar(SIDECAR_NAME) {
        Ok(cmd) => cmd,
        Err(e) => {
            eprintln!("no sidecar configured for this build, not spawning one: {e}");
            return false;
        }
    };

    // The sidecar binary is now a copy of the frozen python3 Mach-O itself
    // (see packaging/freeze-daemon.sh step 4), not a wrapper script -- a
    // script's code signature is a detached xattr that Tauri's updater
    // extraction drops, which is exactly the bug this whole arrangement
    // fixes (a Mach-O's signature is embedded in the file and survives
    // that extraction). The cost: a standalone python-build-standalone
    // interpreter resolves its stdlib/site-packages from the *real path of
    // the running executable* by default, which is wrong once this binary
    // lives at Contents/MacOS/personal-db-daemon (bundle.externalBin) while
    // the `python/` tree it needs lives at the sibling Contents/Resources/python
    // (bundle.resources) -- so PYTHONHOME is set explicitly here rather than
    // left for the binary to infer. `resource_dir()` is the same call
    // `cli_install.rs::wrapper_path` uses to find `Contents/Resources/cli/`;
    // `.join("python")` mirrors tauri.conf.json's
    // `"../../packaging/build/payload/python": "python"` resource mapping.
    // PYTHONHOME alone is sufficient -- python-build-standalone's site
    // module finds `<PYTHONHOME>/lib/python3.11/site-packages` on its own;
    // no PYTHONPATH needed (verified locally against the actual frozen
    // payload). `-m personal_db` is prepended to the args since this binary
    // is bare python3 now, with no wrapper script to supply it.
    let python_home = match app.path().resource_dir() {
        Ok(dir) => dir.join("python"),
        Err(e) => {
            eprintln!(
                "failed to resolve bundle resource dir for PYTHONHOME, not spawning sidecar: {e}"
            );
            return false;
        }
    };

    let port = daemon_port(base);
    let root = root_dir();
    eprintln!(
        "daemon unreachable at {base}; spawning sidecar (root={}, port={port}, PYTHONHOME={})",
        root.display(),
        python_home.display()
    );

    // The frozen daemon must never see a developer Python environment.
    // Incident (2026-07-20): a shell launched from a session that exported
    // PYTHONPATH into a repo venv left the *bundled* python3.11 importing
    // `requests`/`certifi` from a since-deleted python3.12 site-packages --
    // every HTTPS call then died with "invalid path: .../.venv/lib/
    // python3.12/site-packages/certifi/cacert.pem". PYTHONHOME (set below)
    // pins the stdlib but does NOT override PYTHONPATH's prepended entries,
    // and requests also honors the *_CA_BUNDLE / SSL_CERT_* overrides
    // directly. So the spawn env is rebuilt from a sanitized copy of the
    // parent env instead of inherited wholesale.

    let sidecar = sidecar
        .env_clear()
        .envs(sanitized_parent_env())
        .env("PERSONAL_DB_ROOT", root.to_string_lossy().to_string())
        .env("PYTHONHOME", python_home.to_string_lossy().to_string())
        .args([
            "-m",
            "personal_db",
            "dev",
            "daemon",
            "run",
            "--port",
            &port.to_string(),
        ]);

    let child = match sidecar.spawn() {
        Ok((mut rx, child)) => {
            // Drain stdout/stderr in the background: without a reader the
            // sidecar's pipes fill up and it blocks on write(); this also
            // surfaces a first-run spawn failure in the app's own stderr
            // (Console.app / a piped log) instead of it vanishing silently.
            tauri::async_runtime::spawn(async move {
                while let Some(event) = rx.recv().await {
                    match event {
                        CommandEvent::Stdout(line) => {
                            eprint!("[daemon] {}", String::from_utf8_lossy(&line))
                        }
                        CommandEvent::Stderr(line) => {
                            eprint!("[daemon] {}", String::from_utf8_lossy(&line))
                        }
                        CommandEvent::Error(e) => eprintln!("[daemon] spawn error: {e}"),
                        CommandEvent::Terminated(payload) => {
                            eprintln!("[daemon] exited: {:?}", payload.code)
                        }
                        _ => {}
                    }
                }
            });
            child
        }
        Err(e) => {
            eprintln!("failed to spawn sidecar: {e}");
            return false;
        }
    };

    *state.0.lock().unwrap() = Some(child);

    let attempts = (SIDECAR_START_TIMEOUT.as_millis() / SIDECAR_POLL_INTERVAL.as_millis()) as u32;
    for _ in 0..attempts {
        // `Ok(Some(v)) if v == SHELL_VERSION`, not a bare `.is_ok()` -- see
        // this function's doc comment for the incident that requires this.
        if let Ok(Some(v)) = check_health(base).await {
            if v == SHELL_VERSION {
                return true;
            }
        }
        tokio::time::sleep(SIDECAR_POLL_INTERVAL).await;
    }
    eprintln!(
        "sidecar spawned but never reported v{SHELL_VERSION} within {SIDECAR_START_TIMEOUT:?}"
    );
    false
}

/// The full bootstrap flow described in the Phase 4 plan: locate the root,
/// read the token, check daemon health, and either navigate to the
/// authenticated target page (via the OTC bootstrap URL, with `next` set to
/// `next_path`) or show the bundled "daemon not running" guidance page.
/// Invoked (via `open_dashboard` and `open_page`'s other callers) on first
/// launch and from the tray's "Open Dashboard"/"Health" items, and from the
/// fallback page's Retry button (via the `open_dashboard` Tauri command).
///
/// If the initial health check fails, this now also tries to spawn the
/// bundled sidecar daemon (see `try_start_sidecar`) before falling back to
/// the guidance page -- a signed release build should self-heal a
/// not-yet-running daemon instead of only ever telling the user how to
/// start it themselves.
///
/// `next_path` is percent-encoded into the `next` query param of both the
/// OTC bootstrap URL and the manual-auth fallback URL -- it's the path the
/// daemon's auth flow redirects to post-auth (or immediately, if no auth is
/// needed). Pass `"/"` for the main dashboard, `"/health"` for the daemon's
/// health page, etc.
pub async fn open_page(app: &AppHandle, next_path: &str) -> Result<(), String> {
    let base = base_url();
    let health = match check_health(&base).await {
        Ok(version) => Ok(version),
        Err(reason) => {
            if try_start_sidecar(app, &base).await {
                // Just spawned this from our own bundled sidecar payload, and
                // `try_start_sidecar` returning `true` at all now means its
                // own health poll already confirmed `v == SHELL_VERSION`
                // (see that function's doc comment) -- so this is enforced,
                // not merely assumed, and needs no extra `/api/v1/health`
                // round trip here.
                Ok(Some(SHELL_VERSION.to_string()))
            } else {
                Err(reason)
            }
        }
    };
    match health {
        Ok(mut daemon_version) => {
            // A version handshake cannot catch a sidecar orphaned by the
            // updater when both old and new bundles carry the same version
            // string (the v0.1.14 incident). Check executable provenance
            // before navigating to its HTTP UI instead. This is deliberately
            // narrower than version-drift recovery: only a frozen sidecar
            // outside this app's bundle is eligible.
            if recover_foreign_frozen_sidecar(app, &base).await {
                daemon_version = Some(SHELL_VERSION.to_string());
            }

            // ---- Version handshake gate --------------------------------
            // Incident (v0.1.4): a *different* personal_db daemon -- a
            // stale `com.personal_db.daemon` LaunchAgent from an old dev
            // checkout, in the case that was actually hit -- was already
            // listening on 127.0.0.1:8765 when this shell launched.
            // `check_health` above passed (something did answer
            // `/api/v1/health`), so the old code went straight into the
            // OTC/auth navigation below. But that foreign daemon's routes
            // and templates didn't match what this build's webview
            // expected, and the navigation rendered a blank window with no
            // error anywhere -- indistinguishable, from the user's side,
            // from the app just hanging. A successful health check only
            // proves *some* personal_db daemon owns the port; it does not
            // prove it's *this* build's daemon. So: gate on an exact
            // version match before ever navigating to the auth flow, and
            // route a mismatch (or a pre-handshake daemon reporting no
            // version at all) to a diagnosable guidance page instead.
            //
            // Deliberately does NOT call `try_start_sidecar` here directly:
            // this branch is only reached when the port is already bound (by
            // the mismatched daemon), so spawning our own sidecar could
            // never bind it either -- see that function's doc comment.
            //
            // Incident (v0.1.8 -> v0.1.9 self-update): the in-app updater
            // replaced the bundle and relaunched the shell, but the OLD
            // sidecar daemon (spawned by the pre-update shell process) was
            // still holding the port. This gate correctly caught the version
            // mismatch, but at the time the only move from here was straight
            // to the guidance page -- "killing the foreign process to make
            // room is out of scope" was the call as of that comment. That
            // scope decision is reversed now: when the answering daemon is
            // STRICTLY OLDER than `SHELL_VERSION` (per `parse_semver_lenient`)
            // or reports no version at all (`None` -- pre-0.1.5 daemons
            // predate the version handshake and are older by definition),
            // attempt one self-heal via `restart_stale_daemon` (which now has
            // a verified kill-by-port fallback, see its doc comment) before
            // falling through to the guidance page. Equal-or-newer daemons
            // are never touched here -- that's the same launchd-dev-daemon
            // safety case `check_version_drift` documents, just enforced on
            // this launch path too, not only the health-poll path.
            // `VersionDriftState` is shared with `check_version_drift` as the
            // once-per-version guard on this path as well (keyed on the
            // daemon's exact reported version string, or a sentinel for "no
            // version"), so a user who bounces between launching the app and
            // the background health poll doesn't get a restart attempted
            // twice for the same stale version.
            if daemon_version.as_deref() != Some(SHELL_VERSION) {
                let is_older_or_unversioned = match daemon_version.as_deref() {
                    None => true,
                    Some(v) => matches!(
                        (parse_semver_lenient(v), parse_semver_lenient(SHELL_VERSION)),
                        (Some(dv), Some(sv)) if dv < sv
                    ),
                };

                let mut healed = false;
                if is_older_or_unversioned {
                    let version_key = daemon_version
                        .clone()
                        .unwrap_or_else(|| "<none>".to_string());
                    let already_attempted = {
                        let state = app.state::<VersionDriftState>();
                        let mut guard = state.0.lock().unwrap();
                        if guard.as_deref() == Some(version_key.as_str()) {
                            true
                        } else {
                            *guard = Some(version_key.clone());
                            false
                        }
                    };

                    if already_attempted {
                        // The guard being taken doesn't mean the attempt is
                        // over -- at startup the health poll's immediate
                        // first tick races this launch path, and losing that
                        // race used to strand the window on the mismatch
                        // page while the poll's restart succeeded in the
                        // background (v0.1.9 -> v0.1.10 update incident).
                        // Give the in-flight attempt the same window a fresh
                        // spawn gets before concluding it failed.
                        eprintln!(
                            "open_page: a restart for stale {version_key} was already attempted \
                             or is in flight -- waiting up to {SIDECAR_START_TIMEOUT:?} for the \
                             daemon to come up at v{SHELL_VERSION}"
                        );
                        let deadline = tokio::time::Instant::now() + SIDECAR_START_TIMEOUT;
                        while tokio::time::Instant::now() < deadline {
                            if let Ok(Some(v)) = check_health(&base).await {
                                if v == SHELL_VERSION {
                                    eprintln!(
                                        "open_page: concurrent restart succeeded, daemon now v{v}"
                                    );
                                    daemon_version = Some(v);
                                    healed = true;
                                    break;
                                }
                            }
                            tokio::time::sleep(SIDECAR_POLL_INTERVAL).await;
                        }
                    } else {
                        eprintln!(
                            "open_page: daemon reports {version_key} (shell is v{SHELL_VERSION}) \
                             on launch -- attempting one self-heal restart before falling back \
                             to guidance"
                        );
                        restart_stale_daemon(app, &base).await;
                        if let Ok(Some(v)) = check_health(&base).await {
                            if v == SHELL_VERSION {
                                eprintln!(
                                    "open_page: self-heal restart succeeded, daemon now v{v}"
                                );
                                daemon_version = Some(v);
                                healed = true;
                            }
                        }
                    }
                }

                if !healed {
                    return show_version_mismatch(app, &base, daemon_version.as_deref(), next_path);
                }
            }

            let next =
                url::form_urlencoded::byte_serialize(next_path.as_bytes()).collect::<String>();
            let target = match read_token() {
                Some(token) => match mint_otc(&base, &token).await {
                    Ok(otc) => format!(
                        "{base}/auth/bootstrap?otc={}&next={next}",
                        url::form_urlencoded::byte_serialize(otc.as_bytes()).collect::<String>()
                    ),
                    // OTC mint failed for some transient reason (token
                    // rotated mid-flight, etc.) -- fall back to the manual
                    // paste-the-token page rather than crash the flow.
                    Err(_) => format!("{base}/auth?next={next}"),
                },
                // No token file yet (daemon never started, or a fresh root)
                // -- still worth pointing at the manual auth page rather
                // than erroring, in case the daemon comes up moments later.
                None => format!("{base}/auth?next={next}"),
            };
            show_external(app, &target)
        }
        Err(reason) => show_daemon_down(app, &base, &reason),
    }
}

/// Convenience wrapper around `open_page` for the main dashboard (`next=/`)
/// -- the common case used by first launch, the tray's "Open Dashboard"
/// item, and the daemon-down fallback page's Retry button (via the
/// zero-argument `open_dashboard` Tauri command in `main.rs`).
pub async fn open_dashboard(app: &AppHandle) -> Result<(), String> {
    open_page(app, "/").await
}

/// `POST /api/v1/sync_due` with the daemon token, surfaced as a native
/// notification. Returns the summary string on success for callers that
/// want it (the tray click handler just logs/notifies and discards it).
pub async fn sync_now(app: &AppHandle) -> Result<String, String> {
    let base = base_url();
    let token = read_token().ok_or_else(|| {
        "no daemon token found -- has `personal-db daemon install` been run?".to_string()
    })?;
    let client = http_client(REQUEST_TIMEOUT)?;
    let resp = client
        .post(format!("{base}/api/v1/sync_due"))
        .bearer_auth(&token)
        .send()
        .await
        .map_err(|e| format!("daemon unreachable at {base}: {e}"))?;
    if !resp.status().is_success() {
        let msg = format!("sync_due returned HTTP {}", resp.status());
        notify(app, "Sync failed", &msg);
        return Err(msg);
    }
    let body: serde_json::Value = resp
        .json()
        .await
        .map_err(|e| format!("sync_due response was not valid JSON: {e}"))?;
    let results = body
        .get("results")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    let (ok, err) = summarize_results(&results);
    let summary = format!("{ok} synced, {err} errored");
    notify(app, "Sync complete", &summary);
    Ok(summary)
}

fn summarize_results(results: &serde_json::Value) -> (usize, usize) {
    let mut ok = 0usize;
    let mut err = 0usize;
    if let Some(map) = results.as_object() {
        for value in map.values() {
            match value.as_str() {
                Some(s) if s == "ok" => ok += 1,
                Some(s) if s.starts_with("error") => err += 1,
                _ => {}
            }
        }
    }
    (ok, err)
}

pub(crate) fn notify(app: &AppHandle, title: &str, body: &str) {
    if let Err(e) = app.notification().builder().title(title).body(body).show() {
        eprintln!("notification failed: {e}");
    }
}

/// Joins failing tracker names for the tray tooltip, collapsing anything
/// past `MAX_TOOLTIP_NAMES` into a "+N more" suffix so a bad afternoon with
/// a dozen failing trackers doesn't produce an unreadable tooltip.
fn format_failing_names(names: &[String]) -> String {
    if names.len() <= MAX_TOOLTIP_NAMES {
        names.join(", ")
    } else {
        let shown = names[..MAX_TOOLTIP_NAMES].join(", ");
        format!("{shown}, +{} more", names.len() - MAX_TOOLTIP_NAMES)
    }
}

/// This shell binary's own version, compared against the daemon's reported
/// `app_version` by `check_version_drift`. `CARGO_PKG_VERSION` (baked in at
/// compile time from `Cargo.toml`'s `package.version`) rather than
/// `app.package_info().version` -- same value, but a plain `&'static str`
/// needs no `AppHandle` and no `.to_string()` at every call site.
const SHELL_VERSION: &str = env!("CARGO_PKG_VERSION");

/// How long `restart_stale_daemon` waits for a shut-down-requested daemon to
/// actually stop answering `/api/v1/health` before giving up and trying to
/// spawn a fresh sidecar anyway.
const SHUTDOWN_WAIT_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(250);

/// Parses the leading `major.minor.patch` out of a version string, ignoring
/// any pre-release/build suffix on the patch component (e.g. `"0.1.2-dev3"`
/// -> `(0, 1, 2)`). Deliberately lenient rather than pulling in a `semver`
/// dependency for one comparison: `personal_db`'s Python package version and
/// this shell's `Cargo.toml` version are both plain `x.y.z` in practice.
/// Returns `None` on anything that doesn't parse as at least three numeric
/// components -- callers treat that as "can't compare, skip" rather than
/// guessing.
fn parse_semver_lenient(s: &str) -> Option<(u64, u64, u64)> {
    let mut parts = s.trim().split('.');
    let major = parts.next()?.parse().ok()?;
    let minor = parts.next()?.parse().ok()?;
    let patch_raw = parts.next()?;
    let patch_digits: String = patch_raw
        .chars()
        .take_while(|c| c.is_ascii_digit())
        .collect();
    let patch = patch_digits.parse().ok()?;
    Some((major, minor, patch))
}

/// `POST /api/v1/admin/shutdown` with the daemon token -- asks a daemon to
/// exit itself cleanly (see `services/daemon/routes/admin.py`). Used only by
/// `restart_stale_daemon` when this shell instance doesn't hold a
/// `SidecarState` handle on the daemon it's talking to (i.e. it didn't spawn
/// it -- the exact "old sidecar survived a self-update restart" case this
/// whole mechanism exists for).
async fn shutdown_daemon_via_route(base: &str, token: &str) -> Result<(), String> {
    let client = http_client(HEALTH_TIMEOUT)?;
    let resp = client
        .post(format!("{base}/api/v1/admin/shutdown"))
        .bearer_auth(token)
        .send()
        .await
        .map_err(|e| format!("shutdown request failed: {e}"))?;
    if !resp.status().is_success() {
        return Err(format!("shutdown request returned HTTP {}", resp.status()));
    }
    Ok(())
}

/// Pure signature check gating the kill-by-port fallback in
/// `kill_stale_daemon_by_port` -- kept as a standalone function (rather than
/// inlined) specifically so it can be unit-tested without spawning any real
/// processes. True when `command_line` looks like it's a personal_db daemon
/// process, by either of two shapes seen in practice:
///   - the frozen sidecar binary, whose argv0 is literally
///     `personal-db-daemon` (see `SIDECAR_NAME` / `packaging/freeze-daemon.sh`
///     step 4 -- it's a renamed copy of the frozen python3 Mach-O, not a
///     wrapper script);
///   - a `python -m personal_db ...` invocation -- a dev checkout's launchd
///     `KeepAlive` daemon, or a bare `.venv/bin/python -m personal_db.cli...`
///     run.
/// Both `python` and `personal_db` are required together (an AND, not two
/// independent checks) precisely so this doesn't false-positive on some
/// unrelated process that merely happens to mention Python -- e.g.
/// `/usr/bin/python3 -m http.server 8765` contains "python" but not
/// "personal_db", and correctly does not match. This function is the only
/// thing standing between a SIGTERM/SIGKILL and an arbitrary PID that
/// happened to be listening on the daemon's port; callers must never signal
/// a PID whose command line fails this check.
fn looks_like_personal_db_daemon(command_line: &str) -> bool {
    command_line.contains("personal-db-daemon")
        || (command_line.contains("python") && command_line.contains("personal_db"))
}

/// Runs `/usr/sbin/lsof -nP -ti tcp:{port} -sTCP:LISTEN` to enumerate PIDs
/// currently listening on `port`. Absolute path (not just `lsof`) since an
/// app bundle's inherited PATH can't be relied on to contain it. `-n`/`-P`
/// skip hostname/port-name resolution (irrelevant for a loopback port,
/// and just slows things down); `-t` gives bare newline-separated PIDs, the
/// exact shape this needs to parse. An `lsof` failure (missing binary,
/// nothing listening, etc.) is logged and yields an empty list rather than
/// erroring the caller -- "couldn't find anyone to kill" and "found no one
/// listening" look the same to the caller either way.
fn listener_pids(port: u16) -> Vec<u32> {
    let output = match std::process::Command::new("/usr/sbin/lsof")
        .args(["-nP", "-ti", &format!("tcp:{port}"), "-sTCP:LISTEN"])
        .output()
    {
        Ok(o) => o,
        Err(e) => {
            eprintln!("version drift: lsof failed, cannot enumerate port {port} listeners: {e}");
            return Vec::new();
        }
    };
    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| line.trim().parse::<u32>().ok())
        .collect()
}

/// Runs `ps -o command= -p {pid}` to fetch a PID's full command line (argv0
/// plus arguments -- `command=`, not `comm=`, since the signature check in
/// `looks_like_personal_db_daemon` needs to see e.g. `-m personal_db` in the
/// arguments, not just an interpreter's argv0). Returns `None` if the PID is
/// gone by the time this runs, or `ps` itself fails -- either way, "can't
/// verify" and the caller treats that the same as "signature check failed":
/// refuse to signal it.
fn command_line_for_pid(pid: u32) -> Option<String> {
    let output = std::process::Command::new("ps")
        .args(["-o", "command=", "-p", &pid.to_string()])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout).trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

/// `ps -o comm=` reports the executable image rather than argv with its
/// arguments. That distinction matters for updater recovery: a frozen
/// sidecar's argv includes `-m personal_db`, but only the executable path
/// tells us whether it belongs to *this* installed app bundle.
fn process_executable_path(raw: &str) -> Option<PathBuf> {
    let text = raw.trim().trim_end_matches(" (deleted)").trim();
    (!text.is_empty()).then(|| PathBuf::from(text))
}

/// Resolve the process's mapped executable. `lsof`'s `txt` mapping is the
/// strongest source on macOS when an updater has unlinked its temporary
/// bundle; `ps comm` remains a useful fallback on systems where lsof cannot
/// inspect the process.
fn executable_for_pid(pid: u32) -> Option<PathBuf> {
    if let Ok(output) = std::process::Command::new("/usr/sbin/lsof")
        .args(["-a", "-p", &pid.to_string(), "-d", "txt", "-Fn"])
        .output()
    {
        if output.status.success() {
            if let Some(path) = String::from_utf8_lossy(&output.stdout)
                .lines()
                .find_map(|line| line.strip_prefix('n'))
                .and_then(process_executable_path)
            {
                return Some(path);
            }
        }
    }

    let output = std::process::Command::new("/bin/ps")
        .args(["-o", "comm=", "-p", &pid.to_string()])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    process_executable_path(&String::from_utf8_lossy(&output.stdout))
}

/// The only frozen daemon binary a packaged shell may own. Tauri places
/// `externalBin` next to the shell executable in `Contents/MacOS`; deriving
/// this path from the running app (rather than hard-coding `/Applications`)
/// also works for a user who drags PersonalDB elsewhere.
fn bundled_sidecar_candidate_from_exe(executable: &Path) -> Option<PathBuf> {
    let macos_dir = executable.parent()?;
    if !is_packaged_macos_dir(&macos_dir) {
        return None;
    }
    Some(macos_dir.join(SIDECAR_NAME))
}

/// Derive the current bundle's sidecar from the actual Mach-O path. Tauri's
/// `PathResolver::executable_dir` intentionally returns None on macOS, while
/// `current_exe` is the authoritative location of the running app after an
/// updater relaunch.
fn expected_sidecar_path() -> Option<PathBuf> {
    let sidecar = bundled_sidecar_candidate_from_exe(&std::env::current_exe().ok()?)?;
    sidecar.is_file().then_some(sidecar)
}

/// A release shell/sidecar directory is exactly
/// `<Name>.app/Contents/MacOS`. `tauri dev` / target-debug paths deliberately
/// fail this shape check, so a developer's launchd daemon can never be
/// considered foreign merely because a release app uses the same loopback
/// port.
fn is_packaged_macos_dir(macos_dir: &Path) -> bool {
    let Some(contents_dir) = macos_dir.parent() else {
        return false;
    };
    let Some(bundle_dir) = contents_dir.parent() else {
        return false;
    };
    macos_dir.file_name().and_then(|name| name.to_str()) == Some("MacOS")
        && contents_dir.file_name().and_then(|name| name.to_str()) == Some("Contents")
        && bundle_dir
            .file_name()
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".app"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum SidecarProvenance {
    /// The listener is the frozen payload next to this shell executable.
    CurrentBundle,
    /// A frozen PersonalDB sidecar from another bundle. This is the precise
    /// shape Tauri's updater leaves behind in its temporary `current_app`
    /// directory when the old shell exits without terminating its child.
    ForeignFrozenBundle,
    /// A Python/launchd development daemon or an unrelated process. Never
    /// signal this based on a packaged-app health check.
    Other,
}

/// Pure executable-provenance classifier used before any signal is sent.
/// Matching the basename alone identifies a frozen PersonalDB payload;
/// equality with the expected path proves it belongs to this app instance.
/// Development daemons run a Python executable, so they deliberately fall
/// into `Other` even if their arguments contain `personal_db`.
fn classify_sidecar_provenance(executable: &Path, expected: &Path) -> SidecarProvenance {
    let same_current_bundle = executable == expected
        || std::fs::canonicalize(executable)
            .ok()
            .zip(std::fs::canonicalize(expected).ok())
            .is_some_and(|(actual, current)| actual == current);
    let is_frozen_bundle_sidecar = executable.file_name().and_then(|name| name.to_str())
        == Some(SIDECAR_NAME)
        && executable
            .parent()
            .and_then(Path::file_name)
            .and_then(|name| name.to_str())
            == Some("MacOS")
        && executable
            .parent()
            .and_then(Path::parent)
            .and_then(Path::file_name)
            .and_then(|name| name.to_str())
            == Some("Contents")
        && executable
            .parent()
            .and_then(Path::parent)
            .and_then(Path::parent)
            .and_then(Path::file_name)
            .and_then(|name| name.to_str())
            .is_some_and(|name| name.ends_with(".app"));
    if same_current_bundle {
        SidecarProvenance::CurrentBundle
    } else if is_frozen_bundle_sidecar {
        SidecarProvenance::ForeignFrozenBundle
    } else {
        SidecarProvenance::Other
    }
}

/// Return only frozen `personal-db-daemon` listeners that are outside this
/// shell's bundle. It is intentionally impossible for this function to
/// select a launchd-managed Python daemon or an arbitrary listener merely
/// occupying the daemon port.
fn foreign_frozen_sidecar_pids(base: &str) -> Vec<u32> {
    let Some(expected) = expected_sidecar_path() else {
        // `tauri dev` has no stable packaged executable provenance. Skipping
        // recovery there protects the developer's launchd daemon.
        return Vec::new();
    };
    listener_pids(daemon_port(base))
        .into_iter()
        .filter(|pid| {
            executable_for_pid(*pid).is_some_and(|executable| {
                classify_sidecar_provenance(&executable, &expected)
                    == SidecarProvenance::ForeignFrozenBundle
            })
        })
        .collect()
}

/// Generic ownership transfer used by normal exit, updater restart, and the
/// stale-daemon path. Keeping this tiny operation pure makes it impossible
/// for a cleanup call to kill the same child twice.
fn take_owned_sidecar<T>(slot: &mut Option<T>) -> Option<T> {
    slot.take()
}

/// Stop only the sidecar process this shell spawned. It never searches the
/// port, so closing PersonalDB cannot affect a launchd-managed dev daemon or
/// any other process. The updater calls this immediately before relaunch,
/// which prevents the sidecar from becoming orphaned under launchd (PPID 1).
pub fn stop_owned_sidecar(app: &AppHandle, reason: &str) {
    let child = {
        let state = app.state::<SidecarState>();
        let mut sidecar = state.0.lock().unwrap();
        take_owned_sidecar(&mut *sidecar)
    };
    if let Some(child) = child {
        eprintln!("daemon: stopping app-owned sidecar before {reason}");
        if let Err(e) = child.kill() {
            eprintln!("daemon: could not stop app-owned sidecar: {e}");
        }
    }
}

/// Verified kill-by-port fallback, used by `restart_stale_daemon` only after
/// its graceful `/api/v1/admin/shutdown` request (or the lack of a token to
/// send one with) failed to actually clear the port. Enumerates listening
/// PIDs via `lsof`, signature-checks each one's command line with
/// `looks_like_personal_db_daemon` *before ever touching it*, SIGTERMs the
/// verified matches, waits again for the port to go quiet, and only
/// escalates to SIGKILL if it's still answering health after that. A PID
/// whose command line fails the signature check (or can't be read at all)
/// is never signaled -- it's logged and left alone, and the guidance page
/// remains the final fallback for the user to sort out by hand.
async fn kill_stale_daemon_by_port(base: &str) {
    let port = daemon_port(base);
    let pids = listener_pids(port);
    if pids.is_empty() {
        eprintln!("version drift: no lsof listener found on port {port}, nothing to kill");
        return;
    }

    let mut verified_pids = Vec::new();
    for pid in pids {
        match command_line_for_pid(pid) {
            Some(cmd) if looks_like_personal_db_daemon(&cmd) => {
                eprintln!(
                    "version drift: pid {pid} on port {port} looks like a personal_db daemon \
                     ({cmd:?}) -- will signal it"
                );
                verified_pids.push(pid);
            }
            Some(cmd) => {
                eprintln!(
                    "version drift: pid {pid} on port {port} does NOT look like a personal_db \
                     daemon ({cmd:?}) -- refusing to signal it"
                );
            }
            None => {
                eprintln!(
                    "version drift: could not read command line for pid {pid} on port {port} \
                     -- refusing to signal it"
                );
            }
        }
    }

    if verified_pids.is_empty() {
        eprintln!(
            "version drift: no verified personal_db daemon pids to signal on port {port} -- \
             leaving it for the guidance page"
        );
        return;
    }

    for pid in &verified_pids {
        eprintln!("version drift: sending SIGTERM to pid {pid}");
        let _ = std::process::Command::new("kill")
            .args(["-TERM", &pid.to_string()])
            .status();
    }

    wait_until_unreachable(base, SHUTDOWN_WAIT_TIMEOUT).await;

    if check_health(base).await.is_ok() {
        eprintln!(
            "version drift: port {port} still answers health after SIGTERM -- escalating to \
             SIGKILL"
        );
        for pid in &verified_pids {
            eprintln!("version drift: sending SIGKILL to pid {pid}");
            let _ = std::process::Command::new("kill")
                .args(["-KILL", &pid.to_string()])
                .status();
        }
        wait_until_unreachable(base, SHUTDOWN_WAIT_TIMEOUT).await;
    }
}

/// Polls `check_health` until it fails or `timeout` elapses, so callers that
/// just asked a daemon to shut down can wait for the port to actually go
/// quiet before spawning a replacement on top of it.
async fn wait_until_unreachable(base: &str, timeout: Duration) {
    let deadline = tokio::time::Instant::now() + timeout;
    while tokio::time::Instant::now() < deadline {
        if check_health(base).await.is_err() {
            return;
        }
        tokio::time::sleep(SHUTDOWN_POLL_INTERVAL).await;
    }
}

/// Claim a particular foreign listener set for recovery. Startup navigation
/// and the immediate health-poll tick run concurrently; one owner is enough,
/// and repeatedly signalling an undeletable updater-temp process would be
/// worse than leaving the normal guidance fallback in place.
fn claim_foreign_sidecar_recovery(app: &AppHandle, pids: &[u32]) -> bool {
    let key = pids
        .iter()
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let state = app.state::<ProvenanceRecoveryState>();
    let mut guard = state.0.lock().unwrap();
    if guard.as_deref() == Some(key.as_str()) {
        false
    } else {
        *guard = Some(key);
        true
    }
}

/// Signal only the exact non-current frozen sidecar PIDs that were selected
/// by executable provenance. Revalidating immediately before *each* signal
/// protects against a PID disappearing/reusing while we wait; in particular,
/// this never falls back to the broad version-drift port-kill path because
/// that path is allowed to match development Python daemons.
async fn terminate_foreign_frozen_sidecars(base: &str, pids: &[u32]) {
    let mut term_sent = Vec::new();
    for pid in pids {
        // Re-resolve immediately before each signal: a PID can disappear
        // and be reused while a competing startup task is running. A fresh
        // provenance check is required for every process we touch.
        if foreign_frozen_sidecar_pids(base).contains(pid) {
            eprintln!(
                "daemon provenance recovery: sending SIGTERM to non-current frozen sidecar pid {pid}"
            );
            let _ = std::process::Command::new("/bin/kill")
                .args(["-TERM", &pid.to_string()])
                .status();
            term_sent.push(*pid);
        }
    }
    if term_sent.is_empty() {
        return;
    }
    wait_until_unreachable(base, SHUTDOWN_WAIT_TIMEOUT).await;

    if check_health(base).await.is_ok() {
        for pid in &term_sent {
            // Re-run the exact check once more before escalation. If this
            // PID became a dev daemon or another process owns the port now,
            // it is deliberately excluded.
            if foreign_frozen_sidecar_pids(base).contains(pid) {
                eprintln!(
                    "daemon provenance recovery: pid {pid} survived SIGTERM; sending SIGKILL"
                );
                let _ = std::process::Command::new("/bin/kill")
                    .args(["-KILL", &pid.to_string()])
                    .status();
            }
        }
        wait_until_unreachable(base, SHUTDOWN_WAIT_TIMEOUT).await;
    }
}

/// Recover the v0.1.14 updater failure mode even when both processes report
/// the same semantic version. The old sidecar is eligible only when its
/// executable is a frozen `.../Contents/MacOS/personal-db-daemon` outside
/// the current shell's bundle. That lets packaged releases heal Tauri's
/// deleted temporary bundle while leaving launchd/Python development daemons
/// and unrelated port owners untouched.
async fn recover_foreign_frozen_sidecar(app: &AppHandle, base: &str) -> bool {
    let pids = foreign_frozen_sidecar_pids(base);
    if pids.is_empty() || !claim_foreign_sidecar_recovery(app, &pids) {
        return false;
    }
    eprintln!(
        "daemon provenance recovery: found non-current frozen sidecar listener(s) {:?}; replacing",
        pids
    );
    terminate_foreign_frozen_sidecars(base, &pids).await;
    if check_health(base).await.is_ok() {
        eprintln!(
            "daemon provenance recovery: listener remains after targeted cleanup; refusing to \
             touch any other port owner"
        );
        return false;
    }
    if try_start_sidecar(app, base).await {
        eprintln!("daemon provenance recovery: restarted daemon from current bundle");
        true
    } else {
        eprintln!("daemon provenance recovery: could not start current-bundle sidecar");
        false
    }
}

/// Replaces a daemon that's been determined to be running an older version
/// than this shell (or reporting no version at all): stop it (killing our
/// own sidecar handle if we have one, otherwise asking it to shut down via
/// the admin route with the on-disk token), wait briefly for it to actually
/// go quiet, and spawn a fresh sidecar. Returns whether the replacement
/// daemon came up healthy at this shell's own version.
///
/// Deliberately does NOT touch the "main" window itself -- callers own that.
/// It used to end with a `location.reload()`, which is exactly wrong when
/// the window is showing the bundled version-mismatch page: reloading a
/// static guidance page just re-renders the guidance page (v0.1.9 ->
/// v0.1.10 update incident: the daemon was replaced successfully while the
/// user sat on a mismatch screen that a reload could never dismiss). The
/// poll-path caller re-enters the full `open_page` flow instead; the
/// launch-path caller is already inside that flow and falls through to the
/// normal navigation on success.
///
/// Called from two places: `check_version_drift` (the poll-path trigger) and
/// `open_page`'s version-mismatch gate (the launch-path trigger added for
/// the v0.1.8 -> v0.1.9 self-update incident -- see that function's comment).
///
/// The graceful `/api/v1/admin/shutdown` request can fail to actually clear
/// the port: the stale daemon may predate that route entirely, its on-disk
/// token may have rotated out from under it, or it may just be hung. So if
/// `wait_until_unreachable` times out and the port still answers health
/// after the graceful attempt, this escalates to a verified kill-by-port
/// fallback (see `kill_stale_daemon_by_port`) before giving up and trying to
/// spawn a fresh sidecar anyway -- which, per `try_start_sidecar`'s doc
/// comment, will correctly fail to bind (rather than falsely "succeed") if
/// the stale process is somehow still holding the port at that point.
async fn restart_stale_daemon(app: &AppHandle, base: &str) -> bool {
    let had_own_sidecar = {
        let state = app.state::<SidecarState>();
        let mut guard = state.0.lock().unwrap();
        if let Some(child) = guard.take() {
            eprintln!("version drift: killing our own stale sidecar daemon");
            let _ = child.kill();
            true
        } else {
            false
        }
    };

    if !had_own_sidecar {
        match read_token() {
            Some(token) => {
                eprintln!(
                    "version drift: daemon wasn't spawned by this shell instance (likely an old \
                     sidecar that survived a self-update restart) -- requesting shutdown via \
                     POST /api/v1/admin/shutdown"
                );
                if let Err(e) = shutdown_daemon_via_route(base, &token).await {
                    eprintln!("version drift: shutdown route call failed: {e}");
                }
            }
            None => {
                eprintln!(
                    "version drift: no daemon token on disk -- cannot request a graceful \
                     shutdown of the stale daemon"
                );
            }
        }
    }

    wait_until_unreachable(base, SHUTDOWN_WAIT_TIMEOUT).await;

    // The graceful request above didn't necessarily work -- see this
    // function's doc comment for the three ways it can fail. If the port
    // still answers health after waiting, escalate to the verified
    // kill-by-port fallback before attempting the fresh spawn below.
    if check_health(base).await.is_ok() {
        eprintln!(
            "version drift: port {} still answers health after the graceful shutdown attempt \
             (and the wait for it to go quiet) -- trying verified kill-by-port fallback",
            daemon_port(base)
        );
        kill_stale_daemon_by_port(base).await;
    }

    if try_start_sidecar(app, base).await {
        eprintln!("version drift: restarted daemon");
        true
    } else {
        eprintln!(
            "version drift: failed to restart the daemon after shutdown -- will leave it and \
             keep polling health normally"
        );
        false
    }
}

/// Detects and (at most once per observed stale version) reacts to the
/// "zombie daemon after self-update" bug: the Tauri shell spawns the daemon
/// as a sidecar, but a self-update restarts the *shell* process, not the
/// daemon, so an old sidecar can keep running and serving newly-updated
/// on-disk templates against its stale Python routes.
///
/// SAFETY CASE: on a developer machine the daemon is often launchd-managed
/// (`launchctl` `KeepAlive`) and intentionally runs ahead-of-release repo
/// code, whose reported `app_version` may equal or exceed the shell's. Three
/// separate guards keep this mechanism from ever fighting that setup or
/// restart-looping in general:
///   1. **Older-only**: a restart is only attempted when the daemon's
///      version is *strictly older* than the shell's (or when either string
///      fails to parse, `parse_semver_lenient` returns `None` and we skip
///      entirely rather than guess). Equal or newer versions are always left
///      alone -- for a launchd daemon that would just be killed and
///      immediately relaunched by launchd running the exact same (still
///      "newer-or-equal") code, an unproductive restart loop with no benefit
///      to anyone.
///   2. **Once per version**: `VersionDriftState` records the last stale
///      version string a restart was attempted for. A later poll that still
///      observes that exact version only logs; it does not try again. (A
///      *different* stale version -- e.g. the shell itself updated again --
///      gets its own single attempt.)
///   3. **Signature-verified kill only**: `restart_stale_daemon`'s
///      kill-by-port fallback (`kill_stale_daemon_by_port`, added for the
///      v0.1.8 -> v0.1.9 self-update incident where the graceful shutdown
///      route alone didn't clear a stale sidecar) only ever signals a PID
///      whose command line matches `looks_like_personal_db_daemon` -- some
///      unrelated process that happens to be squatting on the port is never
///      touched. A launchd `KeepAlive` dev daemon is doubly protected here in
///      practice: even though its command line *would* match the signature
///      check, guard #1 above means `restart_stale_daemon` (and therefore the
///      kill-by-port fallback inside it) is never even reached for it, since
///      it's excluded by the older-only rule before any of this runs.
async fn check_version_drift(app: &AppHandle, base: &str, daemon_version: &str) {
    let Some(daemon_v) = parse_semver_lenient(daemon_version) else {
        return;
    };
    let Some(shell_v) = parse_semver_lenient(SHELL_VERSION) else {
        return;
    };
    if daemon_v >= shell_v {
        // Equal or newer daemon: normal for a dev setup running ahead of the
        // shell's own version -- see the safety case above. Nothing to do.
        return;
    }

    let state = app.state::<VersionDriftState>();
    {
        let mut guard = state.0.lock().unwrap();
        if guard.as_deref() == Some(daemon_version) {
            eprintln!(
                "version drift: daemon still reports stale v{daemon_version} (shell is \
                 v{SHELL_VERSION}) after a prior restart attempt for this version -- leaving it \
                 alone"
            );
            return;
        }
        *guard = Some(daemon_version.to_string());
    }

    eprintln!(
        "version drift detected: daemon reports v{daemon_version}, shell is v{SHELL_VERSION} -- \
         attempting one restart"
    );
    let restarted = restart_stale_daemon(app, base).await;

    // Window recovery. If a "main" window is open right now it is almost
    // certainly showing something served by (or about) the stale daemon --
    // most likely the bundled version-mismatch guidance page, if `open_page`
    // lost the once-per-version race to this poll (both fire at startup; the
    // poll's first tick is immediate). Re-enter the full open flow rather
    // than reloading in place: a reload of the static mismatch page can
    // never dismiss it (the v0.1.9 -> v0.1.10 update incident), while
    // `open_page` health-checks the fresh daemon and navigates to the real
    // dashboard. No cycle: `open_page`'s own mismatch branch calls
    // `restart_stale_daemon`, never back into this function.
    if restarted && app.get_webview_window(WINDOW_LABEL).is_some() {
        eprintln!("version drift: re-entering the open flow to replace the stale window");
        if let Err(e) = open_page(app, "/").await {
            eprintln!("version drift: failed to reopen the dashboard after restart: {e}");
        }
    }
}

/// `GET /api/v1/health` on a timer (see `HEALTH_POLL_INTERVAL` /
/// `main.rs`'s setup-time poll loop) and updates the "main" tray icon's
/// title/tooltip to reflect what it finds:
///
/// - healthy, no repeated failures: clear the "!" title, tooltip shows the
///   daemon's reported `app_version`.
/// - healthy, `repeated_sync_failures` non-empty: "!" title, tooltip names
///   the failing trackers.
/// - unreachable: only acts on the down-*transition* (last poll was
///   healthy) to avoid spawn-looping a sidecar or repeatedly yanking focus
///   back to the guidance window every tick while the daemon stays down --
///   see `HealthState`'s doc comment. On a transition it tries
///   `try_start_sidecar` once; if that doesn't bring the daemon back, it
///   re-shows the bundled daemon-down page if a "main" window is currently
///   open, or otherwise just badges the tray.
pub async fn poll_health_status(app: &AppHandle) {
    let base = base_url();
    let tray = app.tray_by_id(TRAY_ID);

    let client = match http_client(HEALTH_TIMEOUT) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("health poll: {e}");
            return;
        }
    };

    let response = client.get(format!("{base}/api/v1/health")).send().await;

    let healthy_payload = match response {
        Ok(resp) if resp.status().is_success() => resp.json::<HealthPayload>().await.ok(),
        _ => None,
    };

    if let Some(payload) = healthy_payload {
        {
            let state = app.state::<HealthState>();
            *state.0.lock().unwrap() = Some(true);
        }

        // Runs regardless of whether a tray exists (tray is optional; a
        // stale post-update daemon isn't). This same poll task's interval
        // fires its first tick immediately (see main.rs's `setup`), so this
        // also covers "check once ~5s after startup" without any extra
        // wiring -- startup is the moment this matters most, right after a
        // self-update relaunch.
        if recover_foreign_frozen_sidecar(app, &base).await {
            // `try_start_sidecar` verifies the replacement's exact version.
            // Let the next poll read its fresh health payload instead of
            // applying the orphan's old tooltip/failure state to it.
            return;
        }
        if !payload.app_version.is_empty() {
            check_version_drift(app, &base, &payload.app_version).await;
        }

        let Some(tray) = tray else { return };
        if payload.repeated_sync_failures.is_empty() {
            let _ = tray.set_title(None::<String>);
            let tooltip = if payload.app_version.is_empty() {
                "PersonalDB".to_string()
            } else {
                format!("PersonalDB — v{}", payload.app_version)
            };
            let _ = tray.set_tooltip(Some(tooltip));
        } else {
            let _ = tray.set_title(Some("!"));
            let _ = tray.set_tooltip(Some(format!(
                "Sync failing: {}",
                format_failing_names(&payload.repeated_sync_failures)
            )));
        }
        return;
    }

    // Unreachable (request failed, non-2xx, or an unparseable body). Only
    // act on the transition from healthy -> unreachable; a poll that finds
    // the daemon *still* down since the last tick is a no-op, both for the
    // sidecar (try_start_sidecar's own SidecarState guard would refuse a
    // second spawn anyway) and for the window/tray updates below.
    let was_healthy = {
        let state = app.state::<HealthState>();
        let mut guard = state.0.lock().unwrap();
        let was = guard.unwrap_or(false);
        *guard = Some(false);
        was
    };
    if !was_healthy {
        return;
    }

    eprintln!(
        "daemon health poll: was healthy, now unreachable at {base} -- attempting one restart"
    );
    if try_start_sidecar(app, &base).await {
        let state = app.state::<HealthState>();
        *state.0.lock().unwrap() = Some(true);
        if let Some(tray) = tray {
            let _ = tray.set_title(None::<String>);
            let _ = tray.set_tooltip(Some("PersonalDB".to_string()));
        }
        return;
    }

    if app.get_webview_window(WINDOW_LABEL).is_some() {
        let _ = show_daemon_down(app, &base, "daemon unreachable");
    } else if let Some(tray) = tray {
        let _ = tray.set_tooltip(Some("PersonalDB — daemon not running"));
        let _ = tray.set_title(Some("!"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Real command lines observed via `ps -o command=` while diagnosing the
    // v0.1.8 -> v0.1.9 stale-sidecar incident -- see
    // `looks_like_personal_db_daemon`'s doc comment for why the check is an
    // AND of "python" and "personal_db", not two independent ORs.

    #[test]
    fn sidecar_env_denylist_strips_python_and_tls_overrides() {
        for poison in [
            "PYTHONPATH",
            "PYTHONHOME",
            "VIRTUAL_ENV",
            "REQUESTS_CA_BUNDLE",
            "SSL_CERT_FILE",
        ] {
            assert!(
                !env_var_is_sidecar_safe(poison),
                "{poison} must be stripped"
            );
        }
        for safe in ["PATH", "HOME", "LANG", "PERSONAL_DB_ROOT", "TMPDIR"] {
            assert!(env_var_is_sidecar_safe(safe), "{safe} must survive");
        }
    }

    #[test]
    fn sanitized_parent_env_preserves_path_and_drops_pythonpath() {
        // std::env mutation is process-global; this test relies on PATH being
        // present in any real test environment rather than setting vars.
        let env = sanitized_parent_env();
        assert!(env.iter().any(|(k, _)| k == "PATH"));
        assert!(env.iter().all(|(k, _)| env_var_is_sidecar_safe(k)));
    }

    #[test]
    fn matches_frozen_sidecar_binary() {
        assert!(looks_like_personal_db_daemon(
            "/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon -m personal_db dev \
             daemon run --port 8765"
        ));
    }

    #[test]
    fn matches_dev_venv_python_invocation() {
        assert!(looks_like_personal_db_daemon(
            "/Users/x/repo/.venv/bin/python -m personal_db.cli.main --root ... init"
        ));
    }

    #[test]
    fn does_not_match_unrelated_node_process() {
        assert!(!looks_like_personal_db_daemon("node server.js"));
    }

    #[test]
    fn does_not_match_python_without_personal_db() {
        // Contains "python" but not "personal_db" -- must not match, since
        // this is exactly the kind of unrelated process squatting on a port
        // that the signature check exists to protect.
        assert!(!looks_like_personal_db_daemon(
            "/usr/bin/python3 -m http.server 8765"
        ));
    }

    #[test]
    fn parse_semver_lenient_handles_prerelease_suffix() {
        assert_eq!(parse_semver_lenient("0.1.2-dev3"), Some((0, 1, 2)));
        assert_eq!(parse_semver_lenient("0.1.9"), Some((0, 1, 9)));
        assert_eq!(parse_semver_lenient("not-a-version"), None);
    }

    #[test]
    fn parse_semver_lenient_orders_as_expected() {
        assert!(parse_semver_lenient("0.1.8").unwrap() < parse_semver_lenient("0.1.9").unwrap());
        assert!(parse_semver_lenient("0.1.9").unwrap() >= parse_semver_lenient("0.1.9").unwrap());
    }

    #[test]
    fn provenance_accepts_current_packaged_sidecar() {
        let current = Path::new("/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon");
        assert_eq!(
            classify_sidecar_provenance(current, current),
            SidecarProvenance::CurrentBundle
        );
    }

    #[test]
    fn provenance_flags_same_version_updater_temp_sidecar_even_when_deleted() {
        let current = Path::new("/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon");
        let stale = process_executable_path(
            "/private/var/folders/x/T/tauri_current_app/current_app/PersonalDB.app/Contents/MacOS/personal-db-daemon (deleted)",
        )
        .unwrap();
        assert_eq!(
            classify_sidecar_provenance(&stale, current),
            SidecarProvenance::ForeignFrozenBundle
        );
    }

    #[test]
    fn provenance_never_selects_python_launchd_daemon_or_unrelated_owner() {
        let current = Path::new("/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon");
        assert_eq!(
            classify_sidecar_provenance(Path::new("/Users/dev/repo/.venv/bin/python"), current,),
            SidecarProvenance::Other
        );
        assert_eq!(
            classify_sidecar_provenance(Path::new("/usr/local/bin/node"), current),
            SidecarProvenance::Other
        );
        assert_eq!(
            classify_sidecar_provenance(
                Path::new("/tmp/not-a-bundle/Contents/MacOS/personal-db-daemon"),
                current,
            ),
            SidecarProvenance::Other
        );
    }

    #[test]
    fn process_executable_path_strips_deleted_suffix_and_whitespace() {
        assert_eq!(
            process_executable_path(
                "  /tmp/PersonalDB.app/Contents/MacOS/personal-db-daemon (deleted) \n"
            ),
            Some(PathBuf::from(
                "/tmp/PersonalDB.app/Contents/MacOS/personal-db-daemon"
            ))
        );
    }

    #[test]
    fn only_packaged_shell_directories_have_sidecar_provenance_recovery() {
        assert!(is_packaged_macos_dir(Path::new(
            "/Applications/PersonalDB.app/Contents/MacOS"
        )));
        assert!(!is_packaged_macos_dir(Path::new(
            "/Users/dev/repo/shell/target/debug"
        )));
    }

    #[test]
    fn derives_sidecar_only_from_a_packaged_shell_executable() {
        assert_eq!(
            bundled_sidecar_candidate_from_exe(Path::new(
                "/Applications/PersonalDB.app/Contents/MacOS/personal-db-shell"
            )),
            Some(PathBuf::from(
                "/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon"
            ))
        );
        assert_eq!(
            bundled_sidecar_candidate_from_exe(Path::new(
                "/Users/dev/repo/shell/target/debug/personal-db-shell"
            )),
            None
        );
    }

    #[test]
    fn owned_sidecar_cleanup_transfers_child_only_once() {
        let mut sidecar = Some("owned child");
        assert_eq!(take_owned_sidecar(&mut sidecar), Some("owned child"));
        assert_eq!(take_owned_sidecar(&mut sidecar), None);
    }
}
