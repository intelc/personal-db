from pathlib import Path

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.manifest import load_manifest
from personal_db.permissions import open_fda_settings_pane, probe_sqlite_access

# Trackers we know need to read a specific gated SQLite file.
# Connectors declare this via manifest.permission_type + a probe path in their setup_steps;
# for v0 we hard-code the two known FDA-gated ones.
FDA_PROBES = {
    "screen_time": Path("~/Library/Application Support/Knowledge/knowledgeC.db").expanduser(),
    "imessage": Path("~/Library/Messages/chat.db").expanduser(),
}


def check(tracker: str = typer.Argument(...)) -> None:
    """Probe whether the OS permission required by this tracker is granted."""
    cfg = Config(root=get_root())
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    if manifest.permission_type != "full_disk_access":
        typer.echo(f"{tracker}: permission_type={manifest.permission_type} (no probe needed)")
        return
    probe_path = FDA_PROBES.get(tracker)
    if probe_path is None:
        typer.echo(f"no FDA probe path registered for {tracker}", err=True)
        raise typer.Exit(2)
    r = probe_sqlite_access(probe_path)
    typer.echo(f"{tracker}: granted={r.granted} -- {r.reason}")
    if not r.granted:
        typer.echo("Opening System Settings -> Privacy & Security -> Full Disk Access...")
        typer.echo("Grant access to your terminal binary (Terminal.app/iTerm2/Cursor),")
        typer.echo("then restart the terminal and re-run.")
        open_fda_settings_pane()
        raise typer.Exit(1)
