from __future__ import annotations

import shutil
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PermissionResult:
    granted: bool
    reason: str


def probe_sqlite_access(path: Path) -> PermissionResult:
    """Try to open a SQLite file read-only. Distinguish FDA-deny from missing.

    Some apps (Chrome, etc.) hold a lock on their DB while running. If the direct
    open fails with 'database is locked', fall back to copy-first — if we can copy
    the file, FDA is granted regardless of the lock.
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        con.close()
        return PermissionResult(granted=True, reason="ok")
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "authorization denied" in msg or "operation not permitted" in msg:
            return PermissionResult(granted=False, reason=f"FDA denied: {e}")
        if "unable to open" in msg and not path.exists():
            return PermissionResult(granted=False, reason=f"file missing: {path}")
        if "database is locked" in msg:
            return _probe_via_copy(path)
        return PermissionResult(granted=False, reason=str(e))


def _probe_via_copy(path: Path) -> PermissionResult:
    """Fallback: copy the locked DB to a tempdir and try to read the copy.

    If the copy succeeds, FDA is granted (the lock was the only obstacle).
    If the copy fails with EPERM, FDA is the real issue.
    """
    try:
        with tempfile.TemporaryDirectory() as td:
            copy = Path(td) / "probe.db"
            shutil.copy2(path, copy)
            con = sqlite3.connect(f"file:{copy}?mode=ro", uri=True)
            con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
            con.close()
        return PermissionResult(granted=True, reason="ok (via copy; source was locked)")
    except (OSError, sqlite3.Error) as e:
        msg = str(e).lower()
        if "permission denied" in msg or "operation not permitted" in msg:
            return PermissionResult(granted=False, reason=f"FDA denied: {e}")
        return PermissionResult(granted=False, reason=str(e))


def responsible_binary_path() -> Path:
    """The actual binary TCC will see when probing protected files.

    sys.executable points at the venv shim, but TCC follows the symlink to
    the real interpreter. Return the resolved path so the wizard can tell
    the user exactly which binary to grant FDA to.
    """
    return Path(sys.executable).resolve()


def open_fda_settings_pane() -> None:
    """Open System Settings -> Privacy & Security -> Full Disk Access."""
    subprocess.run(
        [
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        ],
        check=False,
    )
