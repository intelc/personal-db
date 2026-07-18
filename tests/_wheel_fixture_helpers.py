"""Shared test helper: a tiny hand-built wheel fixture for exercising
core/pack_deps.py's real `pip install --target` path without ever touching
PyPI.

A wheel is just a zip file with a couple of metadata files, so this is built
directly with `zipfile` rather than via `python -m pip wheel`/a build
backend -- some dev environments (this one included) have neither
`setuptools` nor `wheel` installed, and building a wheel from source needs
one of those (or network access to fetch one). Installing a *pre-built*
wheel, unlike building one, needs no build backend at all -- just pip --
so `install_python_deps`'s unmodified `pip install --target ... --upgrade`
command runs against it via `--no-index --find-links` spliced into the
"requirement list" exactly like a real manifest's python_deps entries.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

DIST_NAME = "pdb-test-fixture-pkg"
MODULE_NAME = "pdb_test_fixture_pkg"
VERSION = "0.0.1"


def build_fixture_wheel(wheel_dir: Path) -> Path:
    wheel_dir.mkdir(parents=True, exist_ok=True)
    wheel_path = wheel_dir / f"{MODULE_NAME}-{VERSION}-py3-none-any.whl"
    dist_info = f"{MODULE_NAME}-{VERSION}.dist-info"
    with zipfile.ZipFile(wheel_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{MODULE_NAME}/__init__.py", "VALUE = 'installed-from-fixture-wheel'\n")
        zf.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: {DIST_NAME}\nVersion: {VERSION}\n",
        )
        zf.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: pdb-test-fixture\n"
            "Root-Is-Purelib: true\nTag: py3-none-any\n",
        )
        zf.writestr(f"{dist_info}/RECORD", "")
    return wheel_dir


def offline_deps(wheel_dir: Path) -> list[str]:
    """A python_deps-shaped requirement list that installs `DIST_NAME` from
    the local wheel_dir instead of PyPI. `--no-index`/`--find-links` are
    ordinary pip CLI options, so splicing them into the requirement list
    works with the unmodified command core.pack_deps.install_python_deps
    builds."""
    return ["--no-index", "--find-links", str(wheel_dir), DIST_NAME]
