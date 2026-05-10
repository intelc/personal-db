from personal_db.cli.mcp_cmd import _MCP_CMDLINE_RE, _parse_ps_output

PS_SAMPLE = """\
15946 15944 /opt/homebrew/.../Python /Users/x/.venv/bin/personal-db mcp
21062 15944 /Users/x/.local/share/uv/tools/personal-db/bin/python /Users/x/.local/share/uv/tools/personal-db/bin/personal-db mcp
99999 15944 /Users/x/.venv/bin/personal-db mcp refresh
88888 15944 /Users/x/.venv/bin/personal-db mcp install claude_desktop
77777 15944 /Users/x/.venv/bin/personal-db sync omi
66666 15944 /usr/bin/ssh -L 8765:localhost:8765 host
"""


def test_parse_ps_extracts_pid_ppid_command():
    rows = _parse_ps_output(PS_SAMPLE)
    assert len(rows) == 6
    pids = {r[0] for r in rows}
    assert {15946, 21062, 99999, 88888, 77777, 66666} == pids
    # pid -> ppid mapping intact
    by_pid = {r[0]: r[1] for r in rows}
    assert by_pid[15946] == 15944


def test_parse_ps_skips_blank_and_malformed_lines():
    text = "\n   \n  not a row\n  501 1 ok command here\n"
    rows = _parse_ps_output(text)
    assert rows == [(501, 1, "ok command here")]


def test_mcp_regex_matches_only_bare_mcp_invocations():
    rows = _parse_ps_output(PS_SAMPLE)
    matches = [r for r in rows if _MCP_CMDLINE_RE.search(r[2])]
    matched_pids = {r[0] for r in matches}
    # Both bare `personal-db mcp` invocations match; `mcp refresh`,
    # `mcp install ...`, `sync omi`, and ssh do NOT.
    assert matched_pids == {15946, 21062}


def test_mcp_regex_tolerates_trailing_whitespace():
    assert _MCP_CMDLINE_RE.search("/path/personal-db mcp ")
    assert _MCP_CMDLINE_RE.search("/path/personal-db mcp\t")


def test_mcp_regex_rejects_subcommand_invocations():
    assert not _MCP_CMDLINE_RE.search("/path/personal-db mcp refresh")
    assert not _MCP_CMDLINE_RE.search("/path/personal-db mcp install claude_code")
