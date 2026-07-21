//! First-launch nudge: if neither the CLI symlink nor any MCP host is
//! configured yet, show one native notification pointing at the tray's
//! setup items, exactly once (persisted via a marker file under this app's
//! app-data dir -- no new window, no repeated nagging).

use std::fs;
use std::path::{Path, PathBuf};

use tauri::{AppHandle, Manager};
use tauri_plugin_notification::NotificationExt;

use crate::cli_install::{self, LinkState};

const MARKER_FILE: &str = "onboarding-nudged";

fn marker_path(app: &AppHandle) -> Result<PathBuf, String> {
    let dir = app
        .path()
        .app_data_dir()
        .map_err(|e| format!("failed to resolve app data dir: {e}"))?;
    Ok(dir.join(MARKER_FILE))
}

fn home_join(rel: &str) -> Option<PathBuf> {
    dirs::home_dir().map(|h| h.join(rel))
}

/// Best-effort check for whether any of the three MCP hosts already have a
/// personal_db entry. Deliberately a plain substring check rather than a
/// strict JSON parse: Cursor's and Claude Desktop's config files are
/// simple `mcpServers` JSON objects (see
/// `services/wizard/mcp_setup.py::_upsert_json_mcp_server`), but Claude
/// Code's user-scope config (`~/.claude.json`, written by `claude mcp add
/// -s user`) has no stable public schema this app should depend on --
/// erring toward "probably configured" (and thus not re-nudging) is the
/// safer failure mode for a one-time hint, not a correctness-critical path.
fn any_mcp_target_configured() -> bool {
    let candidates: Vec<Option<PathBuf>> = vec![
        home_join(".cursor/mcp.json"),
        home_join("Library/Application Support/Claude/claude_desktop_config.json"),
        home_join(".claude.json"),
    ];
    candidates
        .into_iter()
        .flatten()
        .any(|p| contains_personal_db(&p))
}

fn contains_personal_db(path: &Path) -> bool {
    fs::read_to_string(path)
        .map(|s| s.contains("\"personal_db\""))
        .unwrap_or(false)
}

/// Shows the one-time nudge notification if -- and only if -- neither the
/// CLI symlink nor any MCP host is configured. Safe to call on every
/// launch: after the first call (whichever way it resolves), the marker
/// file makes every subsequent call a no-op.
pub fn maybe_nudge(app: &AppHandle) {
    let marker = match marker_path(app) {
        Ok(p) => p,
        Err(e) => {
            eprintln!("onboarding nudge: {e}");
            return;
        }
    };
    if marker.exists() {
        return;
    }

    let cli_installed = cli_install::describe_state(app) == LinkState::Correct;
    if cli_installed || any_mcp_target_configured() {
        // Already at least partially set up -- don't nudge, but still drop
        // the marker so we don't keep re-checking configs on every launch.
        let _ = write_marker(&marker);
        return;
    }

    if let Err(e) = app
        .notification()
        .builder()
        .title("Set up PersonalDB")
        .body("Install the command line tool and connect your AI apps from the tray menu.")
        .show()
    {
        eprintln!("onboarding nudge notification failed: {e}");
    }
    let _ = write_marker(&marker);
}

fn write_marker(marker: &Path) -> std::io::Result<()> {
    if let Some(parent) = marker.parent() {
        fs::create_dir_all(parent)?;
    }
    fs::write(marker, b"1")
}
