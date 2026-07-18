// Prevents an extra console window from popping up on Windows in release
// builds; harmless (and inert) on the macOS-only target this milestone
// actually ships for.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod daemon;

#[cfg(target_os = "macos")]
use tauri::ActivationPolicy;
use tauri::{
    menu::{CheckMenuItemBuilder, MenuBuilder, MenuItemBuilder},
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

fn spawn_sync_now(app: &AppHandle) {
    let handle = app.clone();
    tauri::async_runtime::spawn(async move {
        if let Err(e) = daemon::sync_now(&handle).await {
            eprintln!("sync_now failed: {e}");
        }
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
        .manage(daemon::SidecarState::default())
        .invoke_handler(tauri::generate_handler![open_dashboard])
        .setup(|app| {
            // No dock icon / app switcher entry -- this is a menu-bar-only
            // app. macOS-only API; other platforms would need their own
            // equivalent (not in scope for this milestone).
            #[cfg(target_os = "macos")]
            app.set_activation_policy(ActivationPolicy::Accessory);

            let open_item = MenuItemBuilder::with_id("open_dashboard", "Open Dashboard")
                .build(app)?;
            let sync_item = MenuItemBuilder::with_id("sync_now", "Sync Now").build(app)?;
            let status_item = MenuItemBuilder::with_id("status", "Status").build(app)?;
            // Reflects the *current* launch-agent registration state at
            // build time; toggling later updates the live item via
            // `set_checked` in the event handler below rather than
            // rebuilding the whole menu.
            let autostart_enabled = app.autolaunch().is_enabled().unwrap_or(false);
            let start_login_item =
                CheckMenuItemBuilder::with_id("start_at_login", "Start at Login")
                    .checked(autostart_enabled)
                    .build(app)?;
            let quit_item = MenuItemBuilder::with_id("quit", "Quit").build(app)?;

            let menu = MenuBuilder::new(app)
                .items(&[&open_item, &sync_item, &status_item])
                .separator()
                .items(&[&start_login_item])
                .separator()
                .items(&[&quit_item])
                .build()?;

            let start_login_item_for_toggle = start_login_item.clone();
            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
                .show_menu_on_left_click(true)
                .tooltip("PersonalDB")
                .on_menu_event(move |app, event| match event.id().as_ref() {
                    "open_dashboard" | "status" => spawn_open_dashboard(app),
                    "sync_now" => spawn_sync_now(app),
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

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
