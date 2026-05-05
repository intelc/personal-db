import json
import sys

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.mcp_server.tools import query as run_query


def query(
    sql: str = typer.Argument(..., help="SELECT or WITH statement"),
    param: list[str] = typer.Option(
        None, "--param", "-p", help="Bind parameter for ? placeholders (repeatable)"
    ),
) -> None:
    """Run a read-only SQL query against the personal_db SQLite file.

    Same validation as the MCP `query` tool: SELECT/WITH only, single statement.
    Results are emitted as a JSON array on stdout.
    """
    cfg = Config(root=get_root())
    try:
        rows = run_query(cfg, sql, params=list(param) if param else None)
    except ValueError as e:
        typer.echo(f"query rejected: {e}", err=True)
        raise typer.Exit(2) from e
    json.dump(rows, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
