"""core/runtime_env.py::activate_lib_dir — the sys.path extension point for
pack-declared python_deps (see CONTEXT in core/runtime_env.py's docstring:
the signed app bundle's embedded Python is sealed, so a pack's deps can only
ever land in <root>/lib, never in the bundle's own site-packages)."""

from __future__ import annotations

import importlib
import sys

from personal_db.core.config import Config
from personal_db.core.runtime_env import (
    activate_lib_dir,
    app_bundle_cli,
    is_app_bundle,
    resolve_app_bundle_root,
)


def test_activate_lib_dir_noop_when_missing(tmp_root):
    cfg = Config(root=tmp_root)
    assert not cfg.lib_dir.exists()
    assert activate_lib_dir(cfg) is False
    assert str(cfg.lib_dir) not in sys.path


def test_activate_lib_dir_adds_dir_and_makes_package_importable(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.lib_dir.mkdir(parents=True)
    pkg_dir = cfg.lib_dir / "pdb_fake_pack_dep"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("VALUE = 'from lib dir'\n")

    try:
        assert activate_lib_dir(cfg) is True
        assert str(cfg.lib_dir) in sys.path
        sys.modules.pop("pdb_fake_pack_dep", None)
        mod = importlib.import_module("pdb_fake_pack_dep")
        assert mod.VALUE == "from lib dir"
    finally:
        sys.modules.pop("pdb_fake_pack_dep", None)
        if str(cfg.lib_dir) in sys.path:
            sys.path.remove(str(cfg.lib_dir))


def test_activate_lib_dir_is_idempotent(tmp_root):
    """Calling it twice (every entrypoint calls it once at its own startup)
    must not produce duplicate sys.path entries."""
    cfg = Config(root=tmp_root)
    cfg.lib_dir.mkdir(parents=True)

    try:
        activate_lib_dir(cfg)
        activate_lib_dir(cfg)
        assert sys.path.count(str(cfg.lib_dir)) == 1
    finally:
        while str(cfg.lib_dir) in sys.path:
            sys.path.remove(str(cfg.lib_dir))


def test_activate_lib_dir_appends_after_existing_entries_so_it_cannot_shadow(tmp_root):
    """Ordering guarantee: lib_dir must land *after* whatever's already on
    sys.path (stdlib + the bundle's own site-packages in production), so a
    same-named package already importable from an earlier sys.path entry
    always wins over one a pack installed into <root>/lib. This is the
    property that keeps a pack's dependency from shadowing the engine's own.
    """
    cfg = Config(root=tmp_root)
    cfg.lib_dir.mkdir(parents=True)

    # Simulate "the engine's own copy" of a module living earlier on sys.path.
    bundled_dir = cfg.root / "fake_bundled_site_packages"
    bundled_dir.mkdir()
    (bundled_dir / "pdb_shadow_probe.py").write_text("SOURCE = 'bundled'\n")
    (cfg.lib_dir / "pdb_shadow_probe.py").write_text("SOURCE = 'pack lib dir'\n")

    sys.path.insert(0, str(bundled_dir))
    try:
        before_index = sys.path.index(str(bundled_dir))
        activate_lib_dir(cfg)
        after_index = sys.path.index(str(cfg.lib_dir))
        assert after_index > before_index, (
            "lib_dir must be appended after already-present sys.path entries"
        )

        sys.modules.pop("pdb_shadow_probe", None)
        mod = importlib.import_module("pdb_shadow_probe")
        assert mod.SOURCE == "bundled", (
            "a same-named module earlier on sys.path (standing in for the "
            "bundle's own site-packages) must win over the pack's lib_dir copy"
        )
    finally:
        sys.modules.pop("pdb_shadow_probe", None)
        if str(bundled_dir) in sys.path:
            sys.path.remove(str(bundled_dir))
        while str(cfg.lib_dir) in sys.path:
            sys.path.remove(str(cfg.lib_dir))


# --- is_app_bundle / app_bundle_cli / resolve_app_bundle_root ---
#
# The packaged PersonalDB.app's sidecar runs as
# `<bundle>/Contents/MacOS/personal-db-daemon`; a dev venv/uv python never has
# an `.app/Contents/...` path segment. These are monkeypatched via
# sys.executable so no real app bundle is needed to exercise the logic (see
# services/daemon/routes/setup.py and services/wizard/mcp_setup.py, which
# both branch on is_app_bundle()).


def test_resolve_app_bundle_root_finds_dot_app_segment():
    from pathlib import Path

    p = Path("/Applications/PersonalDB.app/Contents/MacOS/personal-db-daemon")
    assert resolve_app_bundle_root(p) == Path("/Applications/PersonalDB.app")


def test_resolve_app_bundle_root_none_for_plain_venv_path():
    from pathlib import Path

    p = Path("/Users/me/venv/bin/python3")
    assert resolve_app_bundle_root(p) is None


def test_resolve_app_bundle_root_requires_contents_sibling():
    """A directory that merely ends in .app but isn't followed by Contents/
    (e.g. a coincidentally named folder) must not match."""
    from pathlib import Path

    p = Path("/Users/me/Downloads/notabundle.app/readme.txt")
    assert resolve_app_bundle_root(p) is None


def test_is_app_bundle_false_for_dev_interpreter(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/Users/me/.venv/bin/python3")
    assert is_app_bundle() is False


def test_is_app_bundle_true_inside_bundle(monkeypatch, tmp_path):
    exe = tmp_path / "PersonalDB.app" / "Contents" / "MacOS" / "personal-db-daemon"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert is_app_bundle() is True


def test_app_bundle_cli_none_outside_bundle(monkeypatch):
    monkeypatch.setattr(sys, "executable", "/Users/me/.venv/bin/python3")
    assert app_bundle_cli() is None


def test_app_bundle_cli_none_when_wrapper_missing(monkeypatch, tmp_path):
    exe = tmp_path / "PersonalDB.app" / "Contents" / "MacOS" / "personal-db-daemon"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(exe))
    # No cli/personal-db wrapper written -- app_bundle_cli should report None,
    # not a path to a file that doesn't exist.
    assert app_bundle_cli() is None


def test_app_bundle_cli_returns_wrapper_when_present(monkeypatch, tmp_path):
    bundle = tmp_path / "PersonalDB.app"
    exe = bundle / "Contents" / "MacOS" / "personal-db-daemon"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/sh\n")
    wrapper = bundle / "Contents" / "Resources" / "cli" / "personal-db"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\n")
    monkeypatch.setattr(sys, "executable", str(exe))
    assert app_bundle_cli() == wrapper.resolve()
