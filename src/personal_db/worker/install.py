from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from personal_db.worker.enrich import FINANCE_RECEIPT_V1

LABEL = "com.personal_db.enrichment-worker"

# Module-level so tests can monkeypatch.
_LAUNCHAGENTS_DIR = Path("~/Library/LaunchAgents").expanduser()


def plist_path() -> Path:
    return _LAUNCHAGENTS_DIR / f"{LABEL}.plist"


def build_plist(
    pdb_path: str,
    root: Path,
    log_path: Path,
    *,
    kind: str = FINANCE_RECEIPT_V1,
    batch_size: int = 1,
    interval_seconds: float = 900,
    lease_seconds: int = 1200,
) -> str:
    pdb = escape(str(pdb_path))
    r = escape(str(root))
    log = escape(str(log_path))
    kind_ = escape(str(kind))
    batch = escape(str(batch_size))
    interval = escape(str(interval_seconds))
    lease = escape(str(lease_seconds))
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
    <string>worker</string><string>enrich</string>
    <string>--kind</string><string>{kind_}</string>
    <string>--batch-size</string><string>{batch}</string>
    <string>--interval-seconds</string><string>{interval}</string>
    <string>--lease-seconds</string><string>{lease}</string>
  </array>
  <key>KeepAlive</key><true/>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>{log}</string>
  <key>StandardErrorPath</key><string>{log}</string>
</dict>
</plist>
"""


def install(
    root: Path,
    *,
    kind: str = FINANCE_RECEIPT_V1,
    batch_size: int = 1,
    interval_seconds: float = 900,
    lease_seconds: int = 1200,
) -> dict:
    pdb_path = _resolve_personal_db_executable()
    if pdb_path is None:
        raise RuntimeError(
            "personal-db not found on PATH. "
            "Activate the virtualenv or install the package before running `worker install`."
        )
    log_path = root / "state" / "enrichment-worker.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        build_plist(
            pdb_path,
            root,
            log_path,
            kind=kind,
            batch_size=batch_size,
            interval_seconds=interval_seconds,
            lease_seconds=lease_seconds,
        )
    )
    subprocess.run(["launchctl", "unload", str(p)], capture_output=True)
    try:
        subprocess.run(["launchctl", "load", str(p)], check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"launchctl failed to load the worker plist ({p}): {exc}") from exc
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


def info(root: Path) -> dict:
    p = plist_path()
    log_path = root / "state" / "enrichment-worker.log"
    installed = p.exists()
    r = subprocess.run(["launchctl", "list", LABEL], capture_output=True, text=True)
    loaded = installed and r.returncode == 0
    return {
        "label": LABEL,
        "installed": installed,
        "loaded": loaded,
        "plist": str(p),
        "log_path": str(log_path),
        "status": r.stdout if loaded else ("not installed" if not installed else f"plist exists but not loaded: {p}"),
        "last_exit_status": _parse_launchctl_value(r.stdout, "LastExitStatus") if loaded else None,
        "program": _parse_launchctl_value(r.stdout, "Program") if loaded else None,
    }


def log_tail(root: Path, *, lines: int = 50) -> dict:
    log_path = root / "state" / "enrichment-worker.log"
    if not log_path.exists():
        return {"path": str(log_path), "exists": False, "lines": []}
    text_lines = log_path.read_text(errors="replace").splitlines()
    n = max(0, int(lines))
    return {"path": str(log_path), "exists": True, "lines": text_lines[-n:] if n else []}


def _resolve_personal_db_executable() -> str | None:
    current_env_script = Path(sys.executable).parent / "personal-db"
    if current_env_script.exists():
        return str(current_env_script)
    return shutil.which("personal-db")


def _parse_launchctl_value(status_text: str, key: str) -> str | int | None:
    prefix = f'"{key}" = '
    for line in status_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        value = stripped[len(prefix):].rstrip(";")
        if value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        try:
            return int(value)
        except ValueError:
            return value
    return None
