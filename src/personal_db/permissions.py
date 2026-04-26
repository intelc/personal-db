from __future__ import annotations

import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PermissionResult:
    granted: bool
    reason: str


def probe_sqlite_access(path: Path) -> PermissionResult:
    """Try to open a SQLite file read-only. Distinguish FDA-deny from missing."""
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
        return PermissionResult(granted=False, reason=str(e))


def open_fda_settings_pane() -> None:
    """Open System Settings -> Privacy & Security -> Full Disk Access."""
    subprocess.run(
        [
            "open",
            "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles",
        ],
        check=False,
    )
