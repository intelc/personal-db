"""`personal-db status` — one screen: daemon, trackers, FDA, MCP targets.

Deliberately read-only and fast: every check here is a local file read, a
loopback HTTP GET, or (for the FDA/MCP probes) a cheap best-effort local
call. Nothing here installs, writes, or mutates anything — that's what
`setup`/`tracker install`/`mcp install`/`daemon install` are for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import typer

from personal_db.cli.permission_cmd import FDA_PROBES
from personal_db.cli.state import get_root
from personal_db.core.config import Config
from personal_db.core.daemon_token import read_token
from personal_db.core.permissions import probe_sqlite_access
from personal_db.services.daemon import client as dc

_MCP_CONFIG_PATHS = {
    "cursor": Path("~/.cursor/mcp.json").expanduser(),
    "claude_desktop": Path(
        "~/Library/Application Support/Claude/claude_desktop_config.json"
    ).expanduser(),
}


def _daemon_status() -> str:
    try:
        health = dc.health()
    except dc.DaemonUnreachable:
        return "not running -- run `personal-db daemon install` (or `personal-db dev daemon run` to try it in the foreground)"
    except dc.DaemonError as e:
        return f"running but erroring: {e}"
    uptime = health.get("uptime_seconds")
    return f"running (uptime {uptime}s)" if uptime is not None else "running"


def _token_status(cfg: Config) -> str:
    return "present" if read_token(cfg) else "not yet generated (created on first daemon start)"


def _tracker_summary(cfg: Config) -> list[str]:
    if not cfg.trackers_dir.exists():
        return ["no trackers installed"]
    names = sorted(d.name for d in cfg.trackers_dir.iterdir() if d.is_dir())
    if not names:
        return ["no trackers installed"]
    last_run_path = cfg.state_dir / "last_run.json"
    last_run: dict[str, str] = {}
    if last_run_path.is_file():
        try:
            last_run = json.loads(last_run_path.read_text())
        except (OSError, json.JSONDecodeError):
            last_run = {}
    lines = [f"{len(names)} installed"]
    for name in names:
        ts = last_run.get(name)
        if not ts:
            lines.append(f"{name:30s} never synced")
            continue
        try:
            age = datetime.now(UTC) - datetime.fromisoformat(ts).astimezone(UTC)
            hours = age.total_seconds() / 3600
            age_str = f"{hours:.1f}h ago" if hours < 48 else f"{hours / 24:.1f}d ago"
        except ValueError:
            age_str = f"unparseable timestamp: {ts}"
        lines.append(f"{name:30s} last synced {age_str}")
    return lines


def _fda_summary() -> list[str]:
    lines = []
    for tracker, path in FDA_PROBES.items():
        result = probe_sqlite_access(path)
        icon = "granted" if result.granted else "denied"
        lines.append(f"  {tracker:15s} {icon} ({result.reason})")
    return lines


def _mcp_claude_code_configured() -> bool:
    if not shutil.which("claude"):
        return False
    try:
        r = subprocess.run(
            ["claude", "mcp", "list"], capture_output=True, text=True, timeout=5
        )
    except (subprocess.SubprocessError, OSError):
        return False
    return "personal_db" in (r.stdout or "")


def _mcp_json_configured(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    return "personal_db" in data.get("mcpServers", {})


def _mcp_summary() -> list[str]:
    configured = {
        "claude_code": _mcp_claude_code_configured(),
        **{key: _mcp_json_configured(path) for key, path in _MCP_CONFIG_PATHS.items()},
    }
    any_configured = any(configured.values())
    lines = []
    for key, ok in configured.items():
        lines.append(f"  {key:15s} {'configured' if ok else 'not configured'}")
    if not any_configured:
        lines.append("  (run `personal-db mcp install` to add one)")
    return lines


def status() -> None:
    """Daemon, trackers, FDA, and MCP status at a glance."""
    cfg = Config(root=get_root())

    typer.echo("daemon")
    typer.echo(f"  {_daemon_status()}")
    typer.echo(f"  token: {_token_status(cfg)}")
    typer.echo()

    typer.echo("trackers")
    for line in _tracker_summary(cfg):
        typer.echo(f"  {line}")
    typer.echo()

    typer.echo("full disk access")
    for line in _fda_summary():
        typer.echo(line)
    typer.echo()

    typer.echo("mcp targets")
    for line in _mcp_summary():
        typer.echo(line)
