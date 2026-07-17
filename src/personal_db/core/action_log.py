"""Action audit log: core table `action_log` (see core/db.py for the schema).

Every daemon-dispatched action — a tracker's `actions.py` handler, an app's
`actions.py` handler, or a declared background-job tick — gets a row written
BEFORE dispatch (`result` NULL), with `result` filled in afterward ("ok" or
"error: ..."). Kept lean on purpose: no UI surface yet, just an append-only
record for later phases (and for anyone debugging "what did the agent just
do to my data").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from personal_db.core.config import Config
from personal_db.core.db import transaction


def _ensure_action_log_table(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS action_log ("
        "id INTEGER PRIMARY KEY, ts TEXT NOT NULL, surface TEXT NOT NULL, "
        "extension TEXT, action TEXT NOT NULL, params_json TEXT, result TEXT)"
    )


def log_action_start(
    cfg: Config,
    *,
    surface: str,
    extension: str | None,
    action: str,
    params: dict[str, Any] | None = None,
) -> int:
    """Insert a row before dispatch (result NULL). Returns the row id, to be
    passed to `log_action_result` once dispatch finishes."""
    with transaction(cfg.db_path) as con:
        _ensure_action_log_table(con)
        cur = con.execute(
            "INSERT INTO action_log (ts, surface, extension, action, params_json, result) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (
                datetime.now(UTC).isoformat(),
                surface,
                extension,
                action,
                json.dumps(params, default=str) if params is not None else None,
            ),
        )
        row_id = cur.lastrowid
    assert row_id is not None
    return row_id


def log_action_result(cfg: Config, row_id: int, result: str) -> None:
    with transaction(cfg.db_path) as con:
        _ensure_action_log_table(con)
        con.execute("UPDATE action_log SET result = ? WHERE id = ?", (result, row_id))
