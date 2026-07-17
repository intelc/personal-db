"""Phase 2b: the action_log audit table (core/action_log.py)."""

from __future__ import annotations

import json
import sqlite3

from personal_db.core.action_log import log_action_result, log_action_start
from personal_db.core.config import Config


def _rows(cfg: Config) -> list[sqlite3.Row]:
    con = sqlite3.connect(cfg.db_path)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("SELECT * FROM action_log ORDER BY id").fetchall()
    finally:
        con.close()


def test_log_action_start_writes_row_with_null_result(tmp_root):
    cfg = Config(root=tmp_root)
    row_id = log_action_start(
        cfg, surface="tracker_action", extension="habits", action="hello", params={"x": 1}
    )
    rows = _rows(cfg)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == row_id
    assert row["surface"] == "tracker_action"
    assert row["extension"] == "habits"
    assert row["action"] == "hello"
    assert json.loads(row["params_json"]) == {"x": 1}
    assert row["result"] is None
    assert row["ts"]


def test_log_action_result_updates_existing_row(tmp_root):
    cfg = Config(root=tmp_root)
    row_id = log_action_start(cfg, surface="app_action", extension="finance", action="mark_paid")
    log_action_result(cfg, row_id, "ok")
    row = _rows(cfg)[0]
    assert row["result"] == "ok"


def test_log_action_result_records_errors(tmp_root):
    cfg = Config(root=tmp_root)
    row_id = log_action_start(cfg, surface="background_job", extension="tracker:finance", action="run")
    log_action_result(cfg, row_id, "error: boom")
    row = _rows(cfg)[0]
    assert row["result"] == "error: boom"


def test_multiple_rows_get_distinct_ids(tmp_root):
    cfg = Config(root=tmp_root)
    id1 = log_action_start(cfg, surface="tracker_action", extension="a", action="x")
    id2 = log_action_start(cfg, surface="tracker_action", extension="b", action="y")
    assert id1 != id2
    assert len(_rows(cfg)) == 2
