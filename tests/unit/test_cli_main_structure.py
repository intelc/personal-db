"""Phase 2d: CLI de-jargoning -- `personal-db dev`, hidden compat aliases,
and `personal-db --help` showing exactly the user-meaningful command set.

Every invocation passes an isolated `--root` (a tmp_path). The global
`@app.callback()` runs `_load_root_env`/`load_dotenv` on *every* invocation,
including bare `--help`, and `load_dotenv` mutates the real `os.environ` for
the rest of the test process (unlike `monkeypatch.setenv`); invoking without
`--root` would fall back to the real default root (`~/personal_db`) and load
whoever's real `.env` lives there into this test process, contaminating
later tests. Never invoke this CLI's Typer `app` without `--root` in a test.
"""

from __future__ import annotations

from pathlib import Path

from typer.main import get_command
from typer.testing import CliRunner

from personal_db.cli.main import app

runner = CliRunner()

_TOP_LEVEL_VISIBLE = {
    "init",
    "setup",
    "sync",
    "backfill",
    "log",
    "ui",
    "status",
    "browser",
    "tracker",
    "app",
    "mcp",
    "dev",
}

_TOP_LEVEL_HIDDEN = {
    "context",
    "enrich",
    "source",
    "permission",
    "daemon",
    "code-agent-hook-write",
}

_DEV_VISIBLE = {
    "query",
    "contract",
    "context",
    "enrich",
    "source",
    "permission",
    "code-agent-hook-write",
    "mcp",
    "daemon",
    "tracker",
}


def _run(root: Path, *args: str):
    return runner.invoke(app, ["--root", str(root), *args])


def _visible_command_names(group) -> set[str]:
    """Names of a Click/Typer group's direct subcommands that aren't hidden.

    This mirrors what `--help` renders in its Commands panel, but reads the
    command tree directly (`typer.main.get_command(app)` -> Click `Group` ->
    `.commands`) instead of parsing Rich-rendered `--help` text. Rendering
    differs across terminal widths/no-TTY environments (e.g. CI runners), but
    the underlying Click command tree -- and each command's `hidden` flag --
    does not.
    """
    return {name for name, cmd in group.commands.items() if not cmd.hidden}


def _hidden_command_names(group) -> set[str]:
    return {name for name, cmd in group.commands.items() if cmd.hidden}


def test_top_level_help_shows_exactly_the_user_meaningful_set():
    group = get_command(app)
    shown = _visible_command_names(group)
    assert shown == _TOP_LEVEL_VISIBLE
    assert _hidden_command_names(group) == _TOP_LEVEL_HIDDEN


def test_dev_help_shows_the_plumbing():
    group = get_command(app)
    dev_group = group.commands["dev"]
    assert _visible_command_names(dev_group) == _DEV_VISIBLE


def test_tracker_help_hides_new():
    group = get_command(app)
    tracker_group = group.commands["tracker"]
    shown = _visible_command_names(tracker_group)
    assert "new" not in shown
    assert {"list", "install", "reinstall", "setup", "validate"} <= shown


def test_tracker_new_hidden_alias_still_works_and_notes_new_location(tmp_path):
    root = tmp_path / "personal_db"
    result = _run(root, "tracker", "new", "widget")
    assert result.exit_code == 0, result.output
    assert (root / "trackers" / "widget" / "manifest.yaml").is_file()
    assert "moved to `personal-db dev tracker new`" in result.output


def test_dev_tracker_new_works_without_deprecation_note(tmp_path):
    root = tmp_path / "personal_db"
    result = _run(root, "dev", "tracker", "new", "widget")
    assert result.exit_code == 0, result.output
    assert (root / "trackers" / "widget" / "manifest.yaml").is_file()
    assert "moved to" not in result.output


def test_daemon_group_hidden_but_subcommands_still_work(monkeypatch, tmp_path):
    from personal_db.services.daemon import install as di

    monkeypatch.setattr(di, "install", lambda root: {"plist": root / "p.plist"})
    result = _run(tmp_path, "daemon", "install")
    assert result.exit_code == 0, result.output


def test_mcp_refresh_hidden_alias_notes_new_location(tmp_path):
    result = _run(tmp_path, "mcp", "refresh")
    assert result.exit_code == 0, result.output
    assert "moved to `personal-db dev mcp refresh`" in result.output


def test_dev_mcp_refresh_works_without_note(tmp_path):
    result = _run(tmp_path, "dev", "mcp", "refresh")
    assert result.exit_code == 0, result.output
    assert "moved to" not in result.output


def test_permission_group_hidden_but_check_still_works(tmp_path):
    # permission check reads a tracker's manifest for its FDA-probed path;
    # a nonexistent tracker exits non-zero, which is fine -- we're only
    # checking that the (hidden) route dispatches and prints the note.
    result = _run(tmp_path, "permission", "check", "nonexistent")
    assert "moved to `personal-db dev permission check`" in result.output


def test_context_group_note_only_on_legacy_path(tmp_path):
    legacy = _run(tmp_path, "context", "email", "--help")
    dev = _run(tmp_path, "dev", "context", "email", "--help")
    assert "moved to `personal-db dev context`" in legacy.output
    assert "moved to" not in dev.output
