//! Self-update flow (tauri-plugin-updater, Rust-side only -- no JS updater
//! API is exposed to the WebView).
//!
//! Two entry points:
//!   - `spawn_check(app, Interactive)` -- the tray's "Check for Updates..."
//!     item. Always surfaces a result: an update-available prompt, a
//!     "you're up to date" dialog, or an error dialog.
//!   - `spawn_startup_check(app)` -- passive check ~60s after launch (the
//!     delay keeps first-launch startup snappy and lets the daemon
//!     bootstrap flow settle first). Only surfaces UI when an update
//!     actually exists; "no update" and errors are silent (eprintln only).
//!
//! The update feed is `plugins.updater` in tauri.conf.json: a static
//! `latest.json` on the GitHub release (assembled by packaging/release.sh),
//! signature-checked against the minisign pubkey embedded there. Dialogs
//! use tauri-plugin-dialog (actionable Install/Later/Restart prompts;
//! the notification plugin stays reserved for passive info).

use std::time::Duration;

use tauri::AppHandle;
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// How long after launch the passive background check fires.
const STARTUP_CHECK_DELAY: Duration = Duration::from_secs(60);

/// Whether a check was explicitly requested from the tray menu (surfaces
/// every outcome as a dialog) or is the passive startup check (silent
/// unless an update actually exists).
#[derive(Clone, Copy, PartialEq)]
pub enum CheckKind {
    Interactive,
    Passive,
}

/// Tray-menu entry point: run a check now, reporting every outcome.
pub fn spawn_check(app: &AppHandle) {
    spawn(app, CheckKind::Interactive, Duration::ZERO);
}

/// Launch-time entry point: passive check after `STARTUP_CHECK_DELAY`,
/// without blocking startup.
pub fn spawn_startup_check(app: &AppHandle) {
    spawn(app, CheckKind::Passive, STARTUP_CHECK_DELAY);
}

fn spawn(app: &AppHandle, kind: CheckKind, delay: Duration) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
        if let Err(e) = check_and_prompt(&handle, kind).await {
            eprintln!("update check failed: {e}");
            if kind == CheckKind::Interactive {
                handle
                    .dialog()
                    .message(format!("Could not check for updates.\n\n{e}"))
                    .title("PersonalDB Update")
                    .kind(MessageDialogKind::Error)
                    .blocking_show();
            }
        }
    });
}

/// The whole flow: check -> (maybe) offer -> download+install -> offer
/// restart. Runs on an async-runtime worker thread, so the
/// `blocking_show()` dialog calls below block only that worker, never the
/// main event loop (tauri-plugin-dialog documents blocking_show as "don't
/// call on the main thread" -- this is exactly the supported usage).
async fn check_and_prompt(app: &AppHandle, kind: CheckKind) -> Result<(), String> {
    let updater = app.updater().map_err(|e| format!("updater init: {e}"))?;
    let update = updater.check().await.map_err(|e| e.to_string())?;

    let Some(update) = update else {
        if kind == CheckKind::Interactive {
            let current = app.package_info().version.to_string();
            app.dialog()
                .message(format!("You're up to date (v{current})"))
                .title("PersonalDB Update")
                .kind(MessageDialogKind::Info)
                .blocking_show();
        }
        return Ok(());
    };

    // `body` carries the `notes` field of latest.json (release.sh puts the
    // release notes there).
    let notes = update
        .body
        .as_deref()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("(no release notes)");
    let message = format!(
        "PersonalDB {} is available (you have {}).\n\nWhat's new:\n{}",
        update.version, update.current_version, notes
    );
    let install = app
        .dialog()
        .message(message)
        .title("Update Available")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Install".into(),
            "Later".into(),
        ))
        .blocking_show();
    if !install {
        return Ok(());
    }

    // No fine-grained progress UI -- the download is a background task the
    // user already consented to; chunk callbacks are intentionally no-ops.
    update
        .download_and_install(|_chunk, _total| {}, || {})
        .await
        .map_err(|e| format!("download/install failed: {e}"))?;

    let restart = app
        .dialog()
        .message(format!(
            "PersonalDB {} is installed. Restart now to start using it?",
            update.version
        ))
        .title("Update Installed")
        .kind(MessageDialogKind::Info)
        .buttons(MessageDialogButtons::OkCancelCustom(
            "Restart".into(),
            "Later".into(),
        ))
        .blocking_show();
    if restart {
        app.restart();
    }
    Ok(())
}
