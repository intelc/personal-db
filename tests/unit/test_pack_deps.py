"""core/pack_deps.py — install pack-declared python_deps into <root>/lib via
`pip install --target` (see core/runtime_env.py for why <root>/lib exists at
all: the signed app bundle's embedded Python is sealed).

Uses the hand-built fixture wheel from tests/_wheel_fixture_helpers.py so
`install_python_deps`'s real `pip install --target ... --upgrade` command
runs unmodified, without ever touching PyPI.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
import yaml

from personal_db.core.apps import load_app_manifest
from personal_db.core.config import Config
from personal_db.core.pack_deps import (
    DepsInstallError,
    app_python_deps,
    install_app_deps,
    install_python_deps,
    install_tracker_deps,
    tracker_python_deps,
)
from tests._wheel_fixture_helpers import MODULE_NAME, build_fixture_wheel, offline_deps


@pytest.fixture
def fixture_wheel_dir(tmp_path) -> Path:
    return build_fixture_wheel(tmp_path / "wheelhouse")


def _import_from(lib_dir: Path):
    sys.path.insert(0, str(lib_dir))
    try:
        sys.modules.pop(MODULE_NAME, None)
        return importlib.import_module(MODULE_NAME)
    finally:
        sys.path.remove(str(lib_dir))
        sys.modules.pop(MODULE_NAME, None)


def test_install_python_deps_noop_when_empty(tmp_root):
    cfg = Config(root=tmp_root)
    result = install_python_deps(cfg, "demo", [])
    assert result.installed is False
    assert "no python_deps" in result.detail


def test_install_python_deps_installs_into_lib_dir(tmp_root, fixture_wheel_dir):
    cfg = Config(root=tmp_root)
    result = install_python_deps(cfg, "demo", offline_deps(fixture_wheel_dir))
    assert result.installed is True
    assert (cfg.lib_dir / MODULE_NAME / "__init__.py").is_file()
    mod = _import_from(cfg.lib_dir)
    assert mod.VALUE == "installed-from-fixture-wheel"


def test_install_python_deps_raises_for_unresolvable_package(tmp_root, tmp_path):
    cfg = Config(root=tmp_root)
    empty_wheel_dir = tmp_path / "empty_wheelhouse"
    empty_wheel_dir.mkdir()
    with pytest.raises(DepsInstallError):
        install_python_deps(
            cfg,
            "demo",
            ["--no-index", "--find-links", str(empty_wheel_dir), "nonexistent-pkg-xyz"],
        )


def test_install_tracker_deps_reads_manifest_and_installs(tmp_root, fixture_wheel_dir):
    cfg = Config(root=tmp_root)
    tracker_dir = cfg.trackers_dir / "demo"
    tracker_dir.mkdir(parents=True)
    deps = offline_deps(fixture_wheel_dir)
    (tracker_dir / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "d",
                "permission_type": "none",
                "time_column": "ts",
                "python_deps": deps,
                "schema": {
                    "tables": {"demo": {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    assert tracker_python_deps(cfg, "demo") == deps

    result = install_tracker_deps(cfg, "demo")
    assert result.installed is True
    mod = _import_from(cfg.lib_dir)
    assert mod.VALUE == "installed-from-fixture-wheel"


def test_tracker_python_deps_raises_for_missing_tracker(tmp_root):
    cfg = Config(root=tmp_root)
    with pytest.raises(FileNotFoundError):
        tracker_python_deps(cfg, "nope")


def test_install_app_deps_reads_manifest_and_installs(tmp_root, fixture_wheel_dir):
    cfg = Config(root=tmp_root)
    app_dir = cfg.apps_dir / "demo_app"
    app_dir.mkdir(parents=True)
    deps = offline_deps(fixture_wheel_dir)
    (app_dir / "app.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo_app",
                "pages": [{"slug": "home", "title": "Home", "view": "render_home"}],
                "python_deps": deps,
            }
        )
    )
    assert app_python_deps(cfg, "demo_app") == deps
    assert load_app_manifest(app_dir / "app.yaml").python_deps == tuple(deps)

    result = install_app_deps(cfg, "demo_app")
    assert result.installed is True
    mod = _import_from(cfg.lib_dir)
    assert mod.VALUE == "installed-from-fixture-wheel"


def test_app_python_deps_raises_for_missing_app(tmp_root):
    cfg = Config(root=tmp_root)
    with pytest.raises(FileNotFoundError):
        app_python_deps(cfg, "nope")
