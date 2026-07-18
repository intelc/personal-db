"""core/runtime_env.py::activate_lib_dir — the sys.path extension point for
pack-declared python_deps (see CONTEXT in core/runtime_env.py's docstring:
the signed app bundle's embedded Python is sealed, so a pack's deps can only
ever land in <root>/lib, never in the bundle's own site-packages)."""

from __future__ import annotations

import importlib
import sys

from personal_db.core.config import Config
from personal_db.core.runtime_env import activate_lib_dir


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
