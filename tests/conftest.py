from pathlib import Path

import pytest


@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Fresh personal_db root for each test."""
    root = tmp_path / "personal_db"
    for sub in ("trackers", "entities", "notes", "state", "state/oauth"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
