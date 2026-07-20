"""MCP setup sub-menu — install personal-db MCP into Claude Code / Cursor / Claude Desktop."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import questionary

from personal_db.core import runtime_env

_BACK = "__BACK__"
_AUTO = "__AUTO__"
_MANUAL = "__MANUAL__"

# The stable, on-PATH symlink the Tauri shell's "Install Command Line
# Tool..." tray item creates (see shell/src-tauri/src/cli_install.rs),
# pointing at whatever bundle is currently installed. Module-level so tests
# can monkeypatch it to a scratch path instead of touching the real
# /usr/local/bin.
_CLI_LINK_PATH = Path("/usr/local/bin/personal-db")


def _resolve_running_cli_path() -> str | None:
    """Absolute path to the personal-db entry point invoking THIS process,
    if argv[0] is one. Mirrors
    services/daemon/install.py::_resolve_cli_binary's argv[0] preference:
    the process running `<venv>/bin/personal-db mcp install ...` (or the
    frozen bundle's `Contents/Resources/cli/personal-db` wrapper invoked
    directly, or the /usr/local/bin symlink pointing at it) should write
    *that* binary's path into the host config, not some unrelated copy
    that happens to shadow it on PATH. Returns None when argv[0] isn't a
    personal-db entry point at all (e.g. this is invoked as a library call
    from a plain `python`/pytest process).
    """
    argv0 = Path(sys.argv[0]).resolve()
    if argv0.name == "personal-db" and argv0.is_file():
        return str(argv0)
    return None


def _personal_db_path() -> str:
    """Absolute path to the personal-db CLI shim. Required for MCP target configs:
    they spawn child processes without inheriting PATH.

    Resolution order:
      1. The /usr/local/bin/personal-db symlink (`_CLI_LINK_PATH`), IF it
         resolves to the exact binary currently running this process --
         i.e. it's the Tauri shell's tray-installed symlink pointing at
         this exact bundle (see shell/src-tauri/src/cli_install.rs and
         mcp_connect.rs, which invoke the CLI via that same preference so
         the two sides agree). This is the most *stable* choice: it
         survives the app bundle being resigned, rebuilt, or moved, so a
         host's MCP config written against it keeps working across app
         updates.
      2. argv[0] resolved, when it's itself a personal-db entry point (a
         venv's `bin/personal-db` shim, or the bundle's
         `Contents/Resources/cli/personal-db` wrapper invoked directly
         without the symlink) -- mirrors
         services/daemon/install.py::_resolve_cli_binary's argv[0]
         preference.
      3. Inside the frozen app bundle's daemon sidecar, argv[0] is rewritten
         by runpy to the sidecar's own `__main__.py` (it's spawned as
         `personal-db-daemon -m personal_db dev daemon run`), so step 2 never
         matches there. When `runtime_env.is_app_bundle()` is True, prefer
         `_CLI_LINK_PATH` if it exists and resolves into *some* `.app`
         bundle's CLI wrapper (best-effort: it may point at a different
         install than the one currently running, but it's still a real,
         on-PATH CLI -- far better than nothing); else fall back to this
         exact bundle's own `runtime_env.app_bundle_cli()`. Mirrors
         `shell/src-tauri/src/mcp_connect.rs::invoke_path`'s preference
         order, so the web Finish page and the Tauri tray's "Connect AI
         Apps" flow agree on which binary ends up written into a host's MCP
         config.
      4. `shutil.which("personal-db")` as a last resort, for the classic
         case of calling this outside a `personal-db ...` invocation
         entirely (argv[0] is `python`/`pytest`/etc.).
    """
    running = _resolve_running_cli_path()
    if running is not None:
        try:
            if _CLI_LINK_PATH.is_symlink() and os.path.realpath(_CLI_LINK_PATH) == os.path.realpath(
                running
            ):
                return str(_CLI_LINK_PATH)
        except OSError:
            pass
        return running

    if runtime_env.is_app_bundle():
        try:
            if _CLI_LINK_PATH.exists() and runtime_env.resolve_app_bundle_root(
                _CLI_LINK_PATH.resolve()
            ):
                return str(_CLI_LINK_PATH)
        except OSError:
            pass
        bundle_cli = runtime_env.app_bundle_cli()
        if bundle_cli is not None:
            return str(bundle_cli)

    p = shutil.which("personal-db")
    if not p:
        raise RuntimeError(
            "personal-db not found on PATH; activate the venv or install personal_db"
        )
    return str(Path(p).resolve())


# --- Per-target install functions ---


def _install_claude_code() -> tuple[bool, str]:
    """`claude mcp add -s user personal_db -- <abs-path> mcp`.

    User scope = available in every project, not just the cwd Claude was launched
    from. Without -s the default is `local` (project-specific to current dir),
    which is surprising when installing from a UI server."""
    if not shutil.which("claude"):
        return False, "claude CLI not found on PATH"
    pdb = _personal_db_path()
    # Remove from every scope so a stale entry at lower-precedence scope can't
    # shadow the user-scope one we're about to write. Failures are expected and
    # ignored (entry doesn't exist at that scope).
    for scope in ("local", "project", "user"):
        subprocess.run(
            ["claude", "mcp", "remove", "personal_db", "-s", scope],
            capture_output=True,
        )
    r = subprocess.run(
        ["claude", "mcp", "add", "-s", "user", "personal_db", "--", pdb, "mcp"],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0:
        return False, f"claude mcp add failed: {r.stderr.strip() or r.stdout.strip()}"
    return True, f"registered with Claude Code at user scope (command: {pdb} mcp)"


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
        f"    claude mcp add -s user personal_db -- {pdb} mcp\n\n"
        f"(The absolute path is required — Claude Code spawns MCP servers with a\n"
        f"minimal env that doesn't inherit your shell PATH. The -s user flag\n"
        f"makes it available in every project, not just the current dir.)"
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
