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


def _listed_commands(help_text: str) -> set[str]:
    """Pull command names out of a Typer/Click --help panel's Commands section."""
    names = set()
    in_commands = False
    for line in help_text.splitlines():
        if "Commands" in line and line.strip().startswith(("╭", "-", "─")):
            in_commands = True
            continue
        if not in_commands:
            continue
        content = line.strip().strip("│").strip()
        if not content or content.startswith("╰") or set(content) <= {"─"}:
            break
        first = content.split()[0] if content.split() else ""
        # Continuation lines of a wrapped help description have extra
        # indentation after the box char in the *original* line (Typer
        # left-aligns command names right after "│ "); only trust lines
        # where the third character is non-blank as a real entry.
        if line.startswith("│ ") and len(line) > 2 and line[2] != " ":
            names.add(first)
    return names


def test_top_level_help_shows_exactly_the_user_meaningful_set(tmp_path):
    result = _run(tmp_path, "--help")
    assert result.exit_code == 0
    shown = _listed_commands(result.output)
    assert shown == _TOP_LEVEL_VISIBLE
    for hidden in _TOP_LEVEL_HIDDEN:
        assert hidden not in shown


def test_dev_help_shows_the_plumbing(tmp_path):
    result = _run(tmp_path, "dev", "--help")
    assert result.exit_code == 0
    shown = _listed_commands(result.output)
    assert shown == _DEV_VISIBLE


def test_tracker_help_hides_new(tmp_path):
    result = _run(tmp_path, "tracker", "--help")
    assert result.exit_code == 0
    shown = _listed_commands(result.output)
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
