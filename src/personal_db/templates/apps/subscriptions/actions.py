from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from personal_db.apps import AppContext
from personal_db.db import connect


def _text(payload: dict[str, Any], key: str, *, required: bool = False) -> str:
    value = str(payload.get(key) or "").strip()
    if required and not value:
        raise ValueError(f"{key} is required")
    return value


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return re.sub(r"_+", "_", text)[:80] or "unknown"


def _json_refs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = payload.get("refs_json") or payload.get("refs") or "[]"
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = []
    else:
        parsed = raw
    if not isinstance(parsed, list):
        return []

    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in parsed:
        if isinstance(item, str):
            ref = item.strip()
            entry: dict[str, Any] = {"ref": ref}
        elif isinstance(item, dict):
            ref = str(item.get("ref") or "").strip()
            entry = dict(item)
            entry["ref"] = ref
        else:
            continue
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(entry)
    return refs


def _ensure_note_schema(con) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS ui_notes (
          note_id    TEXT PRIMARY KEY,
          body       TEXT NOT NULL,
          created_at TEXT NOT NULL DEFAULT (datetime('now')),
          updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS ui_note_refs (
          note_id       TEXT NOT NULL,
          ref           TEXT NOT NULL,
          ref_kind      TEXT,
          label         TEXT,
          metadata_json TEXT,
          created_at    TEXT NOT NULL DEFAULT (datetime('now')),
          PRIMARY KEY (note_id, ref),
          FOREIGN KEY(note_id) REFERENCES ui_notes(note_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_ui_note_refs_ref
          ON ui_note_refs(ref);
        """
    )


def _notes_for_ref(con, ref: str) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT n.note_id, n.body, n.created_at, n.updated_at
        FROM ui_notes n
        JOIN ui_note_refs r ON r.note_id = n.note_id
        WHERE r.ref = ?
        ORDER BY n.created_at DESC
        """,
        (ref,),
    ).fetchall()
    return [
        {
            "note_id": str(row[0]),
            "body": str(row[1] or ""),
            "created_at": str(row[2] or ""),
            "updated_at": str(row[3] or ""),
        }
        for row in rows
    ]


def _ensure_finance_classification_schema(ctx: AppContext) -> None:
    ctx.require_write_tables(
        "finance_categories",
        "finance_transaction_user_categories",
        "app_finance_burn_rules",
        "app_finance_burn_buckets",
    )
    con = connect(ctx.cfg.db_path)
    try:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS finance_categories (
              category   TEXT PRIMARY KEY,
              label      TEXT NOT NULL,
              parent     TEXT,
              sort_order INTEGER NOT NULL DEFAULT 1000,
              source     TEXT NOT NULL DEFAULT 'user',
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS finance_transaction_user_categories (
              finance_transaction_id TEXT PRIMARY KEY,
              user_category          TEXT NOT NULL,
              note                   TEXT,
              updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS app_finance_burn_rules (
              rule_id             INTEGER PRIMARY KEY AUTOINCREMENT,
              rule_key            TEXT NOT NULL UNIQUE,
              priority            INTEGER NOT NULL DEFAULT 1000,
              label               TEXT NOT NULL,
              bucket              TEXT NOT NULL,
              merchant_pattern    TEXT,
              category_pattern    TEXT,
              category_match_type TEXT NOT NULL DEFAULT 'contains',
              flag_name           TEXT,
              amount_direction    TEXT NOT NULL DEFAULT 'any',
              min_amount          REAL,
              reason              TEXT,
              source              TEXT NOT NULL DEFAULT 'user',
              enabled             INTEGER NOT NULL DEFAULT 1,
              created_at          TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS app_finance_burn_buckets (
              bucket     TEXT PRIMARY KEY,
              label      TEXT NOT NULL,
              emoji      TEXT,
              sort_order INTEGER NOT NULL DEFAULT 1000,
              source     TEXT NOT NULL DEFAULT 'user',
              color      TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        con.commit()
    finally:
        con.close()


def _clear_materialized_subscription(ctx: AppContext, subscription_id: str) -> None:
    ctx.require_write_tables(
        "subscription_entities",
        "subscription_charges",
        "subscription_usage_evidence",
        "subscription_utilization_periods",
    )
    con = connect(ctx.cfg.db_path)
    try:
        for table in (
            "subscription_utilization_periods",
            "subscription_usage_evidence",
            "subscription_charges",
            "subscription_entities",
        ):
            con.execute(f"DELETE FROM {table} WHERE subscription_id=?", (subscription_id,))
        con.commit()
    finally:
        con.close()


def mark_not_subscription(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a false-positive judgment in finance-owned classification state."""
    subscription_id = _text(payload, "subscription_id", required=True)
    merchant = _text(payload, "merchant", required=True)
    label = _text(payload, "label") or merchant
    category = _text(payload, "category") or "Entertainment"
    bucket = _text(payload, "bucket") or "entertainment"
    merchant_pattern = merchant.lower()
    now = datetime.now(UTC).isoformat()

    _ensure_finance_classification_schema(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        charge_ids = [
            str(row[0])
            for row in con.execute(
                """
                SELECT finance_transaction_id
                FROM subscription_charges
                WHERE subscription_id=?
                """,
                (subscription_id,),
            ).fetchall()
        ]
        con.execute(
            """
            INSERT INTO finance_categories(category, label, source, created_at, updated_at)
            VALUES (?, ?, 'user', ?, ?)
            ON CONFLICT(category) DO NOTHING
            """,
            (category, category, now, now),
        )
        con.execute(
            """
            INSERT INTO app_finance_burn_buckets(bucket, label, emoji, sort_order, source, color, updated_at)
            VALUES (?, 'Entertainment', '🎬', 55, 'system', '', ?)
            ON CONFLICT(bucket) DO UPDATE SET
              label=excluded.label,
              emoji=COALESCE(NULLIF(app_finance_burn_buckets.emoji, ''), excluded.emoji),
              updated_at=excluded.updated_at
            """,
            (bucket, now),
        )
        con.execute(
            """
            INSERT INTO app_finance_burn_rules(
              rule_key, priority, label, bucket, merchant_pattern,
              amount_direction, reason, source, updated_at
            )
            VALUES (?, 25, ?, ?, ?, 'positive', 'marked not subscription', 'user', ?)
            ON CONFLICT(rule_key) DO UPDATE SET
              priority=excluded.priority,
              label=excluded.label,
              bucket=excluded.bucket,
              merchant_pattern=excluded.merchant_pattern,
              amount_direction=excluded.amount_direction,
              reason=excluded.reason,
              enabled=1,
              updated_at=excluded.updated_at
            """,
            (
                f"user:not_subscription:merchant:{_slug(merchant_pattern)}",
                f'{label} is not a subscription',
                bucket,
                merchant_pattern,
                now,
            ),
        )
        con.executemany(
            """
            INSERT INTO finance_transaction_user_categories(
              finance_transaction_id, user_category, note, updated_at
            )
            VALUES (?, ?, 'marked not subscription', ?)
            ON CONFLICT(finance_transaction_id) DO UPDATE SET
              user_category=excluded.user_category,
              note=excluded.note,
              updated_at=excluded.updated_at
            """,
            [(charge_id, category, now) for charge_id in charge_ids],
        )
        con.commit()
    finally:
        con.close()

    _clear_materialized_subscription(ctx, subscription_id)
    return {
        "ok": True,
        "subscription_id": subscription_id,
        "merchant": merchant,
        "category": category,
        "bucket": bucket,
        "updated_transactions": len(charge_ids),
    }


def create_note(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Create a UI note attached to one or more flexible references."""
    ctx.require_write_tables("ui_notes", "ui_note_refs")
    body = _text(payload, "body", required=True)
    primary_ref = _text(payload, "primary_ref", required=True)
    label = _text(payload, "label")
    refs = _json_refs(payload)
    if primary_ref not in {str(ref.get("ref") or "") for ref in refs}:
        refs.insert(0, {"ref": primary_ref, "ref_kind": "ui_target", "label": label})

    now = datetime.now(UTC).isoformat()
    note_id = f"note_{uuid4().hex}"
    con = connect(ctx.cfg.db_path)
    try:
        _ensure_note_schema(con)
        con.execute(
            """
            INSERT INTO ui_notes(note_id, body, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (note_id, body, now, now),
        )
        for ref in refs:
            ref_value = str(ref.get("ref") or "").strip()
            if not ref_value:
                continue
            metadata = ref.get("metadata")
            con.execute(
                """
                INSERT OR IGNORE INTO ui_note_refs(
                  note_id, ref, ref_kind, label, metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    note_id,
                    ref_value,
                    str(ref.get("ref_kind") or ""),
                    str(ref.get("label") or label or ""),
                    json.dumps(metadata, sort_keys=True) if isinstance(metadata, dict) else None,
                    now,
                ),
            )
        con.commit()
        notes = _notes_for_ref(con, primary_ref)
    finally:
        con.close()

    return {
        "ok": True,
        "note": {"note_id": note_id, "body": body, "created_at": now, "updated_at": now},
        "primary_ref": primary_ref,
        "notes": notes,
    }
