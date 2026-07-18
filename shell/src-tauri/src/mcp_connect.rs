//! Tray "Connect AI Apps" submenu: runs the embedded CLI's
//! `mcp install <target>` for Claude Code / Claude Desktop / Cursor.
//!
//! CORRECTNESS NOTE (this is the whole reason this module exists instead of
//! just shelling out to whatever): `services/wizard/mcp_setup.py`'s
//! `_personal_db_path()` writes an absolute path into the host app's config
//! (Claude Code's `claude mcp add`, or the `command` field of Cursor's /
//! Claude Desktop's JSON config) -- and that path is derived from *this
//! process's own argv[0]* (mirroring the argv[0]-preference pattern in
//! `services/daemon/install.py::_resolve_cli_binary`), preferring the
//! `/usr/local/bin/personal-db` symlink when it resolves to the same
//! binary. So whichever absolute path we `exec` here is exactly the path
//! that ends up durably stored in the host's config -- getting it wrong
//! means every future MCP tool call from that host spawns a stale or
//! nonexistent binary.
//!
//! We therefore invoke the *stable* path ourselves whenever it's available
//! (`cli_install::link_path()`, when it's correctly linked to this bundle),
//! falling back to this bundle's own `Contents/Resources/cli/personal-db`
//! wrapper otherwise -- the same preference order `_personal_db_path()`
//! applies on the Python side, kept in sync deliberately.

use std::path::PathBuf;
use std::process::Command;

use tauri::AppHandle;

use crate::cli_install::{self, LinkState};

/// (config key, human label) for each supported MCP host, in tray
/// menu order.
pub const TARGETS: [(&str, &str); 3] = [
    ("claude_code", "Claude Code"),
    ("claude_desktop", "Claude Desktop"),
    ("cursor", "Cursor"),
];

/// The CLI path to invoke -- see module docs for why this has to match
/// `_personal_db_path()`'s own preference order.
pub fn invoke_path(app: &AppHandle) -> Result<PathBuf, String> {
    let wrapper = cli_install::wrapper_path(app)?;
    let link = cli_install::link_path();
    if cli_install::describe_state_at(&wrapper, &link) == LinkState::Correct {
        Ok(link)
    } else {
        Ok(wrapper)
    }
}

/// Runs `<invoke_path> mcp install <target>` and returns the CLI's own
/// stdout (or stderr on failure) as the human-readable result.
pub fn install(app: &AppHandle, target: &str) -> Result<String, String> {
    let cli = invoke_path(app)?;
    if !cli.exists() {
        return Err(format!(
            "personal-db CLI not found at {} -- install the Command Line Tool from the tray first",
            cli.display()
        ));
    }
    let output = Command::new(&cli)
        .args(["mcp", "install", target])
        .output()
        .map_err(|e| format!("failed to run {}: {e}", cli.display()))?;
    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
    if output.status.success() {
        Ok(if stdout.is_empty() {
            "done".to_string()
        } else {
            stdout
        })
    } else if !stderr.is_empty() {
        Err(stderr)
    } else if !stdout.is_empty() {
        Err(stdout)
    } else {
        Err(format!("mcp install {target} exited with {}", output.status))
    }
}
