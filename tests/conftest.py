import datetime as _datetime_module
from pathlib import Path

import pytest


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Fresh personal_db root for each test."""
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root


@pytest.fixture
def frozen_datetime(monkeypatch):
    """Freeze `datetime.datetime.now()` to a fixed instant for this test.

    A handful of bundled templates compute "recent window" cutoffs via
    `datetime.now()` at call time rather than accepting an injected clock, and
    several of them (app views loaded through `core.apps.load_app_module`,
    tracker `ingest.py` loaded through `core.sync._load_ingest_module`) are
    re-imported fresh from disk on every call -- so monkeypatching an
    already-imported module object's `datetime` name doesn't survive to the
    next reload. Patching the real `datetime` module's `datetime` class
    instead works uniformly: any `from datetime import datetime` executed
    after this fixture applies -- including inside a freshly re-exec'd module
    -- binds to the frozen subclass.

    Returns a callable: `frozen_datetime(2026, 4, 26)` (optionally hour/minute/
    second) freezes "now" to that UTC instant and returns the frozen
    `datetime` for the test's own use (e.g. to compute expected values).
    """

    def _freeze(
        year: int, month: int, day: int, hour: int = 12, minute: int = 0, second: int = 0
    ) -> _datetime_module.datetime:
        fixed = _datetime_module.datetime(
            year, month, day, hour, minute, second, tzinfo=_datetime_module.UTC
        )

        class _FrozenDatetime(_datetime_module.datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed.astimezone(tz) if tz is not None else fixed.replace(tzinfo=None)

        monkeypatch.setattr(_datetime_module, "datetime", _FrozenDatetime)
        return fixed

    return _freeze
