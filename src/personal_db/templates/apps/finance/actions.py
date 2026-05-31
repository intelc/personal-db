from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.db import connect

_KINDS = {"parent_draw", "recurring_candidate"}
_STATUSES = {"reviewed", "ignored"}
_MAX_CATEGORY_LEN = 80


def _text(payload: dict[str, Any], key: str, *, required: bool = False) -> str:
    value = str(payload.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    return value


def _validate_kind(value: str) -> str:
    kind = value or "parent_draw"
    if kind not in _KINDS:
        raise ValueError(f"unknown review kind: {kind}")
    return kind


def _validate_status(value: str) -> str:
    status = value or "reviewed"
    if status not in _STATUSES:
        raise ValueError(f"unknown review status: {status}")
    return status


def mark_reviewed(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Mark a finance review item as handled.

    The review key should be a stable app-level key, such as a transaction id
    or a normalized recurring-candidate key. This intentionally stores only
    review state, not source-owned finance facts.
    """
    review_key = _text(payload, "review_key", required=True)
    kind = _validate_kind(_text(payload, "kind") or "parent_draw")
    status = _validate_status(_text(payload, "status") or "reviewed")
    note = _text(payload, "note") or None
    updated_at = datetime.now(UTC).isoformat()
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            """
            INSERT INTO app_finance_reviews(review_key, kind, status, note, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(review_key) DO UPDATE SET
              kind=excluded.kind,
              status=excluded.status,
              note=excluded.note,
              updated_at=excluded.updated_at
            """,
            (review_key, kind, status, note, updated_at),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "review_key": review_key, "kind": kind, "status": status}


def clear_review(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    review_key = _text(payload, "review_key", required=True)
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute("DELETE FROM app_finance_reviews WHERE review_key=?", (review_key,))
        con.commit()
    finally:
        con.close()
    return {"ok": True, "review_key": review_key, "cleared": True}


def _category(payload: dict[str, Any]) -> str:
    category = _text(payload, "category", required=True)
    if len(category) > _MAX_CATEGORY_LEN:
        raise ValueError(f"category must be {_MAX_CATEGORY_LEN} characters or fewer")
    return category


def set_transaction_category(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Store an app-owned category override for a finance transaction."""
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    category = _category(payload)
    note = _text(payload, "note") or None
    updated_at = datetime.now(UTC).isoformat()
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            """
            INSERT INTO app_finance_transaction_categories(
              finance_transaction_id, category, note, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(finance_transaction_id) DO UPDATE SET
              category=excluded.category,
              note=excluded.note,
              updated_at=excluded.updated_at
            """,
            (transaction_id, category, note, updated_at),
        )
        con.execute(
            """
            INSERT INTO app_finance_category_presets(category, created_at)
            VALUES (?, ?)
            ON CONFLICT(category) DO NOTHING
            """,
            (category, updated_at),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "finance_transaction_id": transaction_id, "category": category}


def clear_transaction_category(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            "DELETE FROM app_finance_transaction_categories WHERE finance_transaction_id=?",
            (transaction_id,),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "finance_transaction_id": transaction_id, "cleared": True}
