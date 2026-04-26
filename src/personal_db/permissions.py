from __future__ import annotations

import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PermissionResult:
    granted: bool
    reason: str


def probe_sqlite_access(path: Path) -> PermissionResult:
    """Try to open a SQLite file with immutable=1. Distinguish FDA-deny from missing.

    immutable=1 tells SQLite the file won't change, so it skips the locking
    protocol entirely. This lets us probe DBs held by always-running apps
    (Chrome, Messages with active sync, etc.) without copying.
    """
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
        con.execute("SELECT name FROM sqlite_master LIMIT 1").fetchall()
        con.close()
        return PermissionResult(granted=True, reason="ok")
    except sqlite3.OperationalError as e:
        msg = str(e).lower()
        if "authorization denied" in msg or "operation not permitted" in msg:
            return PermissionResult(granted=False, reason=f"FDA denied: {e}")
        if "unable to open" in msg and not path.exists():
            return PermissionResult(granted=False, reason=f"file missing: {path}")
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
