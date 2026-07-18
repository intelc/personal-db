from __future__ import annotations

from pathlib import Path

import typer

from personal_db.cli.state import get_root
from personal_db.core.config import Config
from personal_db.core.contract import generate_contract


def contract(
    output: Path = typer.Option(
        None, "--output", "-o", help="Write to this path instead of stdout"
    ),
    bundled: bool = typer.Option(
        False,
        "--bundled",
        help=(
            "Describe every bundled tracker/app/source template, ignoring this "
            "root's installed state. This is the mode docs/data-contract.md is "
            "committed from."
        ),
    ),
) -> None:
    """Generate the data contract Markdown doc for third-party consumers.

    Describes every installed (or, with --bundled, every bundled) tracker's
    tables, columns, and semantics, plus the core tables and the read/write
    consumer rules. See docs/data-contract.md for the bundled version.
    """
    cfg = Config(root=get_root())
    text = generate_contract(cfg, bundled=bundled)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text)
    else:
        typer.echo(text, nl=False)
