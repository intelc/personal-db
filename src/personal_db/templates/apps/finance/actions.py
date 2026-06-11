from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.db import connect
from personal_db.enrichments.core import apply_enrichment_schema, enqueue_enrichment_job
from personal_db.enrichments.finance import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    RECEIPT_V1_ENRICHMENT,
)

_KINDS = {"parent_draw", "recurring_candidate"}
_STATUSES = {"reviewed", "ignored"}
_MAX_CATEGORY_LEN = 80
_BASE_BURN_BUCKETS = {
    "rent",
    "food",
    "transportation",
    "ai",
    "health",
    "entertainment",
    "subscriptions",
    "other",
}
_RESERVED_BURN_BUCKETS = {*_BASE_BURN_BUCKETS, "exclude"}
_BURN_SCOPES = {"transaction", "merchant", "category"}
_MAX_BURN_BUCKET_LABEL_LEN = 40
_MAX_BURN_BUCKET_EMOJI_LEN = 12
_BURN_BUCKET_COLORS = {"", "red", "orange", "yellow", "green", "blue", "purple", "pink"}
_CATEGORY_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS finance_categories (
  category   TEXT PRIMARY KEY,
  label      TEXT NOT NULL,
  parent     TEXT,
  sort_order INTEGER NOT NULL DEFAULT 1000,
  source     TEXT NOT NULL DEFAULT 'user',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_finance_categories_parent
  ON finance_categories(parent);

CREATE TABLE IF NOT EXISTS finance_transaction_user_categories (
  finance_transaction_id TEXT PRIMARY KEY,
  user_category          TEXT NOT NULL,
  note                   TEXT,
  updated_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_finance_tx_user_categories_category
  ON finance_transaction_user_categories(user_category);
"""
_DEFAULT_BURN_BUCKETS = [
    ("rent", "Rent", "🏠", 10, "system", ""),
    ("food", "Food", "🍽️", 20, "system", ""),
    ("transportation", "Transportation", "🚕", 30, "system", ""),
    ("ai", "AI spending", "🤖", 40, "system", ""),
    ("health", "Health", "🩺", 50, "system", ""),
    ("entertainment", "Entertainment", "🎬", 55, "system", ""),
    ("subscriptions", "Other subscriptions", "🔁", 60, "system", ""),
    ("other", "Other", "📦", 800, "system", ""),
    ("wasted", "Wasted", "🗑️", 900, "user", "red"),
]


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


def _ensure_category_schema(ctx: AppContext) -> None:
    ctx.require_write_tables("finance_categories", "finance_transaction_user_categories")
    con = connect(ctx.cfg.db_path)
    try:
        con.executescript(_CATEGORY_SCHEMA_SQL)
        con.commit()
    finally:
        con.close()


def set_transaction_category(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a canonical user category for a finance transaction."""
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    category = _category(payload)
    note = _text(payload, "note") or None
    updated_at = datetime.now(UTC).isoformat()
    apply_app_schema(ctx.cfg, ctx.app_dir)
    _ensure_category_schema(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            """
            INSERT INTO finance_transaction_user_categories(
              finance_transaction_id, user_category, note, updated_at
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(finance_transaction_id) DO UPDATE SET
              user_category=excluded.user_category,
              note=excluded.note,
              updated_at=excluded.updated_at
            """,
            (transaction_id, category, note, updated_at),
        )
        con.execute(
            """
            INSERT INTO finance_categories(category, label, source, created_at, updated_at)
            VALUES (?, ?, 'user', ?, ?)
            ON CONFLICT(category) DO NOTHING
            """,
            (category, category, updated_at, updated_at),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "finance_transaction_id": transaction_id, "category": category}


def clear_transaction_category(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    apply_app_schema(ctx.cfg, ctx.app_dir)
    _ensure_category_schema(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            "DELETE FROM finance_transaction_user_categories WHERE finance_transaction_id=?",
            (transaction_id,),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "finance_transaction_id": transaction_id, "cleared": True}


def rerun_receipt_enrichment(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Queue a forced v1 receipt enrichment rerun for one finance transaction."""
    ctx.require_write_tables("enrichment_jobs")
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    window_days = _positive_int(_text(payload, "window_days") or "7", default=7)
    max_threads = _positive_int(_text(payload, "max_threads") or "3", default=3)
    max_candidate_threads = _positive_int(
        _text(payload, "max_candidate_threads")
        or str(DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS),
        default=DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    )
    apply_enrichment_schema(ctx.cfg)
    _ensure_finance_transaction_exists(ctx, transaction_id)
    job = enqueue_enrichment_job(
        ctx.cfg,
        enrichment_name=RECEIPT_V1_ENRICHMENT,
        input_table="finance_transactions",
        input_id=transaction_id,
        priority=50,
        payload={
            "window_days": window_days,
            "scope": _text(payload, "scope") or None,
            "max_threads": max_threads,
            "max_candidate_threads": max_candidate_threads,
            "snippet_window_chars": _positive_int(
                _text(payload, "snippet_window_chars") or "300",
                default=300,
            ),
            "requested_from": "finance_app_receipts",
        },
        force=True,
    )
    return {
        "ok": True,
        "finance_transaction_id": transaction_id,
        "enrichment_name": RECEIPT_V1_ENRICHMENT,
        **job,
    }


def _positive_int(value: str, *, default: int) -> int:
    try:
        parsed = int(value)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _ensure_finance_transaction_exists(ctx: AppContext, transaction_id: str) -> None:
    con = connect(ctx.cfg.db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT 1 FROM finance_transactions WHERE finance_transaction_id=?",
            (transaction_id,),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        raise ValueError(f"unknown finance transaction: {transaction_id}")


def _burn_bucket(ctx: AppContext, payload: dict[str, Any]) -> str:
    bucket = _text(payload, "bucket", required=True)
    if bucket == "exclude" or bucket not in _known_burn_buckets(ctx):
        raise ValueError(f"unknown burn bucket: {bucket}")
    return bucket


def _classification_bucket(ctx: AppContext, payload: dict[str, Any]) -> str:
    bucket = _text(payload, "bucket", required=True)
    if bucket not in _known_burn_buckets(ctx):
        raise ValueError(f"unknown burn bucket: {bucket}")
    return bucket


def _burn_scope(payload: dict[str, Any]) -> str:
    scope = _text(payload, "scope") or "transaction"
    if scope not in _BURN_SCOPES:
        raise ValueError(f"unknown burn rule scope: {scope}")
    return scope


def _rule_key(scope: str, pattern: str) -> str:
    normalized = " ".join(pattern.lower().split())
    return f"user:{scope}:{normalized}"


def _burn_bucket_slug(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    slug = re.sub(r"_+", "_", slug)
    if not slug:
        raise ValueError("bucket name must include a letter or number")
    if slug in _RESERVED_BURN_BUCKETS:
        raise ValueError(f"burn bucket already exists: {label}")
    return slug[:40]


def _burn_bucket_color(payload: dict[str, Any]) -> str:
    color = _text(payload, "color").lower()
    if color not in _BURN_BUCKET_COLORS:
        raise ValueError(f"unknown burn bucket color: {color}")
    return color


def _burn_bucket_emoji(payload: dict[str, Any]) -> str:
    emoji = _text(payload, "emoji")
    if len(emoji) > _MAX_BURN_BUCKET_EMOJI_LEN:
        raise ValueError(f"bucket emoji must be {_MAX_BURN_BUCKET_EMOJI_LEN} characters or fewer")
    return emoji


def _ensure_burn_bucket_metadata(ctx: AppContext) -> None:
    updated_at = datetime.now(UTC).isoformat()
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS app_finance_burn_buckets (
              bucket     TEXT PRIMARY KEY,
              label      TEXT NOT NULL,
              emoji      TEXT,
              sort_order INTEGER NOT NULL DEFAULT 1000,
              source     TEXT NOT NULL DEFAULT 'user',
              color      TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now')),
              updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(app_finance_burn_buckets)")}
        if "color" not in columns:
            con.execute("ALTER TABLE app_finance_burn_buckets ADD COLUMN color TEXT")
        if "emoji" not in columns:
            con.execute("ALTER TABLE app_finance_burn_buckets ADD COLUMN emoji TEXT")
        con.executemany(
            """
            INSERT OR IGNORE INTO app_finance_burn_buckets(
              bucket, label, emoji, sort_order, source, color, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (bucket, label, emoji, sort_order, source, color, updated_at)
                for bucket, label, emoji, sort_order, source, color in _DEFAULT_BURN_BUCKETS
            ],
        )
        con.executemany(
            """
            UPDATE app_finance_burn_buckets
               SET emoji=?, updated_at=?
             WHERE bucket=?
               AND COALESCE(emoji, '') = ''
            """,
            [
                (emoji, updated_at, bucket)
                for bucket, _label, emoji, _sort_order, _source, _color in _DEFAULT_BURN_BUCKETS
            ],
        )
        con.executemany(
            """
            UPDATE app_finance_burn_buckets
               SET color=?, updated_at=?
             WHERE bucket=?
               AND COALESCE(color, '') = ''
               AND ? <> ''
            """,
            [
                (color, updated_at, bucket, color)
                for bucket, _label, _emoji, _sort_order, _source, color in _DEFAULT_BURN_BUCKETS
            ],
        )
        con.commit()
    finally:
        con.close()


def _known_burn_buckets(ctx: AppContext) -> set[str]:
    _ensure_burn_bucket_metadata(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        rows = con.execute("SELECT bucket FROM app_finance_burn_buckets").fetchall()
    finally:
        con.close()
    return {*_BASE_BURN_BUCKETS, "exclude", *[str(row[0]) for row in rows]}


def _burn_rate_state(ctx: AppContext) -> dict[str, Any] | None:
    try:
        return ctx.module("models").burn_rate(ctx)
    except Exception:
        return None


def set_burn_classification(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Store a burn-rate override or create a reusable user rule."""
    bucket = _classification_bucket(ctx, payload)
    scope = _burn_scope(payload)
    transaction_id = _text(payload, "finance_transaction_id", required=scope == "transaction")
    merchant = _text(payload, "merchant")
    category = _text(payload, "source_category")
    updated_at = datetime.now(UTC).isoformat()
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        if scope == "transaction":
            con.execute(
                """
                INSERT INTO app_finance_burn_overrides(
                  finance_transaction_id, bucket, note, updated_at
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(finance_transaction_id) DO UPDATE SET
                  bucket=excluded.bucket,
                  note=excluded.note,
                  updated_at=excluded.updated_at
                """,
                (transaction_id, bucket, "inline burn-rate override", updated_at),
            )
        elif scope == "merchant":
            if not merchant:
                raise ValueError("merchant is required for merchant burn rules")
            pattern = merchant.lower()
            rule_key = _rule_key(scope, pattern)
            con.execute(
                """
                DELETE FROM app_finance_burn_rules
                 WHERE source='user'
                   AND LOWER(COALESCE(merchant_pattern, '')) = ?
                   AND rule_key <> ?
                """,
                (pattern, rule_key),
            )
            con.execute(
                """
                INSERT INTO app_finance_burn_rules(
                  rule_key, priority, label, bucket, merchant_pattern,
                  amount_direction, reason, source, updated_at
                )
                VALUES (?, 30, ?, ?, ?, 'positive', ?, 'user', ?)
                ON CONFLICT(rule_key) DO UPDATE SET
                  bucket=excluded.bucket,
                  label=excluded.label,
                  merchant_pattern=excluded.merchant_pattern,
                  amount_direction=excluded.amount_direction,
                  reason=excluded.reason,
                  enabled=1,
                  updated_at=excluded.updated_at
                """,
                (
                    rule_key,
                    f'Merchant contains "{merchant}"',
                    bucket,
                    pattern,
                    "user merchant rule" if bucket != "exclude" else "excluded by merchant rule",
                    updated_at,
                ),
            )
        else:
            if not category:
                raise ValueError("source_category is required for category burn rules")
            pattern = category.upper()
            rule_key = _rule_key(scope, pattern)
            con.execute(
                """
                DELETE FROM app_finance_burn_rules
                 WHERE source='user'
                   AND UPPER(COALESCE(category_pattern, '')) = ?
                   AND rule_key <> ?
                """,
                (pattern, rule_key),
            )
            con.execute(
                """
                INSERT INTO app_finance_burn_rules(
                  rule_key, priority, label, bucket, category_pattern,
                  category_match_type, amount_direction, reason, source, updated_at
                )
                VALUES (?, 30, ?, ?, ?, 'exact', 'positive', ?, 'user', ?)
                ON CONFLICT(rule_key) DO UPDATE SET
                  bucket=excluded.bucket,
                  label=excluded.label,
                  category_pattern=excluded.category_pattern,
                  category_match_type=excluded.category_match_type,
                  amount_direction=excluded.amount_direction,
                  reason=excluded.reason,
                  enabled=1,
                  updated_at=excluded.updated_at
                """,
                (
                    rule_key,
                    f'Source category is "{category}"',
                    bucket,
                    pattern,
                    "user category rule" if bucket != "exclude" else "excluded by category rule",
                    updated_at,
                ),
            )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "scope": scope, "bucket": bucket, "burn_rate": _burn_rate_state(ctx)}


def clear_burn_override(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    transaction_id = _text(payload, "finance_transaction_id", required=True)
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        con.execute(
            "DELETE FROM app_finance_burn_overrides WHERE finance_transaction_id=?",
            (transaction_id,),
        )
        con.commit()
    finally:
        con.close()
    return {"ok": True, "finance_transaction_id": transaction_id, "cleared": True}


def create_burn_bucket(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    label = _text(payload, "label", required=True)
    if len(label) > _MAX_BURN_BUCKET_LABEL_LEN:
        raise ValueError(f"bucket label must be {_MAX_BURN_BUCKET_LABEL_LEN} characters or fewer")
    bucket = _burn_bucket_slug(label)
    color = _burn_bucket_color(payload)
    emoji = _burn_bucket_emoji(payload)
    updated_at = datetime.now(UTC).isoformat()
    _ensure_burn_bucket_metadata(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        sort_order = con.execute(
            "SELECT COALESCE(MAX(sort_order), 100) + 10 FROM app_finance_burn_buckets"
        ).fetchone()[0]
        con.execute(
            """
            INSERT INTO app_finance_burn_buckets(bucket, label, emoji, sort_order, source, color, updated_at)
            VALUES (?, ?, ?, ?, 'user', ?, ?)
            ON CONFLICT(bucket) DO UPDATE SET
              label=excluded.label,
              emoji=excluded.emoji,
              color=excluded.color,
              updated_at=excluded.updated_at
            """,
            (bucket, label, emoji, sort_order, color, updated_at),
        )
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "bucket": bucket,
        "label": label,
        "emoji": emoji,
        "color": color,
        "burn_rate": _burn_rate_state(ctx),
    }


def set_burn_bucket_color(ctx: AppContext, payload: dict[str, Any]) -> dict[str, Any]:
    bucket = _burn_bucket(ctx, payload)
    label = _text(payload, "label") or bucket.replace("_", " ").title()
    if len(label) > _MAX_BURN_BUCKET_LABEL_LEN:
        raise ValueError(f"bucket label must be {_MAX_BURN_BUCKET_LABEL_LEN} characters or fewer")
    color = _burn_bucket_color(payload)
    emoji = _burn_bucket_emoji(payload) if "emoji" in payload else None
    updated_at = datetime.now(UTC).isoformat()
    _ensure_burn_bucket_metadata(ctx)
    con = connect(ctx.cfg.db_path)
    try:
        existing = con.execute(
            """
            SELECT COALESCE(sort_order, 1000), COALESCE(emoji, '')
            FROM app_finance_burn_buckets
            WHERE bucket=?
            """,
            (bucket,),
        ).fetchone()
        next_emoji = emoji if emoji is not None else (existing[1] if existing else "")
        con.execute(
            """
            INSERT INTO app_finance_burn_buckets(bucket, label, emoji, sort_order, source, color, updated_at)
            VALUES (?, ?, ?, ?, 'user', ?, ?)
            ON CONFLICT(bucket) DO UPDATE SET
              emoji=excluded.emoji,
              color=excluded.color,
              updated_at=excluded.updated_at
            """,
            (bucket, label, next_emoji, existing[0] if existing else 1000, color, updated_at),
        )
        con.commit()
    finally:
        con.close()
    return {
        "ok": True,
        "bucket": bucket,
        "label": label,
        "emoji": next_emoji,
        "color": color,
        "burn_rate": _burn_rate_state(ctx),
    }
