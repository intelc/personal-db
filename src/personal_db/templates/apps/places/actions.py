from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.db import connect


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _payload_text(payload: dict[str, Any], key: str, *, max_len: int = 300) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ValueError(f"missing {key}")
    if len(value) > max_len:
        raise ValueError(f"{key} too long")
    return value


def set_privacy(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist app-owned display preferences.

    The action name is kept for compatibility with already-installed templates.
    """

    apply_app_schema(ctx.cfg, ctx.app_dir)
    blur_raw = str(payload.get("blur_precision_m") or "0").strip()
    days_raw = str(payload.get("default_days") or "30").strip()
    try:
        blur = max(0, min(5000, int(float(blur_raw))))
        days = max(1, min(3650, int(float(days_raw))))
    except ValueError as exc:
        raise ValueError("blur_precision_m and default_days must be numbers") from exc

    hide = payload.get("hide_coordinates")
    hide_value = "1" if str(hide).lower() in {"1", "true", "yes", "on"} else "0"
    rows = [
        ("blur_precision_m", str(blur), _now()),
        ("hide_coordinates", hide_value, _now()),
        ("default_days", str(days), _now()),
    ]
    con = connect(ctx.cfg.db_path)
    try:
        con.executemany(
            """
            INSERT INTO app_places_settings(key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
              value=excluded.value,
              updated_at=excluded.updated_at
            """,
            rows,
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "settings": {key: value for key, value, _ in rows}}


def set_place_alias(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a local alias or hidden flag for a place label."""

    apply_app_schema(ctx.cfg, ctx.app_dir)
    place_name = _payload_text(payload, "place_name")
    alias = str(payload.get("alias") or "").strip() or place_name
    if len(alias) > 300:
        raise ValueError("alias too long")
    hidden = 1 if str(payload.get("hidden") or "").lower() in {"1", "true", "yes", "on"} else 0
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            """
            INSERT INTO app_places_aliases(place_name, alias, hidden, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(place_name) DO UPDATE SET
              alias=excluded.alias,
              hidden=excluded.hidden,
              updated_at=excluded.updated_at
            """,
            (place_name, alias, hidden, _now()),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "place_name": place_name, "alias": alias, "hidden": bool(hidden)}


def clear_place_alias(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    place_name = _payload_text(payload, "place_name")
    con = connect(ctx.cfg.db_path)
    try:
        cur = con.execute("DELETE FROM app_places_aliases WHERE place_name=?", (place_name,))
        con.commit()
        removed = cur.rowcount
    except sqlite3.Error:
        removed = 0
    finally:
        con.close()
    return {"ok": True, "removed": removed}
