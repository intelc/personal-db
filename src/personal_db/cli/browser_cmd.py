"""Setup commands for optional Personal DB browser integrations."""

from __future__ import annotations

from pathlib import Path

import typer

from personal_db.browser_extension.bridge.install import install_native_host
from personal_db.cli.state import get_root


def install() -> None:
    """Install Chrome's Personal DB XHS native-messaging bridge."""
    try:
        result = install_native_host(Path(get_root()))
    except RuntimeError as exc:
        typer.echo(f"browser extension install failed: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("✓ installed Personal DB XHS collector bridge")
    typer.echo(f"  loadable extension dir: {result['extension_dir']}")
    typer.echo(f"  extension id:  {result['extension_id']}")
    typer.echo(f"  socket:        {result['socket']}")
    typer.echo(f"  host manifest: {result['host_manifest']}")
    typer.echo("Load the extension directory at chrome://extensions (Developer mode).")
