fn main() {
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(
        tauri_build::AppManifest::new().commands(&["open_dashboard", "run_tray_action"]),
    ))
    .expect("failed to build PersonalDB's Tauri permissions");
}
