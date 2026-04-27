"""MCP prompts: server-side prompt templates surfaced to clients via slash-menu.

A "prompt" in MCP terms is a parameterized message the user invokes (e.g. via
`/personal_db:create_tracker` in Claude Code). The server returns a primed
message with current personal_db context substituted in, so Claude has
everything it needs to drive a methodology design conversation.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path

import yaml

from personal_db.config import Config

CREATE_TRACKER = "create_tracker"


def _read_template(name: str) -> str:
    """Load a prompt markdown template from this package."""
    return resources.files(__package__).joinpath(f"{name}.md").read_text()


def _user_tables(db_path: Path) -> list[dict]:
    """List user tables (excluding sqlite/personal_db internals) with columns + row counts."""
    if not db_path.exists():
        return []
    con = sqlite3.connect(db_path)
    try:
        names = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            )
        ]
        out: list[dict] = []
        for name in names:
            cols = [
                {"name": r[1], "type": r[2]}
                for r in con.execute(f'PRAGMA table_info("{name}")').fetchall()
            ]
            try:
                count = con.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
            except sqlite3.Error:
                count = None
            out.append({"name": name, "columns": cols, "row_count": count})
        return out
    finally:
        con.close()


def _installed_trackers(trackers_dir: Path) -> list[dict]:
    """Read manifest.yaml from each installed tracker for description + granularity."""
    if not trackers_dir.exists():
        return []
    out: list[dict] = []
    for d in sorted(trackers_dir.iterdir()):
        if not d.is_dir():
            continue
        manifest = d / "manifest.yaml"
        if not manifest.is_file():
            continue
        try:
            data = yaml.safe_load(manifest.read_text()) or {}
        except yaml.YAMLError:
            continue
        out.append(
            {
                "name": data.get("name", d.name),
                "description": data.get("description", ""),
                "granularity": data.get("granularity", ""),
            }
        )
    return out


def _format_tables(tables: list[dict]) -> str:
    if not tables:
        return "_(none — the database is empty; the user needs to install at least one source connector first)_"
    lines: list[str] = []
    for t in tables:
        cols = ", ".join(f"{c['name']} {c['type']}" for c in t["columns"])
        rc = f"{t['row_count']:,} rows" if t["row_count"] is not None else "row count unavailable"
        lines.append(f"- **`{t['name']}`** ({rc}): {cols}")
    return "\n".join(lines)


def _format_trackers(trackers: list[dict]) -> str:
    if not trackers:
        return "_(none installed yet)_"
    return "\n".join(
        f"- **`{t['name']}`** ({t['granularity']}) — {t['description']}" for t in trackers
    )


def build_create_tracker_prompt(cfg: Config) -> str:
    """Render the create_tracker prompt with the user's current personal_db state."""
    template = _read_template(CREATE_TRACKER)
    tables = _user_tables(cfg.db_path)
    trackers = _installed_trackers(cfg.trackers_dir)
    return (
        template.replace("{{root_path}}", str(cfg.root))
        .replace("{{trackers_dir}}", str(cfg.trackers_dir))
        .replace("{{db_path}}", str(cfg.db_path))
        .replace("{{tables_summary}}", _format_tables(tables))
        .replace("{{installed_trackers}}", _format_trackers(trackers))
    )
