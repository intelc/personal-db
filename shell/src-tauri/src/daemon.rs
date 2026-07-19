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
use std::path::PathBuf;
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
async fn check_health(base: &str) -> Result<(), String> {
    let client = http_client(HEALTH_TIMEOUT)?;
    let resp = client
        .get(format!("{base}/api/v1/health"))
        .send()
        .await
        .map_err(|e| format!("daemon unreachable at {base}: {e}"))?;
    if resp.status().is_success() {
        Ok(())
    } else {
        Err(format!("daemon at {base} returned HTTP {}", resp.status()))
    }
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
    close_existing_window(app);
    let parsed = url::Url::parse(url).map_err(|e| format!("bad url {url}: {e}"))?;
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

/// Attempts to spawn the frozen daemon payload (`packaging/freeze-daemon.sh`,
/// wired in as `bundle.externalBin` in tauri.conf.json) when nothing answers
/// health at `base` yet. Returns `true` once the spawned daemon becomes
/// healthy, `false` if there's no sidecar for this build (e.g. a `tauri dev`
/// run without the payload at the expected relative path -- not an error,
/// just "nothing to spawn"), if it's already been spawned once this session,
/// or if it never became healthy within `SIDECAR_START_TIMEOUT`. Callers
/// treat `false` the same as "daemon down" and fall back to the guidance
/// page.
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

    let port = daemon_port(base);
    let root = root_dir();
    eprintln!(
        "daemon unreachable at {base}; spawning sidecar (root={}, port={port})",
        root.display()
    );

    let sidecar = sidecar
        .env("PERSONAL_DB_ROOT", root.to_string_lossy().to_string())
        .args(["dev", "daemon", "run", "--port", &port.to_string()]);

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
        if check_health(base).await.is_ok() {
            return true;
        }
        tokio::time::sleep(SIDECAR_POLL_INTERVAL).await;
    }
    eprintln!("sidecar spawned but never became healthy within {SIDECAR_START_TIMEOUT:?}");
    false
}

/// The full bootstrap flow described in the Phase 4 plan: locate the root,
/// read the token, check daemon health, and either navigate to the
/// authenticated dashboard (via the OTC bootstrap URL) or show the bundled
/// "daemon not running" guidance page. Invoked on first launch and from the
/// tray's "Open Dashboard"/"Status" items, and from the fallback page's
/// Retry button (via the `open_dashboard` Tauri command).
///
/// If the initial health check fails, this now also tries to spawn the
/// bundled sidecar daemon (see `try_start_sidecar`) before falling back to
/// the guidance page -- a signed release build should self-heal a
/// not-yet-running daemon instead of only ever telling the user how to
/// start it themselves.
pub async fn open_dashboard(app: &AppHandle) -> Result<(), String> {
    let base = base_url();
    let health = match check_health(&base).await {
        Ok(()) => Ok(()),
        Err(reason) => {
            if try_start_sidecar(app, &base).await {
                Ok(())
            } else {
                Err(reason)
            }
        }
    };
    match health {
        Ok(()) => {
            let target = match read_token() {
                Some(token) => match mint_otc(&base, &token).await {
                    Ok(otc) => format!(
                        "{base}/auth/bootstrap?otc={}&next=%2F",
                        url::form_urlencoded::byte_serialize(otc.as_bytes()).collect::<String>()
                    ),
                    // OTC mint failed for some transient reason (token
                    // rotated mid-flight, etc.) -- fall back to the manual
                    // paste-the-token page rather than crash the flow.
                    Err(_) => format!("{base}/auth?next=%2F"),
                },
                // No token file yet (daemon never started, or a fresh root)
                // -- still worth pointing at the manual auth page rather
                // than erroring, in case the daemon comes up moments later.
                None => format!("{base}/auth?next=%2F"),
            };
            show_external(app, &target)
        }
        Err(reason) => show_daemon_down(app, &base, &reason),
    }
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
    let results = body.get("results").cloned().unwrap_or(serde_json::Value::Null);
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

    eprintln!("daemon health poll: was healthy, now unreachable at {base} -- attempting one restart");
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
