from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

LABEL = "com.personal_db.scheduler"


def plist_path() -> Path:
    return Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"


def build_plist(pdb_path: str, root: Path, interval_seconds: int, log_path: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{pdb_path}</string>
    <string>--root</string><string>{root}</string>
    <string>sync</string>
    <string>--due</string>
  </array>
  <key>StartInterval</key><integer>{interval_seconds}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{log_path}</string>
  <key>StandardErrorPath</key><string>{log_path}</string>
</dict>
</plist>
"""


def install(root: Path, interval_seconds: int = 600) -> Path:
    pdb_path = shutil.which("personal-db") or "personal-db"
    log_path = root / "state" / "scheduler.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_plist(pdb_path, root, interval_seconds, log_path))
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)  # ignore if not loaded
    subprocess.run(["launchctl", "load", str(p)], check=True)
    return p


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
