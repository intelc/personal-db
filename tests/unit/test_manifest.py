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
