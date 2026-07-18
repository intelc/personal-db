from pathlib import Path

import typer
from dotenv import load_dotenv

from personal_db.cli import (
    app_cmd,
    code_agent_hook_cmd,
    context_cmd,
    contract_cmd,
    daemon_cmd,
    enrich_cmd,
    init_cmd,
    log_cmd,
    mcp_cmd,
    permission_cmd,
    query_cmd,
    setup_cmd,
    source_cmd,
    status_cmd,
    sync_cmd,
    tracker_cmd,
    ui_cmd,
)
from personal_db.cli._deprecated import leaf_alias, legacy_group_note
from personal_db.cli.state import _state, get_root
from personal_db.core.config import Config
from personal_db.core.runtime_env import activate_lib_dir

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")
# Developer/plumbing commands live here so top-level --help stays focused on
# what a non-developer user needs (see cli/_deprecated.py for how the old
# top-level names keep working).
dev_app = typer.Typer(no_args_is_help=True, help="Developer/plumbing commands")


def _load_root_env(root: Path) -> None:
    """Load <root>/.env if present. override=False so shell env wins."""
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()
    _load_root_env(get_root())
    # Sealed signed bundle: a pack's declared python_deps can only ever be
    # installed under <root>/lib, never into the bundle's own site-packages.
    # Make that directory importable for every CLI invocation.
    activate_lib_dir(Config(root=get_root()))


# --- Top level: what a non-developer user needs -----------------------------

app.command("init")(init_cmd.run)
app.command("setup")(setup_cmd.run)
app.command("sync")(sync_cmd.sync)
app.command("backfill")(sync_cmd.backfill)
app.command("log")(log_cmd.log)
app.command("ui")(ui_cmd.ui)
app.command("status")(status_cmd.status)

tracker_app = typer.Typer(no_args_is_help=True, help="Tracker management")
tracker_app.command("list")(tracker_cmd.list_cmd)
tracker_app.command("install")(tracker_cmd.install)
tracker_app.command("reinstall")(tracker_cmd.reinstall)
tracker_app.command("setup")(tracker_cmd.setup)
tracker_app.command("validate")(tracker_cmd.validate)
tracker_app.command("deps")(tracker_cmd.deps)
# `tracker new` moved under `dev tracker new` (scaffolding a brand-new tracker
# is a developer action); kept here too, hidden, for compatibility.
tracker_app.command("new", hidden=True)(leaf_alias(tracker_cmd.new, "dev tracker new"))
app.add_typer(tracker_app, name="tracker")

app_app = typer.Typer(no_args_is_help=True, help="App management")
app_app.command("list")(app_cmd.list_cmd)
app_app.command("available")(app_cmd.available)
app_app.command("install")(app_cmd.install)
app_app.command("reinstall")(app_cmd.reinstall)
app_app.command("deps")(app_cmd.deps)
app.add_typer(app_app, name="app")

# `mcp` keeps `install` (and the bare-invocation "run the stdio server"
# behavior) at top level; `refresh` moved under `dev mcp refresh` (see below)
# but stays reachable here too, hidden.
mcp_cmd.mcp_app.command("refresh", hidden=True)(
    leaf_alias(mcp_cmd.refresh, "dev mcp refresh")
)
app.add_typer(mcp_cmd.mcp_app, name="mcp")

# `daemon` keeps install/uninstall/status/restart at top level but the whole
# group is hidden from --help -- `setup`/`status` are how most users touch
# this; `daemon run` (foreground, for debugging) moved under `dev daemon run`.
daemon_app = typer.Typer(no_args_is_help=True, help="Long-running sync daemon")
daemon_app.command("install")(daemon_cmd.install)
daemon_app.command("uninstall")(daemon_cmd.uninstall)
daemon_app.command("status")(daemon_cmd.status)
daemon_app.command("restart")(daemon_cmd.restart)
daemon_app.command("run", hidden=True)(leaf_alias(daemon_cmd.run, "dev daemon run"))
app.add_typer(daemon_app, name="daemon", hidden=True)

# `context`/`enrich`/`source` move wholesale under `dev`. The same Typer
# sub-app objects are mounted at both the old top-level name (hidden) and
# the new `dev` location; a shared group callback (legacy_group_note) prints
# the deprecation pointer only when reached via the old path.
context_cmd.app.callback()(legacy_group_note("dev context"))
enrich_cmd.app.callback()(legacy_group_note("dev enrich"))
source_cmd.app.callback()(legacy_group_note("dev source"))
app.add_typer(context_cmd.app, name="context", hidden=True)
app.add_typer(enrich_cmd.app, name="enrich", hidden=True)
app.add_typer(source_cmd.app, name="source", hidden=True)

# `permission` moves under `dev permission`; kept here too, hidden.
permission_app = typer.Typer(no_args_is_help=True, help="OS permission helpers")
permission_app.command("check", hidden=True)(
    leaf_alias(permission_cmd.check, "dev permission check")
)
app.add_typer(permission_app, name="permission", hidden=True)

# code-agent-hook-write is machine-invoked (Claude Code hooks: async, must
# never break or get noisy) -- mounted at both locations unchanged, with no
# deprecation note, so existing hook configs pointing at the old path keep
# working silently.
app.add_typer(code_agent_hook_cmd.app, name="code-agent-hook-write", hidden=True)


# --- `personal-db dev ...`: developer/plumbing commands ---------------------

dev_app.command("query")(query_cmd.query)
dev_app.command("contract")(contract_cmd.contract)
dev_app.add_typer(context_cmd.app, name="context")
dev_app.add_typer(enrich_cmd.app, name="enrich")
dev_app.add_typer(source_cmd.app, name="source")

dev_permission_app = typer.Typer(no_args_is_help=True, help="OS permission helpers")
dev_permission_app.command("check")(permission_cmd.check)
dev_app.add_typer(dev_permission_app, name="permission")

dev_app.add_typer(code_agent_hook_cmd.app, name="code-agent-hook-write")

dev_mcp_app = typer.Typer(no_args_is_help=True, help="MCP maintenance")
dev_mcp_app.command("refresh")(mcp_cmd.refresh)
dev_app.add_typer(dev_mcp_app, name="mcp")

dev_daemon_app = typer.Typer(no_args_is_help=True, help="Daemon (foreground)")
dev_daemon_app.command("run")(daemon_cmd.run)
dev_app.add_typer(dev_daemon_app, name="daemon")

dev_tracker_app = typer.Typer(no_args_is_help=True, help="Tracker scaffolding")
dev_tracker_app.command("new")(tracker_cmd.new)
dev_app.add_typer(dev_tracker_app, name="tracker")

app.add_typer(dev_app, name="dev")


if __name__ == "__main__":
    app()
