from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from xml.sax.saxutils import escape

LABEL = "com.personal_db.daemon"
OLD_LABEL = "com.personal_db.scheduler"

# Module-level so tests can monkeypatch.
_LAUNCHAGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()


def plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{LABEL}.plist"


def _old_plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{OLD_LABEL}.plist"


def build_plist(pdb_path: str, root: Path, log_path: Path) -> str:
    pdb = escape(str(pdb_path))
    r = escape(str(root))
    log = escape(str(log_path))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{pdb}</string>
    <string>--root</string><string>{r}</string>
    <string>daemon</string><string>run</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _migrate_old_plist() -> bool:
    """Unload + remove the old scheduler plist if present. Returns True if a migration happened."""
    old = _old_plist_path()
    if not old.exists():
        return False
    subprocess.run(["launchctl", "unload", str(old)], capture_output=True)
    old.unlink()
    return True


def install(root: Path) -> dict:
    """Install the launchd daemon plist and load it.

    Returns a dict with keys:
      - ``plist``: :class:`~pathlib.Path` to the installed plist file.
      - ``migrated_old_scheduler``: ``True`` if the old
        ``com.personal_db.scheduler.plist`` was removed during this call.
    """
    pdb_path = shutil.which("personal-db")
    if pdb_path is None:
        raise RuntimeError(
            "personal-db not found on PATH. "
            "Activate the virtualenv or install the package before running `daemon install`."
        )
    log_path = root / "state" / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    migrated = _migrate_old_plist()

    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_plist(pdb_path, root, log_path))
    # Idempotent reload: unload first in case a previous version is loaded, then load fresh.
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    try:
        subprocess.run(["launchctl", "load", str(p)], check=True)
    except subprocess.CalledProcessError as exc:
        msg = f"launchctl failed to load the daemon plist ({p}): {exc}"
        if migrated:
            msg += (
                " Note: the old com.personal_db.scheduler.plist was already removed."
                " Re-run `personal-db daemon install` after resolving the issue."
            )
        raise RuntimeError(msg) from exc
    return {"plist": p, "migrated_old_scheduler": migrated}


def uninstall() -> None:
    p = plist_path()
    if p.exists():
        subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
        p.unlink()


def status() -> str:
    p = plist_path()
    if not p.exists():
        return "not installed"
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    if r.returncode != 0:
        return f"plist exists but not loaded: {p}"
    return r.stdout
