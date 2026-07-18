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
use std::time::Duration;

use serde::Deserialize;
use tauri::{AppHandle, Manager, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_notification::NotificationExt;

const DEFAULT_BASE_URL: &str = "http://127.0.0.1:8765";
const HEALTH_TIMEOUT: Duration = Duration::from_secs(5);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(300);
const WINDOW_LABEL: &str = "main";

#[derive(Debug, Deserialize)]
struct OtcResponse {
    otc: String,
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
        .title("PersonalDB — daemon not running")
        .inner_size(560.0, 460.0)
        .resizable(true)
        .build()
        .map_err(|e| format!("failed to open fallback window: {e}"))?;
    Ok(())
}

/// The full bootstrap flow described in the Phase 4 plan: locate the root,
/// read the token, check daemon health, and either navigate to the
/// authenticated dashboard (via the OTC bootstrap URL) or show the bundled
/// "daemon not running" guidance page. Invoked on first launch and from the
/// tray's "Open Dashboard"/"Status" items, and from the fallback page's
/// Retry button (via the `open_dashboard` Tauri command).
pub async fn open_dashboard(app: &AppHandle) -> Result<(), String> {
    let base = base_url();
    match check_health(&base).await {
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

fn notify(app: &AppHandle, title: &str, body: &str) {
    if let Err(e) = app.notification().builder().title(title).body(body).show() {
        eprintln!("notification failed: {e}");
    }
}
