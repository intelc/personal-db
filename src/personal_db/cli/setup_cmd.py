"""`personal-db setup` — one-stop bootstrap: init + choose Browser/Terminal/Skip wizard.

Called by install.sh after `uv tool install`. Idempotent: re-running re-prompts
for the wizard mode but skips already-done init steps.
"""

from __future__ import annotations

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
        return

    _launch_browser_wizard(cfg, port)


def _launch_browser_wizard(cfg: Config, port: int) -> None:
    url = f"http://127.0.0.1:{port}/setup"
    typer.echo(f"Starting wizard at {url}")
    typer.echo("Press Ctrl+C in this terminal when you're done to stop the server.")

    # Open the browser slightly after uvicorn binds, so the page loads on first try.
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    import uvicorn

    from personal_db.ui.server import build_app

    try:
        uvicorn.run(build_app(cfg), host="127.0.0.1", port=port, log_level="warning")
    except OSError as e:
        # errno 48 (EADDRINUSE on macOS) / 98 (Linux). The dashboard is probably
        # already running — point the user at it instead of crashing.
        if e.errno in (48, 98) or "address already in use" in str(e).lower():
            typer.echo(
                f"\nPort {port} is already in use — the dashboard may already be running.\n"
                f"Open {url} in your browser to continue setup."
            )
            return
        raise
