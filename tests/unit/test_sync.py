import json
import sys
from pathlib import Path

import pytest
import yaml

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.core.manifest import PlatformUnsupportedError
from personal_db.core.sync import _is_due, sync_due, sync_one
from tests._validation_helpers import mark_valid


def _make_tracker_dir(cfg: Config, name: str, schedule_every: str = "1h"):
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": schedule_every},
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {
                        name: {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    (d / "schema.sql").write_text(
        f"CREATE TABLE IF NOT EXISTS {name} (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t, start, end):\n"
        "    t.upsert(t.name, [{'id': 'b1', 'ts': '2026-04-01'}], key=['id'])\n"
        "def sync(t):\n"
        "    t.upsert(t.name, [{'id': 's1', 'ts': '2026-04-25'}], key=['id'])\n"
    )
    mark_valid(cfg, name)
    return d


def test_sync_one_runs_ingest_and_records_last_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo")
    sync_one(cfg, "demo")
    last_run = json.loads((tmp_root / "state" / "last_run.json").read_text())
    assert "demo" in last_run


def test_is_due_when_never_run(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo", schedule_every="1h")
    assert _is_due(cfg, "demo") is True


def test_sync_due_skips_recent(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo", schedule_every="1h")
    sync_one(cfg, "demo")
    # Immediately due-check should be false
    assert _is_due(cfg, "demo") is False


def test_sync_due_uses_provided_sync_one_fn(tmp_root):
    """sync_due should invoke the optional sync_one_fn callable instead of
    the built-in sync_one when one is supplied."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo", schedule_every="1h")

    called_with: list[tuple] = []

    def custom_sync_one(c, name):
        called_with.append((c, name))
        # Delegate to the real implementation so last_run is recorded.
        sync_one(c, name)

    results = sync_due(cfg, sync_one_fn=custom_sync_one)
    assert results.get("demo") == "ok"
    assert len(called_with) == 1
    assert called_with[0] == (cfg, "demo")


def test_sync_one_registers_oauth_adapter_from_manifest(tmp_root, monkeypatch):
    """Ingest.py's sync() does not need to register the adapter itself —
    sync_one wires it up based on the manifest's OAuthStep.adapter field."""
    from personal_db.core.config import Config
    from personal_db.core.oauth import StandardAdapter, _adapter_for, _adapters
    from personal_db.core.sync import sync_one

    cfg = Config(root=tmp_root)
    tracker_dir = cfg.trackers_dir / "fake_oauth_tracker"
    tracker_dir.mkdir(parents=True)

    (tracker_dir / "manifest.yaml").write_text(
        """\
name: fake_oauth_tracker
description: fake oauth tracker
permission_type: oauth
setup_steps:
  - type: oauth
    provider: fake_oauth_provider
    adapter: my_adapter:MyAdapter
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schedule:
  every: 6h
time_column: ts
granularity: event
schema:
  tables:
    fake_table:
      columns:
        id: {type: TEXT, semantic: pk}
""",
    )
    (tracker_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS fake_table (id TEXT PRIMARY KEY);\n"
    )
    (tracker_dir / "my_adapter.py").write_text(
        """\
class MyAdapter:
    def exchange_code(self, **kw): return {}
    def refresh_token(self, **kw): return {}
"""
    )
    (tracker_dir / "ingest.py").write_text(
        """\
def sync(t):
    return None
def backfill(t, start, end):
    return None
"""
    )

    # Sanity: not yet registered.
    assert isinstance(_adapter_for("fake_oauth_provider"), StandardAdapter)

    mark_valid(cfg, "fake_oauth_tracker")
    try:
        sync_one(cfg, "fake_oauth_tracker")
        assert _adapter_for("fake_oauth_provider").__class__.__name__ == "MyAdapter"
    finally:
        _adapters.pop("fake_oauth_provider", None)


def test_backfill_one_registers_oauth_adapter_from_manifest(tmp_root, monkeypatch):
    """backfill_one shares the same _register_oauth_adapters call site as sync_one;
    cover it explicitly so future regressions in either are caught."""
    from personal_db.core.config import Config
    from personal_db.core.oauth import StandardAdapter, _adapter_for, _adapters
    from personal_db.core.sync import backfill_one

    cfg = Config(root=tmp_root)
    tracker_dir = cfg.trackers_dir / "fake_oauth_tracker_b"
    tracker_dir.mkdir(parents=True)

    (tracker_dir / "manifest.yaml").write_text(
        """\
name: fake_oauth_tracker_b
description: fake oauth tracker (backfill variant)
permission_type: oauth
setup_steps:
  - type: oauth
    provider: fake_backfill_oauth_provider
    adapter: my_backfill_adapter:MyBackfillAdapter
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schedule:
  every: 6h
time_column: ts
granularity: event
schema:
  tables:
    fake_table:
      columns:
        id: {type: TEXT, semantic: pk}
""",
    )
    (tracker_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS fake_table (id TEXT PRIMARY KEY);\n"
    )
    (tracker_dir / "my_backfill_adapter.py").write_text(
        """\
class MyBackfillAdapter:
    def exchange_code(self, **kw): return {}
    def refresh_token(self, **kw): return {}
"""
    )
    (tracker_dir / "ingest.py").write_text(
        """\
def sync(t):
    return None
def backfill(t, start, end):
    return None
"""
    )

    assert isinstance(_adapter_for("fake_backfill_oauth_provider"), StandardAdapter)

    mark_valid(cfg, "fake_oauth_tracker_b")
    try:
        backfill_one(cfg, "fake_oauth_tracker_b", start=None, end=None)
        assert (
            _adapter_for("fake_backfill_oauth_provider").__class__.__name__
            == "MyBackfillAdapter"
        )
    finally:
        _adapters.pop("fake_backfill_oauth_provider", None)


def _make_platform_gated_tracker_dir(cfg: Config, name: str, platform: list[str]) -> Path:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "full_disk_access",
                "platform": platform,
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (ts TEXT);")
    (d / "ingest.py").write_text("def sync(t):\n    pass\ndef backfill(t, start, end):\n    pass\n")
    mark_valid(cfg, name)
    return d


def test_sync_one_refuses_on_unsupported_platform(tmp_root, monkeypatch):
    """A tracker declaring `platform: [darwin]` must refuse to sync (with a
    message naming the tracker and the required OS) when personal-db is
    running somewhere else -- simulated here via sys.platform, since the
    check reads that at call time."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_platform_gated_tracker_dir(cfg, "imessage_like", platform=["darwin"])
    monkeypatch.setattr(sys, "platform", "linux")

    with pytest.raises(PlatformUnsupportedError, match="imessage_like requires macOS"):
        sync_one(cfg, "imessage_like")


def test_sync_one_runs_on_supported_platform(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_platform_gated_tracker_dir(cfg, "imessage_like", platform=["darwin"])
    monkeypatch.setattr(sys, "platform", "darwin")

    sync_one(cfg, "imessage_like")  # must not raise

    last_run = json.loads((tmp_root / "state" / "last_run.json").read_text())
    assert "imessage_like" in last_run


def _make_tracker_with_broken_import(cfg: Config, name: str, *, python_deps: list[str]) -> Path:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": "none",
                "time_column": "ts",
                "python_deps": python_deps,
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (ts TEXT);")
    (d / "ingest.py").write_text(
        "import this_module_does_not_exist_anywhere\n"
        "def sync(t):\n    pass\n"
        "def backfill(t, start, end):\n    pass\n"
    )
    mark_valid(cfg, name)
    return d


def test_sync_one_import_error_gets_deps_hint_when_python_deps_declared(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_with_broken_import(cfg, "needs_dep", python_deps=["some-package>=1.0"])
    with pytest.raises(ModuleNotFoundError, match="personal-db tracker deps needs_dep"):
        sync_one(cfg, "needs_dep")


def test_backfill_one_import_error_gets_deps_hint_when_python_deps_declared(tmp_root):
    from personal_db.core.sync import backfill_one

    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_with_broken_import(cfg, "needs_dep_b", python_deps=["some-package>=1.0"])
    with pytest.raises(ModuleNotFoundError, match="personal-db tracker deps needs_dep_b"):
        backfill_one(cfg, "needs_dep_b", start=None, end=None)


def _make_tracker_with_tcc_denial(cfg: Config, name: str, *, permission_type: str) -> Path:
    d = cfg.trackers_dir / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "test",
                "permission_type": permission_type,
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    (d / "schema.sql").write_text(f"CREATE TABLE IF NOT EXISTS {name} (ts TEXT);")
    # sqlite raises exactly this text when macOS TCC blocks the open().
    (d / "ingest.py").write_text(
        "import sqlite3\n"
        "def sync(t):\n"
        "    raise sqlite3.OperationalError('unable to open database file')\n"
        "def backfill(t, start, end):\n"
        "    raise sqlite3.OperationalError('unable to open database file')\n"
    )
    mark_valid(cfg, name)
    return d


def test_sync_one_tcc_denial_gets_fda_hint_for_fda_tracker(tmp_root):
    import sqlite3

    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_with_tcc_denial(cfg, "fda_denied", permission_type="full_disk_access")
    with pytest.raises(sqlite3.OperationalError, match="Full Disk Access"):
        sync_one(cfg, "fda_denied")


def test_sync_one_tcc_denial_no_fda_hint_for_non_fda_tracker(tmp_root):
    """A non-FDA tracker hitting the same sqlite error keeps the plain message:
    for an api_key tracker "unable to open database file" is a real bug, and
    Full Disk Access guidance would send the user down the wrong path."""
    import sqlite3

    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_with_tcc_denial(cfg, "not_fda", permission_type="none")
    with pytest.raises(sqlite3.OperationalError) as exc_info:
        sync_one(cfg, "not_fda")
    assert "Full Disk Access" not in str(exc_info.value)


def test_sync_one_import_error_has_no_hint_without_declared_deps(tmp_root):
    """Keep the message plain (no hint) for a tracker that never declared
    python_deps -- the hint would just be noise/misleading there."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_with_broken_import(cfg, "no_deps_declared", python_deps=[])
    with pytest.raises(ModuleNotFoundError) as exc_info:
        sync_one(cfg, "no_deps_declared")
    assert "personal-db tracker deps" not in str(exc_info.value)


def test_sync_one_picks_up_lib_dir_populated_after_process_started(tmp_root):
    """Simulates the real long-running-daemon scenario: <root>/lib gets
    populated by a separate process (`personal-db tracker deps <name>`)
    *after* this interpreter already started, so this process's sys.path has
    never been extended with cfg.lib_dir before. sync_one must still succeed
    by re-checking <root>/lib on every call (core/sync.py) rather than
    relying solely on the one-time activate_lib_dir call at process startup
    (services/daemon/server.py) -- otherwise a long-lived daemon would need
    restarting after every `tracker deps` run.
    """
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)

    tracker_dir = cfg.trackers_dir / "needs_lib"
    tracker_dir.mkdir(parents=True)
    (tracker_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "needs_lib",
                "description": "test",
                "permission_type": "none",
                "time_column": "ts",
                "python_deps": ["pdb_lib_probe_pkg_for_sync_test"],
                "schema": {
                    "tables": {"needs_lib": {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    (tracker_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS needs_lib (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (tracker_dir / "ingest.py").write_text(
        "import pdb_lib_probe_pkg_for_sync_test as probe\n"
        "def backfill(t, start, end):\n    pass\n"
        "def sync(t):\n"
        "    t.upsert('needs_lib', [{'id': 's1', 'ts': probe.VALUE}], key=['id'])\n"
    )
    mark_valid(cfg, "needs_lib")

    # Populate <root>/lib directly -- bypassing core.pack_deps.install_python_deps
    # (which itself calls activate_lib_dir) -- to isolate sync_one's own
    # defensive re-check from that other code path.
    pkg_dir = cfg.lib_dir / "pdb_lib_probe_pkg_for_sync_test"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("VALUE = 'from-lib-dir'\n")
    assert str(cfg.lib_dir) not in sys.path  # sanity: nothing has activated it yet

    try:
        sync_one(cfg, "needs_lib")  # must not raise
    finally:
        sys.modules.pop("pdb_lib_probe_pkg_for_sync_test", None)
        if str(cfg.lib_dir) in sys.path:
            sys.path.remove(str(cfg.lib_dir))


def test_sync_one_runs_when_manifest_platform_is_portable(tmp_root, monkeypatch):
    """`platform: None` (the default -- most trackers never set this field)
    means portable: no gate at all, regardless of sys.platform."""
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _make_tracker_dir(cfg, "demo")
    monkeypatch.setattr(sys, "platform", "linux")

    sync_one(cfg, "demo")  # must not raise even on a "foreign" OS
