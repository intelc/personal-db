from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.db import connect

_BURN_RATE_EVIDENCE_DAYS = 90
_BURN_RATE_SMOOTHING_DAYS = 180
_BURN_RATE_MONTHLY_BUCKETS = {"rent", "subscriptions"}
_DEFAULT_BURN_BUCKETS = [
    ("rent", "Rent", "🏠", 10, "system", ""),
    ("food", "Food", "🍽️", 20, "system", ""),
    ("transportation", "Transportation", "🚕", 30, "system", ""),
    ("ai", "AI spending", "🤖", 40, "system", ""),
    ("health", "Health", "🩺", 50, "system", ""),
    ("subscriptions", "Other subscriptions", "🔁", 60, "system", ""),
    ("other", "Other", "📦", 800, "system", ""),
    ("wasted", "Wasted", "🗑️", 900, "user", "red"),
]


def _q(ctx: AppContext, name: str, **params: Any) -> list[dict[str, Any]]:
    try:
        return ctx.query(name, **params)
    except Exception:
        return []


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any, *, cents: bool = False) -> str:
    v = _float(value)
    sign = "-" if v < 0 else ""
    precision = 2 if cents else 0
    return f"{sign}${abs(v):,.{precision}f}"


def _display_label(label: str, emoji: str) -> str:
    return f"{emoji} {label}" if emoji else label


def ensure_burn_bucket_metadata(ctx: AppContext) -> None:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    con = connect(ctx.cfg.db_path)
    try:
        columns = {str(row[1]) for row in con.execute("PRAGMA table_info(app_finance_burn_buckets)")}
        if "color" not in columns:
            con.execute("ALTER TABLE app_finance_burn_buckets ADD COLUMN color TEXT")
        if "emoji" not in columns:
            con.execute("ALTER TABLE app_finance_burn_buckets ADD COLUMN emoji TEXT")
        con.executemany(
            """
            INSERT OR IGNORE INTO app_finance_burn_buckets(
              bucket, label, emoji, sort_order, source, color
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            _DEFAULT_BURN_BUCKETS,
        )
        con.executemany(
            """
            UPDATE app_finance_burn_buckets
               SET emoji=?
             WHERE bucket=?
               AND COALESCE(emoji, '') = ''
            """,
            [(emoji, bucket) for bucket, _label, emoji, _sort, _source, _color in _DEFAULT_BURN_BUCKETS],
        )
        con.execute(
            """
            UPDATE app_finance_burn_buckets
               SET color='red'
             WHERE bucket='wasted'
               AND COALESCE(color, '') = ''
            """
        )
        con.commit()
    finally:
        con.close()


def burn_buckets(ctx: AppContext) -> list[dict[str, Any]]:
    ensure_burn_bucket_metadata(ctx)
    return [
        {
            "bucket": str(row.get("bucket") or ""),
            "label": str(row.get("label") or ""),
            "emoji": str(row.get("emoji") or ""),
            "display_label": _display_label(str(row.get("label") or ""), str(row.get("emoji") or "")),
            "sort_order": int(row.get("sort_order") or 1000),
            "source": str(row.get("source") or "user"),
            "color": str(row.get("color") or ""),
        }
        for row in _q(ctx, "burn_buckets")
        if row.get("bucket") and row.get("label")
    ]


def burn_rules(ctx: AppContext) -> list[dict[str, Any]]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return _q(ctx, "burn_rules")


def burn_overrides(ctx: AppContext) -> dict[str, dict[str, Any]]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return {str(row["finance_transaction_id"]): row for row in _q(ctx, "burn_overrides")}


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _category_matches(category: str, pattern: str, match_type: str) -> bool:
    category_upper = category.upper()
    pattern_upper = pattern.upper()
    if match_type == "exact":
        return category_upper == pattern_upper
    if match_type == "starts":
        return category_upper.startswith(pattern_upper)
    return pattern_upper in category_upper


def _rule_matches(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    amount = _float(row.get("amount"))
    merchant = str(row.get("merchant") or "")
    category = str(row.get("category") or "")
    direction = str(rule.get("amount_direction") or "any")
    if direction == "positive" and amount <= 0:
        return False
    if direction == "negative" and amount >= 0:
        return False
    min_amount = rule.get("min_amount")
    if min_amount is not None and amount < _float(min_amount):
        return False
    flag_name = str(rule.get("flag_name") or "")
    if flag_name and not int(row.get(flag_name) or 0):
        return False
    merchant_pattern = str(rule.get("merchant_pattern") or "").lower().strip()
    if merchant_pattern and merchant_pattern not in merchant.lower():
        return False
    category_pattern = str(rule.get("category_pattern") or "").strip()
    if category_pattern and not _category_matches(
        category, category_pattern, str(rule.get("category_match_type") or "contains")
    ):
        return False
    return bool(flag_name or merchant_pattern or category_pattern)


def classify_burn_row(
    row: dict[str, Any],
    rules: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> tuple[str, float, str] | None:
    amount = _float(row.get("amount"))
    transaction_id = str(row.get("finance_transaction_id") or "")
    override = overrides.get(transaction_id)
    if override:
        bucket = str(override.get("bucket") or "")
        if bucket == "exclude":
            return None
        return (bucket, amount, "transaction override")
    for rule in rules:
        if not _rule_matches(row, rule):
            continue
        bucket = str(rule.get("bucket") or "")
        if bucket == "exclude":
            return None
        return (bucket, amount, str(rule.get("reason") or rule.get("label") or "burn rule"))
    if amount <= 0:
        return None
    return ("other", amount, "fallback")


def classified_burn_rows(
    rows: list[dict[str, Any]],
    rules: list[dict[str, Any]],
    overrides: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    classified_rows = []
    for row in rows:
        classified = classify_burn_row(row, rules, overrides)
        if classified is None:
            continue
        bucket, amount, reason = classified
        classified_rows.append(
            {
                "bucket": bucket,
                "finance_transaction_id": row.get("finance_transaction_id") or "",
                "date": row.get("date") or "",
                "merchant": row.get("merchant") or "",
                "amount": amount,
                "amount_display": _money(amount, cents=True),
                "category": row.get("category") or "",
                "reason": reason,
            }
        )
    return classified_rows


def _is_complete_month(month_key: str, today: date) -> bool:
    year_text, month_text = month_key.split("-", 1)
    year = int(year_text)
    month = int(month_text)
    if (year, month) < (today.year, today.month):
        return True
    return (year, month) == (today.year, today.month) and today.day >= 25


def smoothed_monthly_burn(bucket: str, rows: list[dict[str, Any]], today: date) -> float:
    bucket_rows = [row for row in rows if row["bucket"] == bucket]
    if not bucket_rows:
        return 0.0
    if bucket in _BURN_RATE_MONTHLY_BUCKETS:
        by_month: dict[str, float] = {}
        payment_by_month: dict[str, float] = {}
        for row in bucket_rows:
            parsed = _parse_date(row["date"])
            if parsed is None:
                continue
            month_key = f"{parsed.year:04d}-{parsed.month:02d}"
            amount = _float(row["amount"])
            by_month[month_key] = by_month.get(month_key, 0.0) + amount
            if bucket == "rent" and amount > 0 and row.get("reason") == "rent payment":
                payment_by_month[month_key] = payment_by_month.get(month_key, 0.0) + amount
        complete_months = [
            month for month in sorted(by_month) if _is_complete_month(month, today)
        ]
        if bucket == "rent":
            complete_months = [
                month for month in complete_months if payment_by_month.get(month, 0.0) >= 1000
            ]
        selected_months = (complete_months or sorted(by_month))[-4:]
        if not selected_months:
            return 0.0
        return sum(by_month[month] for month in selected_months) / len(selected_months)

    cutoff = today - timedelta(days=_BURN_RATE_EVIDENCE_DAYS)
    recent_total = 0.0
    long_total = 0.0
    for row in bucket_rows:
        amount = _float(row["amount"])
        parsed = _parse_date(row["date"])
        long_total += amount
        if parsed is not None and parsed >= cutoff:
            recent_total += amount
    recent_monthly = recent_total * 30.0 / _BURN_RATE_EVIDENCE_DAYS
    long_monthly = long_total * 30.0 / _BURN_RATE_SMOOTHING_DAYS
    return recent_monthly * 0.7 + long_monthly * 0.3


def burn_rate(ctx: AppContext, _params: dict[str, Any] | None = None) -> dict[str, Any]:
    buckets = burn_buckets(ctx)
    rules = burn_rules(ctx)
    overrides = burn_overrides(ctx)
    smoothing_rows = classified_burn_rows(
        _q(ctx, "burn_rate_transactions", days=_BURN_RATE_SMOOTHING_DAYS), rules, overrides
    )
    evidence_rows = classified_burn_rows(
        _q(ctx, "burn_rate_transactions", days=_BURN_RATE_EVIDENCE_DAYS), rules, overrides
    )
    today = date.today()
    by_bucket = {
        row["bucket"]: {
            **row,
            "monthly": smoothed_monthly_burn(str(row["bucket"]), smoothing_rows, today),
            "monthly_display": _money(smoothed_monthly_burn(str(row["bucket"]), smoothing_rows, today)),
            "count": 0,
        }
        for row in buckets
    }
    for row in evidence_rows:
        bucket = str(row["bucket"])
        if bucket not in by_bucket:
            label = bucket.replace("_", " ").title()
            monthly = smoothed_monthly_burn(bucket, smoothing_rows, today)
            by_bucket[bucket] = {
                "bucket": bucket,
                "label": label,
                "emoji": "",
                "display_label": label,
                "sort_order": 790,
                "source": "derived",
                "color": "",
                "monthly": monthly,
                "monthly_display": _money(monthly),
                "count": 0,
            }
        by_bucket[bucket]["count"] = int(by_bucket[bucket]["count"]) + 1
    ordered = sorted(by_bucket.values(), key=lambda item: (int(item.get("sort_order") or 1000), str(item.get("label") or "")))
    return {
        "evidence_days": _BURN_RATE_EVIDENCE_DAYS,
        "smoothing_days": _BURN_RATE_SMOOTHING_DAYS,
        "buckets": ordered,
        "bucket_counts": {str(item["bucket"]): int(item["count"]) for item in ordered},
        "bucket_monthly": {str(item["bucket"]): _float(item["monthly"]) for item in ordered},
        "rows": evidence_rows,
    }
