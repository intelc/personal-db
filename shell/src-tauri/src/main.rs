// Prevents an extra console window from popping up on Windows in release
// builds; harmless (and inert) on the macOS-only target this milestone
// actually ships for.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod cli_install;
mod daemon;
mod mcp_connect;
mod onboarding;
mod updater;

#[cfg(target_os = "macos")]
use tauri::ActivationPolicy;
use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItem, MenuItemBuilder, SubmenuBuilder},
    tray::TrayIconBuilder,
    AppHandle,
};
use tauri_plugin_autostart::ManagerExt;

/// Exposed to the WebView so the bundled `daemon-down.html` fallback page's
/// Retry button can re-run the bootstrap flow without a full app restart.
/// Every other call site (tray clicks, first-launch) calls
/// `daemon::open_dashboard` directly from Rust instead of round-tripping
/// through `invoke`.
#[tauri::command]
async fn open_dashboard(app: AppHandle) -> Result<(), String> {
    daemon::open_dashboard(&app).await
}

fn spawn_open_dashboard(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = daemon::open_dashboard(&handle).await {
            eprintln!("open_dashboard failed: {e}");
        }
    });
}

/// Like `spawn_open_dashboard`, but for tray items that should land on a
/// page other than the main dashboard -- e.g. "Health" opens `/health`
/// instead of `/`. See `daemon::open_page`.
fn spawn_open_page(app: &AppHandle, next_path: &'static str) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = daemon::open_page(&handle, next_path).await {
            eprintln!("open_page({next_path}) failed: {e}");
        }
    });
}

fn spawn_sync_now(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = daemon::sync_now(&handle).await {
            eprintln!("sync_now failed: {e}");
        }
    });
}

/// Runs `cli_install::install_or_repair` on a background thread (it may
/// block for a while on the `osascript` admin-privileges prompt) and
/// updates the menu item's title + shows a notification once it settles.
fn spawn_install_cli(app: &AppHandle, item: MenuItem<tauri::Wry>) {
    let handle = app.clone();
    std::thread::spawn(move || match cli_install::install_or_repair(&handle) {
        Ok(link) => {
            let _ = item.set_text(cli_install::menu_title(cli_install::LinkState::Correct));
            daemon::notify(
                &handle,
                "Command Line Tool Installed",
                &format!("personal-db is now available at {}", link.display()),
            );
        }
        Err(e) => {
            daemon::notify(&handle, "Command Line Tool Install Failed", &e);
        }
    });
}

/// Runs `<cli> mcp install <target>` on a background thread and reports the
/// result via a native notification.
fn spawn_mcp_connect(app: &AppHandle, target: &'static str, label: &'static str) {
    let handle = app.clone();
    std::thread::spawn(move || match mcp_connect::install(&handle, target) {
        Ok(detail) => daemon::notify(&handle, &format!("Connected to {label}"), &detail),
        Err(e) => daemon::notify(&handle, &format!("Failed to connect to {label}"), &e),
    });
}

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        // Lets daemon.rs spawn the frozen daemon payload (bundle.externalBin
        // in tauri.conf.json) as a sidecar when nothing answers health --
        // see daemon.rs::try_start_sidecar.
        .plugin(tauri_plugin_shell::init())
        // Actionable prompts (Install/Later/Restart) for the self-update
        // flow -- see updater.rs. Notification stays for passive info.
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(daemon::SidecarState::default())
        .manage(daemon::HealthState::default())
        .manage(daemon::VersionDriftState::default())
        .invoke_handler(tauri::generate_handler![open_dashboard])
        .setup(|app| {
            // No dock icon / app switcher entry -- this is a menu-bar-only
            // app. macOS-only API; other platforms would need their own
            // equivalent (not in scope for this milestone).
            #[cfg(target_os = "macos")]
            app.set_activation_policy(ActivationPolicy::Accessory);

            let open_item =
                MenuItemBuilder::with_id("open_dashboard", "Open Dashboard").build(app)?;
            let sync_item = MenuItemBuilder::with_id("sync_now", "Sync Now").build(app)?;
            let status_item = MenuItemBuilder::with_id("status", "Health").build(app)?;
            // Reflects the *current* launch-agent registration state at
            // build time; toggling later updates the live item via
            // `set_checked` in the event handler below rather than
            // rebuilding the whole menu.
            let check_updates_item =
                MenuItemBuilder::with_id("check_updates", "Check for Updates…").build(app)?;
            let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
            let start_login_item =
                CheckMenuItemBuilder::with_id("start_at_login", "Start at Login")
                    .checked(autostart_enabled)
                    .build(app)?;

            // "Install Command Line Tool..." / "Connect AI Apps" --
            // DMG-user onboarding: exposes the embedded CLI (see
            // packaging/cli/personal-db, cli_install.rs) on PATH and wires
            // it into Claude Code / Claude Desktop / Cursor's MCP configs
            // (mcp_connect.rs), matching how the daemon-side `personal-db
            // mcp install` already works for a venv install.
            let app_handle = app.handle().clone();
            let cli_state = cli_install::describe_state(&app_handle);
            let install_cli_item =
                MenuItemBuilder::with_id("install_cli", cli_install::menu_title(cli_state))
                    .build(app)?;

            let connect_items: Vec<_> = mcp_connect::TARGETS
                .iter()
                .copied()
                .map(|(id, label)| {
                    MenuItemBuilder::with_id(format!("connect_{id}"), label).build(app)
                })
                .collect::<Result<_, _>>()?;
            let connect_submenu = {
                let mut builder = SubmenuBuilder::new(app, "Connect AI Apps");
                for item in &connect_items {
                    builder = builder.item(item);
                }
                builder.build()?
            };

            let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;

            let menu = MenuBuilder::new(app)
                .items(&[&open_item, &sync_item, &status_item])
                .separator()
                .items(&[&install_cli_item, &connect_submenu])
                .separator()
                .items(&[&check_updates_item, &start_login_item])
                .separator()
                .items(&[&quit_item])
                .build()?;

            let start_login_item_for_toggle = start_login_item.clone();
            let install_cli_item_for_update = install_cli_item.clone();
            let _tray = TrayIconBuilder::with_id("main")
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(true)
                .tooltip("PersonalDB")
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "open_dashboard" => spawn_open_dashboard(app),
                    "status" => spawn_open_page(app, "/health"),
                    "sync_now" => spawn_sync_now(app),
                    "install_cli" => spawn_install_cli(app, install_cli_item_for_update.clone()),
                    "connect_claude_code" => spawn_mcp_connect(app, "claude_code", "Claude Code"),
                    "connect_claude_desktop" => {
                        spawn_mcp_connect(app, "claude_desktop", "Claude Desktop")
                    }
                    "connect_cursor" => spawn_mcp_connect(app, "cursor", "Cursor"),
                    "check_updates" => updater::spawn_check(app),
                    "start_at_login" => {
                        // `tauri-plugin-autostart` is the *interim*
                        // mechanism for "Start at Login" -- see
                        // packaging/README.md and
                        // packaging/smappservice/RegisterAgent.swift for the
                        // documented TODO to move to SMAppService once the
                        // app is signed (autostart's LaunchAgent plist
                        // approach works unsigned, which SMAppService does
                        // not reliably).
                        let mgr = app.autolaunch();
                        let currently_enabled = mgr.is_enabled().unwrap_or(false);
                        let result = if currently_enabled {
                            mgr.disable()
                        } else {
                            mgr.enable()
                        };
                        match result {
                            Ok(()) => {
                                let _ = start_login_item_for_toggle.set_checked(!currently_enabled);
                            }
                            Err(e) => eprintln!("autostart toggle failed: {e}"),
                        }
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            // First launch: open the dashboard immediately, same as clicking
            // "Open Dashboard" from the tray.
            spawn_open_dashboard(&app.handle().clone());

            // Standing health poll: badges the tray with "!" (and a
            // tooltip naming the failing trackers) when the daemon reports
            // `repeated_sync_failures`, self-heals a daemon that drops off
            // entirely, and detects+restarts a daemon left running an older
            // version than this shell (the "zombie daemon after
            // self-update" bug) -- see daemon::poll_health_status and
            // daemon::check_version_drift. `tokio::time::interval`'s first
            // `.tick()` fires immediately rather than after one full
            // `HEALTH_POLL_INTERVAL`, so this loop's very first iteration
            // runs right at startup -- exactly the moment a version-drift
            // check matters most (right after a self-update relaunch) --
            // with no separate startup-only check needed.
            let health_poll_handle = app.handle().clone();
            tauri::async_runtime::spawn(async move {
                let mut interval = tokio::time::interval(daemon::HEALTH_POLL_INTERVAL);
                loop {
                    interval.tick().await;
                    daemon::poll_health_status(&health_poll_handle).await;
                }
            });

            // Passive update check ~60s after launch (delayed so it never
            // competes with startup / the daemon bootstrap): only surfaces
            // UI when an update actually exists -- see updater.rs.
            updater::spawn_startup_check(&app.handle().clone());

            // One-time nudge toward the Set Up items above, if the user
            // hasn't touched either the CLI symlink or any MCP host yet
            // (see onboarding.rs -- no-ops on every launch after the first).
            onboarding::maybe_nudge(&app_handle);

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|_app, event| {
            // Menu-bar app: closing the last window must NOT quit the app.
            // Without this, "Open Dashboard" (which closes any existing
            // window before/while creating the new one) could race into a
            // zero-window state and take the tray icon down with it. An
            // explicit Quit (app.exit(0)) carries an exit code and is let
            // through.
            if let tauri::RunEvent::ExitRequested { api, code, .. } = event {
                if code.is_none() {
                    api.prevent_exit();
                }
            }
        });
}
