from pathlib import Path

import pytest

from personal_db.manifest import Manifest, ManifestError, load_manifest

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
