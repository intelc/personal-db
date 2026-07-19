from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from personal_db.core.global_writes import blocked_reason

LABEL = "com.personal_db.daemon"

# Module-level so tests can monkeypatch.
_LAUNCHAGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()


def plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{LABEL}.plist"


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
    <string>dev</string><string>daemon</string><string>run</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def _resolve_cli_binary() -> str | None:
    """Path to the personal-db executable the daemon plist should invoke.

    Prefer the binary that is running THIS process (sys.argv[0]): the user who
    runs `<venv>/bin/personal-db daemon install` expects the daemon to run that
    same install, not whatever stale copy happens to shadow it on PATH (e.g. an
    old `uv tool install` shim in ~/.local/bin). Fall back to PATH lookup only
    when argv[0] isn't a personal-db entry point (e.g. invoked via `python -m`).
    """
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == "personal-db" and argv0.is_file():
        return str(argv0)
    return shutil.which("personal-db")


def install(root: Path) -> dict:
    """Install the launchd daemon plist and load it.

    Returns a dict with key ``plist``: :class:`~pathlib.Path` to the installed
    plist file.
    """
    reason = blocked_reason(root)
    if reason:
        raise RuntimeError(reason)
    pdb_path = _resolve_cli_binary()
    if pdb_path is None:
        raise RuntimeError(
            "personal-db not found on PATH. "
            "Activate the virtualenv or install the package before running `daemon install`."
        )
    log_path = root / "state" / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_plist(pdb_path, root, log_path))
    # Idempotent reload: unload first in case a previous version is loaded, then load fresh.
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    try:
        subprocess.run(["launchctl", "load", str(p)], check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"launchctl failed to load the daemon plist ({p}): {exc}") from exc
    return {"plist": p}


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
