//! Self-update flow (tauri-plugin-updater, Rust-side only -- no JS updater
//! API is exposed to the WebView).
//!
//! Two entry points:
//!   - `spawn_check(app, Interactive)` -- the tray's "Check for Updates..."
//!     item. Always surfaces a result: an update-available prompt, a
//!     "you're up to date" dialog, or an error dialog.
//!   - `spawn_periodic_checks(app)` -- passive checks ~60s after launch and
//!     then twice daily. The delay keeps first-launch startup snappy and
//!     lets the daemon bootstrap flow settle first. Passive checks are
//!     silent; an available version is instead retained as state for the
//!     dashboard's sidebar update indicator.
//!
//! The update feed is `plugins.updater` in tauri.conf.json: a static
//! `latest.json` on the GitHub release (assembled by packaging/release.sh),
//! signature-checked against the minisign pubkey embedded there. Dialogs
//! use tauri-plugin-dialog (actionable Install/Later/Restart prompts for
//! explicit checks only).

use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};

use serde::Serialize;
use tauri::{AppHandle, Emitter, Manager};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons, MessageDialogKind};
use tauri_plugin_updater::UpdaterExt;

/// How long after launch the passive background check fires.
const STARTUP_CHECK_DELAY: Duration = Duration::from_secs(60);
/// Check again while the app remains open. Twice daily keeps update discovery
/// timely without turning the static update feed into a polling load.
const PERIODIC_CHECK_INTERVAL: Duration = Duration::from_secs(12 * 60 * 60);
/// Serializes updater work across the passive scheduler and the explicit
/// tray/Settings action. It also retains the latest available version so the
/// remote dashboard can redraw its indicator after navigation or a reload.
#[derive(Default)]
pub struct UpdateCheckState {
    in_progress: AtomicBool,
    ready: Mutex<Option<ReadyUpdate>>,
}

#[derive(Clone)]
struct ReadyUpdate {
    version: String,
}

/// Deliberately small bridge payload. Release notes and updater internals stay
/// native; the browser only learns whether a user-actionable update exists.
#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct UpdateStatus {
    pub available: bool,
    pub version: Option<String>,
}

impl UpdateCheckState {
    fn try_start(&self) -> bool {
        self.in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }

    fn finish(&self) {
        self.in_progress.store(false, Ordering::Release);
    }

    pub fn status(&self) -> UpdateStatus {
        let ready = self
            .ready
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        UpdateStatus {
            available: ready.is_some(),
            version: ready.as_ref().map(|update| update.version.clone()),
        }
    }

    fn mark_ready(&self, version: impl Into<String>) -> UpdateStatus {
        let mut ready = self
            .ready
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *ready = Some(ReadyUpdate {
            version: version.into(),
        });
        UpdateStatus {
            available: true,
            version: ready.as_ref().map(|update| update.version.clone()),
        }
    }

    fn clear_ready(&self) -> UpdateStatus {
        let mut ready = self
            .ready
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        *ready = None;
        UpdateStatus {
            available: false,
            version: None,
        }
    }
}

/// Whether a check was explicitly requested from the tray menu (surfaces
/// every outcome as a dialog) or is a passive check (completely silent).
#[derive(Clone, Copy, PartialEq)]
pub enum CheckKind {
    Interactive,
    Passive,
}

/// Tray-menu entry point: run a check now, reporting every outcome.
pub fn spawn_check(app: &AppHandle) {
    spawn(app, CheckKind::Interactive, Duration::ZERO);
}

/// Schedule passive checks without blocking startup. The scheduler waits
/// before its first request and waits a full interval after each completed
/// check, so a slow download/install can never create a backlog of checks.
pub fn spawn_periodic_checks(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(STARTUP_CHECK_DELAY).await;
        loop {
            run_check(&handle, CheckKind::Passive).await;
            tokio::time::sleep(PERIODIC_CHECK_INTERVAL).await;
        }
    });
}

fn spawn(app: &AppHandle, kind: CheckKind, delay: Duration) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if !delay.is_zero() {
            tokio::time::sleep(delay).await;
        }
        run_check(&handle, kind).await;
    });
}

async fn run_check(app: &AppHandle, kind: CheckKind) {
    // Acquire only when the delayed task actually begins. Holding the gate
    // while waiting for the startup delay would make a manual check appear
    // busy even though no network request had started yet.
    if !app.state::<UpdateCheckState>().try_start() {
        // A manual request still gets feedback, but intentionally as a
        // notification rather than a second dialog: the active check may be
        // showing an actionable update prompt already.
        if kind == CheckKind::Interactive {
            crate::daemon::notify(
                app,
                "Update Check Already Running",
                "PersonalDB is already checking for an update.",
            );
        }
        return;
    }

    if let Err(e) = check_and_prompt(app, kind).await {
        eprintln!("update check failed: {e}");
        if kind == CheckKind::Interactive {
            app.dialog()
                .message(format!("Could not check for updates.\n\n{e}"))
                .title("PersonalDB Update")
                .kind(MessageDialogKind::Error)
                .blocking_show();
        }
    }
    // Release only after an interactive error dialog closes. Otherwise a
    // second menu click could open another check behind the visible dialog.
    app.state::<UpdateCheckState>().finish();
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
        publish_status(app, app.state::<UpdateCheckState>().clear_ready());
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

    publish_status(
        app,
        app.state::<UpdateCheckState>()
            .mark_ready(update.version.to_string()),
    );

    // A background check must never steal focus with a native dialog. The
    // dashboard's persistent indicator is the opt-in affordance to begin the
    // exact same install/restart flow as the explicit menu action.
    if kind == CheckKind::Passive {
        return Ok(());
    }

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

    // The replacement is on disk now, even if the person defers the restart.
    // Do not leave a misleading "ready to install" indicator behind.
    publish_status(app, app.state::<UpdateCheckState>().clear_ready());

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

fn publish_status(app: &AppHandle, status: UpdateStatus) {
    // The status is always retained in `UpdateCheckState`; this event merely
    // makes an already-open dashboard update immediately. A page navigation
    // or a normal browser that cannot listen remains graceful because its JS
    // fetches the same state through the narrow Tauri command.
    if let Err(e) = app.emit("pdb://update-ready", status) {
        eprintln!("could not publish update status: {e}");
    }
}

#[cfg(test)]
mod tests {
    use super::UpdateCheckState;

    #[test]
    fn update_check_state_allows_only_one_check_at_a_time() {
        let state = UpdateCheckState::default();

        assert!(state.try_start());
        assert!(!state.try_start());

        state.finish();
        assert!(state.try_start());
    }

    #[test]
    fn ready_update_status_survives_navigation_state_reads() {
        let state = UpdateCheckState::default();

        assert_eq!(state.status().available, false);
        assert_eq!(state.mark_ready("0.2.0").version.as_deref(), Some("0.2.0"));
        assert_eq!(state.status().version.as_deref(), Some("0.2.0"));
        assert_eq!(state.clear_ready().available, false);
    }
}
