from pathlib import Path

import pytest

from personal_db.core.manifest import Manifest, ManifestError, load_manifest

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_load_valid_manifest():
    m = load_manifest(FIXTURES / "manifest_valid.yaml")
    assert isinstance(m, Manifest)
    assert m.name == "github_commits"
    assert m.time_column == "committed_at"
    assert "github_commits" in m.schema.tables
    assert m.permission_type == "api_key"


def test_missing_time_column_rejected():
    with pytest.raises(ManifestError):
        load_manifest(FIXTURES / "manifest_missing_time_column.yaml")


def test_load_manifest_parses_env_var_step():
    """v0.1: setup_steps is now a list of typed steps, not prose strings."""
    m = load_manifest(FIXTURES / "manifest_valid.yaml")
    assert len(m.setup_steps) >= 1
    step = m.setup_steps[0]
    assert step.type == "env_var"
    assert step.name == "GITHUB_TOKEN"
    assert step.secret is True


def test_load_manifest_rejects_prose_setup_steps(tmp_path):
    """A v0-style prose setup_steps must fail validation under v0.1."""
    p = tmp_path / "bad.yaml"
    p.write_text(
        "name: x\n"
        "description: x\n"
        "permission_type: api_key\n"
        'setup_steps: ["just a string"]\n'
        "time_column: ts\n"
        "schema:\n"
        "  tables:\n"
        "    x: {columns: {ts: {type: TEXT, semantic: ts}}}\n"
    )
    with pytest.raises(ManifestError):
        load_manifest(p)


def test_load_manifest_rejects_unknown_step_type():
    """A typo'd step type must fail validation."""
    with pytest.raises(ManifestError):
        load_manifest(FIXTURES / "manifest_invalid_step_type.yaml")


def test_load_manifest_parses_optional_env_var(tmp_path):
    """env_var steps support an optional flag; default is False."""
    p = tmp_path / "m.yaml"
    p.write_text(
        "name: x\n"
        "description: x\n"
        "permission_type: api_key\n"
        "setup_steps:\n"
        "  - type: env_var\n"
        "    name: REQUIRED_KEY\n"
        "    prompt: required\n"
        "  - type: env_var\n"
        "    name: OPTIONAL_KEY\n"
        "    prompt: optional\n"
        "    optional: true\n"
        "time_column: ts\n"
        "schema:\n"
        "  tables:\n"
        "    x: {columns: {ts: {type: TEXT, semantic: ts}}}\n"
    )
    m = load_manifest(p)
    assert m.setup_steps[0].optional is False
    assert m.setup_steps[1].optional is True


def test_oauth_step_accepts_optional_adapter_field(tmp_path):
    from personal_db.core.manifest import load_manifest, OAuthStep

    p = tmp_path / "manifest.yaml"
    p.write_text(
        """\
name: t1
description: test
permission_type: oauth
time_column: ts
setup_steps:
  - type: oauth
    provider: withings_test
    adapter: oauth_adapter:WithingsAdapter
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schema:
  tables: {}
""",
    )
    m = load_manifest(p)
    step = m.setup_steps[0]
    assert isinstance(step, OAuthStep)
    assert step.adapter == "oauth_adapter:WithingsAdapter"


def test_manifest_python_deps_defaults_empty(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "name: x\n"
        "description: x\n"
        "permission_type: none\n"
        "time_column: ts\n"
        "schema:\n"
        "  tables:\n"
        "    x: {columns: {ts: {type: TEXT, semantic: ts}}}\n"
    )
    m = load_manifest(p)
    assert m.python_deps == []


def test_manifest_python_deps_parses_requirement_strings(tmp_path):
    p = tmp_path / "m.yaml"
    p.write_text(
        "name: x\n"
        "description: x\n"
        "permission_type: none\n"
        "time_column: ts\n"
        "python_deps:\n"
        "  - requests>=2.31\n"
        "  - some-niche-package==1.2.3\n"
        "schema:\n"
        "  tables:\n"
        "    x: {columns: {ts: {type: TEXT, semantic: ts}}}\n"
    )
    m = load_manifest(p)
    assert m.python_deps == ["requests>=2.31", "some-niche-package==1.2.3"]


def test_oauth_step_adapter_field_is_optional(tmp_path):
    from personal_db.core.manifest import load_manifest, OAuthStep

    p = tmp_path / "manifest.yaml"
    p.write_text(
        """\
name: t2
description: test
permission_type: oauth
time_column: ts
setup_steps:
  - type: oauth
    provider: whoop
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schema:
  tables: {}
""",
    )
    m = load_manifest(p)
    step = m.setup_steps[0]
    assert isinstance(step, OAuthStep)
    assert step.adapter is None


def test_humanize_tracker_name_simple_words():
    from personal_db.core.manifest import humanize_tracker_name

    assert humanize_tracker_name("screen_time") == "Screen Time"
    assert humanize_tracker_name("daily_time_accounting") == "Daily Time Accounting"
    assert humanize_tracker_name("code_agent_activity") == "Code Agent Activity"
    assert humanize_tracker_name("chrome_history") == "Chrome History"
    assert humanize_tracker_name("crypto_wallet") == "Crypto Wallet"
    assert humanize_tracker_name("life_context") == "Life Context"
    assert humanize_tracker_name("mosspath_lite") == "Mosspath Lite"


def test_humanize_tracker_name_casing_overrides():
    from personal_db.core.manifest import humanize_tracker_name

    assert humanize_tracker_name("github_commits") == "GitHub Commits"
    assert humanize_tracker_name("imessage") == "iMessage"
    assert humanize_tracker_name("xhs_saved") == "XHS Saved"
    assert humanize_tracker_name("omi") == "Omi"
    assert humanize_tracker_name("oura") == "Oura"
    assert humanize_tracker_name("whoop") == "Whoop"


def test_manifest_display_title_falls_back_to_humanized_name(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(
        "name: github_commits\n"
        "description: x\n"
        "permission_type: api_key\n"
        "time_column: ts\n"
        "schema:\n"
        "  tables: {}\n"
    )
    m = load_manifest(p)
    assert m.title is None
    assert m.display_title() == "GitHub Commits"


def test_manifest_display_title_prefers_explicit_title(tmp_path):
    p = tmp_path / "manifest.yaml"
    p.write_text(
        "name: github_commits\n"
        "title: My Commits\n"
        "description: x\n"
        "permission_type: api_key\n"
        "time_column: ts\n"
        "schema:\n"
        "  tables: {}\n"
    )
    m = load_manifest(p)
    assert m.title == "My Commits"
    assert m.display_title() == "My Commits"


def test_permission_label():
    from personal_db.core.manifest import permission_label

    assert permission_label("none") == "No permissions"
    assert permission_label("api_key") == "API key"
    assert permission_label("oauth") == "OAuth"
    assert permission_label("full_disk_access") == "Full Disk Access"
    assert permission_label("manual") == "Manual"
