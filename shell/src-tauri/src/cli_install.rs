//! "Install Command Line Tool…" tray action.
//!
//! Symlinks the in-bundle CLI wrapper (`Contents/Resources/cli/personal-db`,
//! shipped via `tauri.conf.json`'s `bundle.resources` -- see
//! `packaging/cli/personal-db`) onto PATH at `/usr/local/bin/personal-db`,
//! Ollama-style. This is also the path `services/wizard/mcp_setup.py`
//! prefers once it exists and points at the running bundle (see
//! `mcp_connect.rs` and that module's docstring) -- a stable path that
//! survives the app being resigned/rebuilt, unlike a raw
//! `Contents/Resources/...` path baked into a host app's MCP config.

use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;

use tauri::{AppHandle, Manager};

/// Directory the CLI symlink is installed into. Overridable via
/// `PERSONAL_DB_CLI_LINK_DIR` so tests (and anyone poking at this from a
/// shell) never have to touch the real `/usr/local/bin`.
fn link_dir() -> PathBuf {
    env::var("PERSONAL_DB_CLI_LINK_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/usr/local/bin"))
}

/// Full path to the symlink itself: `<link_dir>/personal-db`.
pub fn link_path() -> PathBuf {
    link_dir().join("personal-db")
}

/// Path to the in-bundle CLI wrapper this symlink should point at.
pub fn wrapper_path(app: &AppHandle) -> Result<PathBuf, String> {
    let resource_dir = app
        .path()
        .resource_dir()
        .map_err(|e| format!("failed to resolve bundle resource dir: {e}"))?;
    Ok(resource_dir.join("cli").join("personal-db"))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkState {
    /// Nothing at the link path.
    Missing,
    /// A symlink exists and resolves to this exact bundle's wrapper.
    Correct,
    /// Something exists at the link path but isn't a symlink to this
    /// bundle's wrapper: dangling, pointing at a different (possibly
    /// moved-away or older) bundle, or an unrelated file. The repair flow
    /// (re-running install) handles all three the same way: force-relink.
    BrokenOrElsewhere,
}

/// Menu title reflecting the current link state -- checkmark when done,
/// otherwise an actionable verb so the user knows clicking it does
/// something.
pub fn menu_title(state: LinkState) -> &'static str {
    match state {
        LinkState::Missing => "Install Command Line Tool…",
        LinkState::Correct => "✓ Command Line Tool Installed",
        LinkState::BrokenOrElsewhere => "Reinstall Command Line Tool…",
    }
}

pub fn describe_state(app: &AppHandle) -> LinkState {
    let wrapper = match wrapper_path(app) {
        Ok(w) => w,
        // If we can't even resolve the resource dir, there's nothing
        // meaningful to compare against -- report as needing (re)install
        // rather than erroring the whole menu build.
        Err(_) => return LinkState::BrokenOrElsewhere,
    };
    describe_state_at(&wrapper, &link_path())
}

/// Pure version of `describe_state`, parameterized on both paths so it's
/// testable without spinning up an `AppHandle`.
pub fn describe_state_at(wrapper: &Path, link: &Path) -> LinkState {
    let target = match fs::read_link(link) {
        Ok(t) => t,
        Err(e) if e.kind() == io::ErrorKind::NotFound => return LinkState::Missing,
        // Exists but reading it as a symlink failed (e.g. it's a regular
        // file, not a symlink at all) -- treat as needing repair.
        Err(_) => return LinkState::BrokenOrElsewhere,
    };
    if paths_equal(&target, wrapper) {
        LinkState::Correct
    } else {
        LinkState::BrokenOrElsewhere
    }
}

fn paths_equal(a: &Path, b: &Path) -> bool {
    // Prefer canonicalize (resolves further symlinks / `..` components);
    // fall back to raw equality if either side doesn't currently exist on
    // disk (e.g. the symlink's recorded target is a bundle that's since
    // been deleted -- still a meaningful "not equal" signal without this
    // erroring out).
    match (a.canonicalize(), b.canonicalize()) {
        (Ok(ca), Ok(cb)) => ca == cb,
        _ => a == b,
    }
}

/// Creates/repairs the CLI symlink so it points at this bundle's wrapper.
///
/// Tries a direct symlink first (works whenever the link directory is
/// user-writable -- e.g. a Homebrew-owned `/usr/local/bin`, or a test's
/// `PERSONAL_DB_CLI_LINK_DIR` override). On permission failure, falls back
/// to an `osascript … with administrator privileges` prompt: this triggers
/// the standard macOS admin dialog, and the OS -- not this process --
/// collects the password.
pub fn install_or_repair(app: &AppHandle) -> Result<PathBuf, String> {
    let wrapper = wrapper_path(app)?;
    if !wrapper.exists() {
        return Err(format!(
            "bundled CLI wrapper not found at {} -- this build wasn't packaged with the cli/ resource (see tauri.conf.json bundle.resources)",
            wrapper.display()
        ));
    }
    let link = link_path();
    install_or_repair_at(&wrapper, &link)?;
    Ok(link)
}

/// Pure version of `install_or_repair`, parameterized on both paths.
pub fn install_or_repair_at(wrapper: &Path, link: &Path) -> Result<(), String> {
    match try_direct_symlink(wrapper, link) {
        Ok(()) => Ok(()),
        Err(direct_err) if direct_err.kind() == io::ErrorKind::PermissionDenied => {
            try_admin_symlink(wrapper, link)
                .map_err(|admin_err| format!("permission denied for a direct symlink, and the admin-privileged fallback also failed: {admin_err}"))
        }
        Err(direct_err) => Err(format!("failed to create symlink: {direct_err}")),
    }
}

fn try_direct_symlink(wrapper: &Path, link: &Path) -> io::Result<()> {
    if let Some(parent) = link.parent() {
        fs::create_dir_all(parent)?;
    }
    // Manual `ln -sf` behavior: std::os::unix::fs::symlink errors if the
    // destination already exists, so remove whatever's there first
    // (dangling symlink, symlink to a different/moved bundle, or even an
    // unrelated file -- all get replaced by the repair flow).
    match fs::symlink_metadata(link) {
        Ok(_) => fs::remove_file(link)?,
        Err(e) if e.kind() == io::ErrorKind::NotFound => {}
        Err(e) => return Err(e),
    }
    std::os::unix::fs::symlink(wrapper, link)
}

fn try_admin_symlink(wrapper: &Path, link: &Path) -> Result<(), String> {
    let shell_cmd = format!(
        "ln -sf {} {}",
        shell_single_quote(&wrapper.to_string_lossy()),
        shell_single_quote(&link.to_string_lossy()),
    );
    let osa_script = format!(
        "do shell script {} with administrator privileges",
        applescript_string_literal(&shell_cmd)
    );
    let status = Command::new("osascript")
        .arg("-e")
        .arg(&osa_script)
        .status()
        .map_err(|e| format!("failed to invoke osascript: {e}"))?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("osascript exited with {status}"))
    }
}

/// POSIX shell single-quoting for safe interpolation into the `ln -sf`
/// command line (handles embedded single quotes the standard way).
fn shell_single_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// AppleScript string-literal quoting for embedding `shell_cmd` inside the
/// `-e` script passed to osascript (distinct from shell quoting above --
/// this is a *second*, outer layer of quoting for a different language).
fn applescript_string_literal(s: &str) -> String {
    let escaped = s.replace('\\', "\\\\").replace('"', "\\\"");
    format!("\"{escaped}\"")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};

    static COUNTER: AtomicU64 = AtomicU64::new(0);

    /// A fresh scratch directory per test, so parallel `cargo test` runs
    /// never collide.
    fn scratch_dir() -> PathBuf {
        let n = COUNTER.fetch_add(1, Ordering::SeqCst);
        let dir =
            env::temp_dir().join(format!("pdb-cli-install-test-{}-{}", std::process::id(), n));
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn make_wrapper(dir: &Path) -> PathBuf {
        let wrapper = dir.join("personal-db-wrapper");
        fs::write(&wrapper, b"#!/bin/sh\necho fake\n").unwrap();
        wrapper
    }

    #[test]
    fn missing_when_no_link_exists() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link = dir.join("linkdir").join("personal-db");
        assert_eq!(describe_state_at(&wrapper, &link), LinkState::Missing);
    }

    #[test]
    fn correct_after_install() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link = dir.join("linkdir").join("personal-db");
        install_or_repair_at(&wrapper, &link).expect("install should succeed");
        assert!(link.is_symlink());
        assert_eq!(describe_state_at(&wrapper, &link), LinkState::Correct);
    }

    #[test]
    fn broken_when_pointing_elsewhere() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let other_wrapper = dir.join("other-bundle-wrapper");
        fs::write(&other_wrapper, b"#!/bin/sh\necho other\n").unwrap();
        let link_dir = dir.join("linkdir");
        fs::create_dir_all(&link_dir).unwrap();
        let link = link_dir.join("personal-db");
        std::os::unix::fs::symlink(&other_wrapper, &link).unwrap();

        assert_eq!(
            describe_state_at(&wrapper, &link),
            LinkState::BrokenOrElsewhere
        );
    }

    #[test]
    fn broken_when_dangling() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link_dir = dir.join("linkdir");
        fs::create_dir_all(&link_dir).unwrap();
        let link = link_dir.join("personal-db");
        std::os::unix::fs::symlink(dir.join("does-not-exist"), &link).unwrap();

        assert_eq!(
            describe_state_at(&wrapper, &link),
            LinkState::BrokenOrElsewhere
        );
    }

    #[test]
    fn repair_overwrites_broken_symlink() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link_dir = dir.join("linkdir");
        fs::create_dir_all(&link_dir).unwrap();
        let link = link_dir.join("personal-db");
        std::os::unix::fs::symlink(dir.join("does-not-exist"), &link).unwrap();

        install_or_repair_at(&wrapper, &link).expect("repair should succeed");
        assert_eq!(describe_state_at(&wrapper, &link), LinkState::Correct);
    }

    #[test]
    fn repair_replaces_non_symlink_file() {
        // If some unrelated file occupies the link path, the repair flow
        // still force-replaces it (matches `ln -sf` semantics).
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link_dir = dir.join("linkdir");
        fs::create_dir_all(&link_dir).unwrap();
        let link = link_dir.join("personal-db");
        fs::write(&link, b"not a symlink").unwrap();

        install_or_repair_at(&wrapper, &link).expect("repair should succeed");
        assert!(link.is_symlink());
        assert_eq!(describe_state_at(&wrapper, &link), LinkState::Correct);
    }

    #[test]
    fn install_creates_missing_parent_dir() {
        let dir = scratch_dir();
        let wrapper = make_wrapper(&dir);
        let link = dir
            .join("nested")
            .join("does")
            .join("not")
            .join("exist")
            .join("personal-db");
        install_or_repair_at(&wrapper, &link).expect("should create parent dirs");
        assert!(link.is_symlink());
    }

    #[test]
    fn menu_titles_are_distinct() {
        let missing = menu_title(LinkState::Missing);
        let correct = menu_title(LinkState::Correct);
        let broken = menu_title(LinkState::BrokenOrElsewhere);
        assert_ne!(missing, correct);
        assert_ne!(correct, broken);
        assert_ne!(missing, broken);
        assert!(correct.contains('✓'));
    }
}
