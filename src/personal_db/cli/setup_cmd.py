"""`personal-db setup` — one-stop bootstrap: init + choose Browser/Terminal/Skip wizard.

Called by install.sh after `uv tool install`. Idempotent: re-running re-prompts
for the wizard mode but skips already-done init steps.

After the terminal wizard exits, runs three finalize steps:
  1. Install the launchd scheduler (silent, macOS only)
  2. Offer to install the MCP server into an agent (Claude Code / Desktop / Cursor)
  3. Optionally launch the menu bar + dashboard

The browser wizard handles its own finalize via the /setup/finish web route.
"""

from __future__ import annotations

import os
import sys
import threading
import webbrowser

import questionary
import typer

from personal_db.cli.init_cmd import run as run_init
from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.wizard.menu import run_menu


def run(
    port: int = typer.Option(8765, "--port", help="Port for the web wizard"),
) -> None:
    """Initialize the data root, then pick a configuration mode."""
    run_init()

    cfg = Config(root=get_root())

    choice = questionary.select(
        "How do you want to configure trackers?",
        choices=[
            questionary.Choice("Browser  — visual wizard (recommended)", value="browser"),
            questionary.Choice("Terminal — questionary prompts here", value="terminal"),
            questionary.Choice("Skip     — set up later", value="skip"),
        ],
    ).ask()

    if choice is None or choice == "skip":
        typer.echo(
            "Skipping. Run `personal-db setup` again any time, "
            "or `personal-db tracker setup` to jump straight to the terminal wizard."
        )
        return

    if choice == "terminal":
        run_menu(cfg)
        _finalize_terminal(cfg)
        return

    _launch_browser_wizard(cfg, port)


def _launch_browser_wizard(cfg: Config, port: int) -> None:
    url = f"http://127.0.0.1:{port}/setup"
    typer.echo(f"Starting wizard at {url}")
    typer.echo(
        "When you click 'Finish setup' in the browser we'll wire up the scheduler "
        "and offer to install the MCP server into an agent."
    )
    typer.echo("Press Ctrl+C in this terminal when you're done to stop the server.")

    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    from personal_db.ui.server import build_app

    try:
        uvicorn.run(build_app(cfg), host="127.0.0.1", port=port, log_level="warning")
    except OSError as e:
        if e.errno in (48, 98) or "address already in use" in str(e).lower():
            typer.echo(
                f"\nPort {port} is already in use — the dashboard may already be running.\n"
                f"Open {url} in your browser to continue setup."
            )
            return
        raise


def _finalize_terminal(cfg: Config) -> None:
    """Post-wizard: scheduler, MCP, optional dashboard launch."""
    typer.echo("\n──────── finalize ────────")
    install_scheduler(cfg)

    install_mcp = questionary.confirm(
        "Install personal_db MCP server into an agent (Claude Code / Desktop / Cursor)?",
        default=True,
    ).ask()
    if install_mcp:
        from personal_db.wizard.mcp_setup import run_mcp_setup_menu

        run_mcp_setup_menu(cfg)

    open_ui = questionary.confirm(
        "Open the dashboard now? (you can run `personal-db ui` any time)",
        default=False,
    ).ask()
    if open_ui:
        # exec replaces this process so Ctrl+C goes to the menubar app, not us
        os.execvp("personal-db", ["personal-db", "ui"])

    typer.echo("\n──────── try it ────────")
    typer.echo("Open Claude (Code, Desktop, or any MCP-connected agent) and paste:")
    typer.echo("")
    typer.echo("    What can personal_db tell you about my last week?")
    typer.echo("")
    typer.echo("Backfills are running in the background — agent answers get richer as")
    typer.echo("historical data populates. Tail logs at <root>/state/backfill_*.log")
    typer.echo("")
    typer.echo("Other useful commands:")
    typer.echo("  personal-db ui                # menu bar + dashboard")
    typer.echo("  personal-db setup             # add or reconfigure trackers")
    typer.echo("  personal-db mcp install       # add MCP into another agent")
    typer.echo("  personal-db scheduler status  # check periodic sync")


def install_scheduler(cfg: Config) -> None:
    """Install the launchd job. macOS-only — prints a notice on other platforms.

    Honors PERSONAL_DB_NO_SCHEDULER=1 so tests, demo recordings, and users who
    don't want a background process can opt out cleanly. The launchd plist
    location is global (~/Library/LaunchAgents/...), so writing it from a test
    or demo would clobber the real install — the env var prevents that.
    """
    if os.environ.get("PERSONAL_DB_NO_SCHEDULER") == "1":
        typer.echo("✓ scheduler skipped (PERSONAL_DB_NO_SCHEDULER=1)")
        return
    if sys.platform != "darwin":
        typer.echo(
            f"⚠ scheduler is macOS-only (detected {sys.platform}); periodic sync skipped"
        )
        return
    try:
        from personal_db import scheduler

        plist = scheduler.install(cfg.root, 600)
        typer.echo(f"✓ scheduler installed → {plist} (sync every 10 min)")
    except Exception as e:  # noqa: BLE001
        typer.echo(f"⚠ scheduler install failed: {e}")
