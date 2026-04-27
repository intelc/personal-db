"""Tests for the read/write/validate tracker MCP tools, including path safety."""

import pytest

from personal_db.config import Config
from personal_db.mcp_server.tools import (
    _resolve_tracker_path,
    read_tracker_file,
    validate_tracker,
    write_tracker_file,
)

VALID_MANIFEST = """
name: demo
description: a demo
permission_type: none
setup_steps: []
schedule:
  every: 1h
time_column: ts
granularity: event
schema:
  tables:
    demo:
      columns:
        ts: {type: TEXT, semantic: ts}
"""

VALID_SCHEMA = "CREATE TABLE IF NOT EXISTS demo (ts TEXT);\n"
VALID_INGEST = "def sync(t):\n    pass\n\ndef backfill(t, start, end):\n    pass\n"


def _cfg(tmp_path):
    root = tmp_path / "personal_db"
    (root / "trackers").mkdir(parents=True)
    return Config(root=root)


def test_write_then_read_roundtrip(tmp_path):
    cfg = _cfg(tmp_path)
    res = write_tracker_file(cfg, "demo/manifest.yaml", VALID_MANIFEST)
    assert res["created"] is True
    assert res["bytes_written"] == len(VALID_MANIFEST.encode())
    # File on disk
    assert (cfg.trackers_dir / "demo" / "manifest.yaml").read_text() == VALID_MANIFEST
    # Read back
    out = read_tracker_file(cfg, "demo/manifest.yaml")
    assert out["content"] == VALID_MANIFEST
    # Overwrite
    res2 = write_tracker_file(cfg, "demo/manifest.yaml", "name: changed\n")
    assert res2["created"] is False


def test_path_traversal_via_dotdot_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="escapes trackers dir"):
        write_tracker_file(cfg, "../escaped.txt", "x")
    with pytest.raises(ValueError, match="escapes trackers dir"):
        read_tracker_file(cfg, "../../etc/passwd")


def test_absolute_path_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="must be relative"):
        write_tracker_file(cfg, "/etc/passwd", "x")


def test_symlink_escape_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    # Create a symlink inside trackers/ pointing outside
    outside = tmp_path / "outside.txt"
    outside.write_text("hi")
    link = cfg.trackers_dir / "demo" / "linked.txt"
    link.parent.mkdir()
    link.symlink_to(outside)
    # The symlink itself is inside trackers/, but resolves outside — must be rejected
    with pytest.raises(ValueError, match="escapes trackers dir"):
        read_tracker_file(cfg, "demo/linked.txt")


def test_empty_or_dot_path_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    for bad in ["", ".", "/"]:
        with pytest.raises(ValueError, match="path is required"):
            _resolve_tracker_path(cfg, bad)


def test_oversized_write_rejected(tmp_path):
    cfg = _cfg(tmp_path)
    huge = "x" * (2 * 1024 * 1024)
    with pytest.raises(ValueError, match="too large"):
        write_tracker_file(cfg, "demo/big.txt", huge)


def test_validate_tracker_ok(tmp_path):
    cfg = _cfg(tmp_path)
    write_tracker_file(cfg, "demo/manifest.yaml", VALID_MANIFEST)
    write_tracker_file(cfg, "demo/schema.sql", VALID_SCHEMA)
    write_tracker_file(cfg, "demo/ingest.py", VALID_INGEST)
    res = validate_tracker(cfg, "demo")
    assert res["ok"] is True, res
    names = {c["name"] for c in res["checks"]}
    assert names == {"manifest_yaml", "manifest_schema", "ingest_py", "schema_sql"}
    assert all(c["ok"] for c in res["checks"])


def test_validate_tracker_reports_each_failure(tmp_path):
    cfg = _cfg(tmp_path)
    # Bad YAML (unquoted JSON-looking value)
    bad_manifest = VALID_MANIFEST + '          extra: like {"a": 1}\n'
    write_tracker_file(cfg, "demo/manifest.yaml", bad_manifest)
    # Bad ingest (syntax error)
    write_tracker_file(cfg, "demo/ingest.py", "def sync(t)\n    pass\n")
    # Bad schema sql
    write_tracker_file(cfg, "demo/schema.sql", "CREAT TABLE oops (a;\n")
    res = validate_tracker(cfg, "demo")
    assert res["ok"] is False
    by_name = {c["name"]: c for c in res["checks"]}
    assert by_name["manifest_yaml"]["ok"] is False
    assert by_name["ingest_py"]["ok"] is False
    assert by_name["schema_sql"]["ok"] is False


def test_validate_tracker_rejects_invalid_name(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(ValueError, match="invalid tracker name"):
        validate_tracker(cfg, "../escape")
    with pytest.raises(ValueError, match="invalid tracker name"):
        validate_tracker(cfg, "Bad-Name")


def test_read_missing_file_raises(tmp_path):
    cfg = _cfg(tmp_path)
    with pytest.raises(FileNotFoundError):
        read_tracker_file(cfg, "demo/missing.yaml")
