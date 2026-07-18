// RegisterAgent.swift
//
// REFERENCE CODE ONLY -- not compiled or wired into shell/'s build. This is
// the future replacement for tauri-plugin-autostart's "Start at Login" tray
// item (see shell/src-tauri/src/main.rs's TODO comment on that item), once:
//   1. PersonalDB.app is signed with a stable Developer ID (SMAppService
//      registration is tied to the app's bundle identity; an unsigned or
//      ad-hoc-signed app can't rely on it the same way launchd plists work
//      today), and
//   2. the frozen daemon sidecar (packaging/freeze-daemon.sh's payload) is
//      actually wired into the Tauri bundle as Contents/MacOS/<sidecar> +
//      Contents/Resources/python/ (see packaging/README.md).
//
// Why SMAppService over the plain LaunchAgent plist tauri-plugin-autostart
// installs to ~/Library/LaunchAgents: SMAppService (macOS 13+,
// ServiceManagement framework) registers a plist that ships *inside* the
// signed app bundle (Contents/Library/LaunchAgents/<label>.plist) instead of
// writing a loose file into the user's Library at runtime. That means:
//   - System Settings > General > Login Items shows the entry with the
//     app's real name/icon, and the user can manage it from there directly
//     (rather than only from PersonalDB's own tray menu).
//   - Uninstalling is just deleting the .app -- no orphaned
//     ~/Library/LaunchAgents/*.plist left behind (a real problem with the
//     plain-plist approach if a user just drags the app to the Trash).
//   - `.status` gives a real registration/approval state (see below)
//     instead of "reimplement launchctl list parsing" to guess.
//
// This file demonstrates the registration + status-polling API shape a
// small Rust<->Swift bridge (or an `swift-rs`-based Tauri plugin, since
// swift-rs is already a transitive dependency of the tauri crate on macOS)
// would wrap. It is deliberately NOT built by the current shell/ Cargo
// project -- compile it standalone to check it against a real SDK
// (`swiftc -parse RegisterAgent.swift` requires macOS 13+ SDK headers) once
// you're ready to do this integration; it was written and reviewed against
// Apple's documented ServiceManagement API but not compiled in this
// environment.

import Foundation
import ServiceManagement

/// Thin wrapper around the SMAppService agent registration this app would
/// use once signed. `plistName` must match a plist that ships at
/// `Contents/Library/LaunchAgents/<plistName>` inside the .app bundle --
/// see com.personal_db.daemon.plist in this directory for the template.
@available(macOS 13.0, *)
enum PersonalDBAgent {
    static let plistName = "com.personal_db.daemon.plist"
    static let service = SMAppService.agent(plistName: plistName)

    /// Register the agent (equivalent to "Start at Login" -> on). Throws if
    /// the user needs to approve it in System Settings first (macOS shows
    /// its own prompt/banner in that case) or if the plist is malformed.
    static func register() throws {
        try service.register()
    }

    /// Unregister the agent ("Start at Login" -> off).
    static func unregister() throws {
        try service.unregister()
    }

    /// Human-readable snapshot of the current state. Callers (the tray menu)
    /// should poll this rather than trusting the result of `register()`
    /// alone -- `.status` after registering can legitimately be
    /// `.requiresApproval` if this is the first time the user has enabled
    /// a login item for this app, and the checkbox should reflect that
    /// rather than silently claiming "enabled".
    static func statusDescription() -> String {
        switch service.status {
        case .notRegistered:
            return "not registered"
        case .enabled:
            return "enabled"
        case .requiresApproval:
            return "requires approval in System Settings > General > Login Items"
        case .notFound:
            return "not found (plist missing or malformed)"
        @unknown default:
            return "unknown SMAppService.Status case"
        }
    }
}

// MARK: - Standalone poll demo
//
// Not part of the public API above; illustrates the polling loop the tray
// menu's "Start at Login" checkbox would run after calling register(), since
// `.requiresApproval` can transition to `.enabled` asynchronously (the user
// approving it in System Settings, outside this process).

@available(macOS 13.0, *)
func demoRegisterAndPoll() {
    do {
        try PersonalDBAgent.register()
        print("registration call succeeded; status: \(PersonalDBAgent.statusDescription())")
    } catch {
        print("registration failed: \(error)")
        return
    }

    for _ in 0..<10 {
        Thread.sleep(forTimeInterval: 1.0)
        let status = PersonalDBAgent.statusDescription()
        print("status: \(status)")
        if status == "enabled" {
            break
        }
    }
}
