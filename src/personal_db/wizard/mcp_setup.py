"""MCP setup sub-menu — install personal-db MCP into Claude Code / Cursor / Claude Desktop."""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import questionary

_BACK = "__BACK__"
_AUTO = "__AUTO__"
_MANUAL = "__MANUAL__"


def _personal_db_path() -> str:
    """Absolute path to the personal-db CLI shim. Required for MCP target configs:
    they spawn child processes without inheriting PATH."""
    p = shutil.which("personal-db")
    if not p:
        raise RuntimeError(
            "personal-db not found on PATH; activate the venv or install personal_db"
        )
    return str(Path(p).resolve())


# --- Per-target install functions ---


def _install_claude_code() -> tuple[bool, str]:
    """`claude mcp add personal_db -- <abs-path> mcp`."""
    if not shutil.which("claude"):
        return False, "claude CLI not found on PATH"
    pdb = _personal_db_path()
    # Remove existing if present (idempotent)
    subprocess.run(
        ["claude", "mcp", "remove", "personal_db", "-s", "local"],
        capture_output=True,
    )
    r = subprocess.run(
        ["claude", "mcp", "add", "personal_db", "--", pdb, "mcp"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False, f"claude mcp add failed: {r.stderr.strip() or r.stdout.strip()}"
    return True, f"registered with Claude Code (command: {pdb} mcp)"


def _upsert_json_mcp_server(path: Path, command: str, args: list[str]) -> tuple[bool, str]:
    """Add (or replace) the 'personal_db' server inside `path`'s mcpServers block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            return False, f"existing config is not valid JSON: {e}"
    servers = data.setdefault("mcpServers", {})
    servers["personal_db"] = {"command": command, "args": args}
    path.write_text(json.dumps(data, indent=2))
    return True, f"wrote {path}"


def _install_cursor() -> tuple[bool, str]:
    pdb = _personal_db_path()
    return _upsert_json_mcp_server(Path("~/.cursor/mcp.json").expanduser(), pdb, ["mcp"])


def _install_claude_desktop() -> tuple[bool, str]:
    pdb = _personal_db_path()
    return _upsert_json_mcp_server(
        Path("~/Library/Application Support/Claude/claude_desktop_config.json").expanduser(),
        pdb,
        ["mcp"],
    )


# --- Manual instruction strings ---


def _manual_claude_code() -> str:
    pdb = _personal_db_path()
    return (
        f"Run this command:\n\n"
        f"    claude mcp add personal_db -- {pdb} mcp\n\n"
        f"(The absolute path is required — Claude Code spawns MCP servers with a\n"
        f"minimal env that doesn't inherit your shell PATH.)"
    )


def _manual_cursor() -> str:
    pdb = _personal_db_path()
    return (
        f"Edit ~/.cursor/mcp.json (create if missing) and merge this into the\n"
        f'top-level "mcpServers" object:\n\n'
        f'    "personal_db": {{\n'
        f'      "command": "{pdb}",\n'
        f'      "args": ["mcp"]\n'
        f"    }}\n\n"
        f"Then reload the Cursor window (Cmd+Shift+P → 'Reload Window')."
    )


def _manual_claude_desktop() -> str:
    pdb = _personal_db_path()
    return (
        f"Edit ~/Library/Application Support/Claude/claude_desktop_config.json\n"
        f'(create if missing) and merge this into "mcpServers":\n\n'
        f'    "personal_db": {{\n'
        f'      "command": "{pdb}",\n'
        f'      "args": ["mcp"]\n'
        f"    }}\n\n"
        f"Then quit and reopen Claude Desktop."
    )


# --- Target dispatch ---


@dataclass
class MCPTarget:
    label: str
    manual: Callable[[], str]
    auto: Callable[[], tuple[bool, str]]


_TARGETS = {
    "claude_code": MCPTarget("Claude Code (CLI)", _manual_claude_code, _install_claude_code),
    "cursor": MCPTarget("Cursor (editor)", _manual_cursor, _install_cursor),
    "claude_desktop": MCPTarget(
        "Claude Desktop (Mac app)", _manual_claude_desktop, _install_claude_desktop
    ),
}


def _per_target_menu(target: MCPTarget) -> None:
    print(f"\n  ── {target.label} ──")
    print()
    print("  Manual setup:")
    for line in target.manual().split("\n"):
        print(f"    {line}")
    print()
    selection = questionary.select(
        f"{target.label}: how would you like to proceed?",
        choices=[
            questionary.Choice(title="🤖 Do it for me (auto-install)", value=_AUTO),
            questionary.Choice(title="✋ Manual — I'll do it myself", value=_MANUAL),
            questionary.Choice(title="← Back", value=_BACK),
        ],
    ).ask()
    if selection == _AUTO:
        ok, detail = target.auto()
        icon = "✓" if ok else "✗"
        print(f"\n  {icon} {detail}\n")


def run_mcp_setup_menu(cfg) -> None:
    """Loop: pick a target, configure it, return."""
    while True:
        choices = [questionary.Choice(title=t.label, value=key) for key, t in _TARGETS.items()]
        choices.append(questionary.Choice(title="← Back to main menu", value=_BACK))
        selection = questionary.select("MCP setup — pick a target:", choices=choices).ask()
        if selection is None or selection == _BACK:
            return
        _per_target_menu(_TARGETS[selection])
