"""Verifies the xhs/xhs_saved trackers raise a clear pip-install hint at sync
time when the `cryptography` package (the `personal_db[xhs]` extra) isn't
installed, instead of a bare ModuleNotFoundError."""

from __future__ import annotations

import importlib
import sys

import pytest


@pytest.mark.parametrize("module_name", ["xhs", "xhs_saved"])
def test_ingest_raises_clear_error_when_cryptography_extra_missing(monkeypatch, module_name):
    full_name = f"personal_db.templates.trackers.{module_name}.ingest"
    # Reset any prior import of the target module so reload actually re-execs
    # its top-level guarded import statement.
    monkeypatch.delitem(sys.modules, full_name, raising=False)
    # sys.modules[name] = None is the documented sentinel that forces the
    # import system to raise ImportError for that module name, simulating the
    # `cryptography` package (the personal_db[xhs] extra) not being installed.
    monkeypatch.setitem(sys.modules, "cryptography", None)
    monkeypatch.setitem(sys.modules, "cryptography.hazmat.primitives.ciphers", None)

    with pytest.raises(ImportError, match=r"pip install 'personal_db\[xhs\]'"):
        importlib.import_module(full_name)
