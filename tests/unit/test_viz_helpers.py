import sqlite3
from datetime import date

import pytest

from personal_db.core.config import Config
from personal_db.core.db import init_db
from personal_db.core.viz_helpers import daily_series, meta


def test_meta_wraps_text():
    assert meta("no data") == '<p class="meta">no data</p>'


def test_daily_series_fills_missing_days(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)

    db = sqlite3.connect(cfg.db_path)
    try:
        db.execute("CREATE TABLE sample_daily(day TEXT PRIMARY KEY, score INTEGER)")
        db.execute(
            "INSERT INTO sample_daily(day, score) VALUES (?, 7)",
            (date.today().isoformat(),),
        )
        db.commit()
    finally:
        db.close()

    rows = daily_series(cfg, "sample_daily", "score", 3)
    assert rows is not None
    assert len(rows) == 3
    assert rows[-1][1] == 7
    assert rows[0][1] == 0


def test_daily_series_rejects_unsafe_identifiers(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)

    with pytest.raises(ValueError, match="unsafe SQLite identifier"):
        daily_series(cfg, "sample_daily; DROP TABLE sample_daily", "score", 3)
