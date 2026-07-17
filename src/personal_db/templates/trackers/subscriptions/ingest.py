"""Derived subscription mart over finance categories and usage evidence."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from statistics import median
from typing import Any

from personal_db.db import connect
from personal_db.migrations import ensure_columns
from personal_db.tracker import Tracker

_AMOUNT_SERIES_MERCHANT_PATTERNS = ("apple",)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _read_rows(con: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = con.execute(sql, params)
    return [dict(row) for row in cur.fetchall()]


def _slug(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    text = re.sub(r"_+", "_", text)
    return text[:48] or "unknown"


def _merchant_key(value: str) -> str:
    text = re.sub(r"\b(inc|llc|ltd|co|corp|subscription|premium|monthly)\b", " ", value.lower())
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = " ".join(text.split())
    return text or value.lower().strip() or "unknown"


def _subscription_id(merchant_key: str) -> str:
    digest = hashlib.sha1(merchant_key.encode("utf-8")).hexdigest()[:12]
    return f"sub_{_slug(merchant_key)[:28]}_{digest}"


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "")
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _date_add(day: date, days: int) -> date:
    return day + timedelta(days=days)


def _coerce_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _ensure_schema_compat(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "subscription_entities"):
        return
    ensure_columns(
        con,
        "subscription_entities",
        {
            "series_key": "TEXT",
            "typical_amount": "REAL",
            "amount_min": "REAL",
            "amount_max": "REAL",
            "expected_day": "INTEGER",
            "monthly_avg_amount": "REAL",
        },
    )
    ensure_columns(con, "subscription_charges", {"series_key": "TEXT"})
    con.commit()


def _charge_source_query(con: sqlite3.Connection) -> str | None:
    if _table_exists(con, "finance_categorized_transactions"):
        return """
            SELECT finance_transaction_id, date, COALESCE(merchant_name, name) AS merchant,
                   amount, effective_category, category_source, source_category,
                   is_credit_card_payment, is_internal_transfer
            FROM finance_categorized_transactions
            WHERE pending = 0
              AND is_internal_transfer = 0
              AND is_credit_card_payment = 0
              AND amount > 0
        """
    return None


def _burn_overrides(con: sqlite3.Connection) -> dict[str, str]:
    if not _table_exists(con, "app_finance_burn_overrides"):
        return {}
    rows = _read_rows(
        con,
        """
        SELECT finance_transaction_id, bucket
        FROM app_finance_burn_overrides
        """,
    )
    return {str(row["finance_transaction_id"]): str(row["bucket"] or "") for row in rows}


def _burn_rules(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(con, "app_finance_burn_rules"):
        return []
    return _read_rows(
        con,
        """
        SELECT priority, label, bucket, merchant_pattern, category_pattern,
               category_match_type, flag_name, amount_direction, min_amount, reason
        FROM app_finance_burn_rules
        WHERE enabled = 1
        ORDER BY priority, rule_id
        """,
    )


def _category_matches(category: str, pattern: str, match_type: str) -> bool:
    category_upper = category.upper()
    pattern_upper = pattern.upper()
    if match_type == "exact":
        return category_upper == pattern_upper
    if match_type == "starts":
        return category_upper.startswith(pattern_upper)
    return pattern_upper in category_upper


def _burn_rule_matches(row: dict[str, Any], rule: dict[str, Any]) -> bool:
    amount = _coerce_float(row.get("amount"))
    merchant = str(row.get("merchant") or "")
    category = str(row.get("effective_category") or row.get("source_category") or "")
    direction = str(rule.get("amount_direction") or "any")
    if direction == "positive" and amount <= 0:
        return False
    if direction == "negative" and amount >= 0:
        return False
    min_amount = rule.get("min_amount")
    if min_amount is not None and amount < _coerce_float(min_amount):
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


def _subscription_charge_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    source_sql = _charge_source_query(con)
    if source_sql is None:
        return []
    rows = _read_rows(con, source_sql)
    overrides = _burn_overrides(con)
    burn_rules = _burn_rules(con)
    out: list[dict[str, Any]] = []
    for row in rows:
        effective = str(row.get("effective_category") or "").strip().lower()
        if effective == "subscriptions":
            row["match_reason"] = "finance effective_category=Subscriptions"
            out.append(row)
            continue
        override_bucket = overrides.get(str(row.get("finance_transaction_id") or ""))
        if override_bucket == "subscriptions":
            row["effective_category"] = row.get("effective_category") or "Subscriptions"
            row["category_source"] = "finance_burn_override"
            row["match_reason"] = "finance burn override=subscriptions"
            out.append(row)
            continue
        if override_bucket == "exclude":
            continue
        for rule in burn_rules:
            if not _burn_rule_matches(row, rule):
                continue
            if str(rule.get("bucket") or "") == "subscriptions":
                row["effective_category"] = row.get("effective_category") or "Subscriptions"
                row["category_source"] = "finance_burn_rule"
                row["match_reason"] = str(rule.get("reason") or rule.get("label") or "finance burn rule")
                out.append(row)
            break
    return out


def _monthly_average_amount(rows: list[dict[str, Any]]) -> float | None:
    by_month: dict[str, float] = defaultdict(float)
    for row in rows:
        parsed = _parse_date(row.get("date"))
        if parsed is None:
            continue
        by_month[f"{parsed.year:04d}-{parsed.month:02d}"] += _coerce_float(row.get("amount"))
    if not by_month:
        return None
    return round(sum(by_month.values()) / len(by_month), 2)


def _expected_day(dates: list[date]) -> int | None:
    if not dates:
        return None
    return round(median([day.day for day in dates]))


def _amount_signature(value: Any) -> str:
    return f"{_coerce_float(value):.2f}"


def _amount_cluster_key(row: dict[str, Any]) -> str:
    return f"amount:{_amount_signature(row.get('amount'))}"


def _should_split_by_amount(merchant_key: str, rows: list[dict[str, Any]]) -> bool:
    if not any(pattern in merchant_key for pattern in _AMOUNT_SERIES_MERCHANT_PATTERNS):
        return False
    if len(rows) < 6:
        return False
    by_amount: dict[str, int] = defaultdict(int)
    for row in rows:
        by_amount[_amount_cluster_key(row)] += 1
    repeated = [count for count in by_amount.values() if count >= 3]
    return len(repeated) >= 2


def _series_groups(merchant_key: str, rows: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    if not _should_split_by_amount(merchant_key, rows):
        return [("merchant", rows)]
    by_amount: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = _amount_cluster_key(row)
        by_amount[key].append(row)
    out = [
        (key, amount_rows)
        for key, amount_rows in sorted(by_amount.items())
        if len(amount_rows) >= 3
    ]
    return out or [("merchant", rows)]


def _series_label(base_label: str, series_key: str, rows: list[dict[str, Any]]) -> str:
    if series_key.startswith("amount:"):
        amount = _coerce_float(rows[-1].get("amount") if rows else series_key.removeprefix("amount:"))
        return f"{base_label} ${amount:,.2f}"
    if series_key == "mixed":
        return f"{base_label} mixed"
    return base_label


def _rules(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(con, "subscription_match_rules"):
        return []
    return _read_rows(
        con,
        """
        SELECT subscription_id, merchant_pattern, label, domain_pattern, app_pattern, bundle_id
        FROM subscription_match_rules
        WHERE enabled = 1
        ORDER BY rule_id
        """,
    )


def _rule_for_merchant(rules: list[dict[str, Any]], merchant: str) -> dict[str, Any] | None:
    lower = merchant.lower()
    for rule in rules:
        pattern = str(rule.get("merchant_pattern") or "").lower().strip()
        if pattern and pattern in lower:
            return rule
    return None


def _label_for(merchant: str, rule: dict[str, Any] | None) -> str:
    if rule and rule.get("label"):
        return str(rule["label"])
    return " ".join(str(merchant or "Unknown").split())[:80]


def _cadence(dates: list[date]) -> tuple[str, date | None]:
    if len(dates) <= 1:
        return "single", _date_add(dates[-1], 30) if dates else None
    gaps = [(b - a).days for a, b in pairwise(dates) if (b - a).days > 0]
    if not gaps:
        return "irregular", None
    med = median(gaps)
    if 25 <= med <= 35:
        return "monthly", _date_add(dates[-1], round(med))
    if 80 <= med <= 100:
        return "quarterly", _date_add(dates[-1], round(med))
    if 330 <= med <= 400:
        return "annual", _date_add(dates[-1], round(med))
    return "irregular", _date_add(dates[-1], round(med))


def _status(last_charge: date | None, cadence: str) -> str:
    if last_charge is None:
        return "unknown"
    age = (datetime.now().astimezone().date() - last_charge).days
    if cadence == "annual":
        return "active" if age <= 430 else "stale"
    if cadence == "quarterly":
        return "active" if age <= 130 else "stale"
    return "active" if age <= 65 else "stale"


def _clear_materialized(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM subscription_utilization_periods")
    con.execute("DELETE FROM subscription_usage_evidence")
    con.execute("DELETE FROM subscription_charges")
    con.execute("DELETE FROM subscription_entities")
    con.commit()


def _materialize_entities_and_charges(
    con: sqlite3.Connection,
    rules: list[dict[str, Any]],
    now: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    charges = _subscription_charge_rows(con)
    by_merchant: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    merchant_rules: dict[tuple[str, str], dict[str, Any] | None] = {}
    for charge in charges:
        merchant = str(charge.get("merchant") or "Unknown").strip() or "Unknown"
        rule = _rule_for_merchant(rules, merchant)
        base_label = _label_for(merchant, rule)
        merchant_key = _merchant_key(str(rule.get("label") or merchant) if rule else merchant)
        charge["merchant_key"] = merchant_key
        charge["base_label"] = base_label
        key = (merchant_key, str(rule.get("subscription_id") or "") if rule and rule.get("subscription_id") else "")
        by_merchant[key].append(charge)
        merchant_rules[key] = rule

    grouped: dict[str, list[dict[str, Any]]] = {}
    group_rules: dict[str, dict[str, Any] | None] = {}
    for key, merchant_rows in by_merchant.items():
        merchant_rows.sort(key=lambda row: str(row.get("date") or ""))
        merchant_key, pinned_subscription_id = key
        rule = merchant_rules.get(key)
        for series_key, series_rows in _series_groups(merchant_key, merchant_rows):
            if pinned_subscription_id and series_key == "merchant":
                sub_id = pinned_subscription_id
            else:
                sub_id = _subscription_id(
                    merchant_key if series_key == "merchant" else f"{merchant_key}:{series_key}"
                )
            for charge in series_rows:
                charge["subscription_id"] = sub_id
                charge["series_key"] = series_key
            grouped[sub_id] = series_rows
            group_rules[sub_id] = rule

    entities: list[dict[str, Any]] = []
    charge_rows: list[dict[str, Any]] = []
    entity_map: dict[str, dict[str, Any]] = {}
    for sub_id, rows in grouped.items():
        rows.sort(key=lambda row: str(row.get("date") or ""))
        dates = [d for d in (_parse_date(row.get("date")) for row in rows) if d is not None]
        amounts = [_coerce_float(row.get("amount")) for row in rows]
        rule = group_rules.get(sub_id)
        series_key = str(rows[-1].get("series_key") or "merchant")
        base_label = str(rows[-1].get("base_label") or _label_for(str(rows[-1].get("merchant") or "Unknown"), rule))
        label = _series_label(base_label, series_key, rows)
        cadence, next_date = _cadence(dates)
        last_charge = dates[-1] if dates else None
        entity = {
            "subscription_id": sub_id,
            "label": label,
            "merchant_key": str(rows[-1].get("merchant_key") or ""),
            "series_key": series_key,
            "typical_amount": round(median(amounts), 2) if amounts else None,
            "amount_min": round(min(amounts), 2) if amounts else None,
            "amount_max": round(max(amounts), 2) if amounts else None,
            "expected_day": _expected_day(dates),
            "first_charge_date": dates[0].isoformat() if dates else None,
            "last_charge_date": last_charge.isoformat() if last_charge else None,
            "charge_count": len(rows),
            "avg_amount": round(sum(amounts) / len(amounts), 2) if amounts else None,
            "monthly_avg_amount": _monthly_average_amount(rows),
            "latest_amount": amounts[-1] if amounts else None,
            "cadence": cadence,
            "next_expected_date": next_date.isoformat() if next_date else None,
            "status": _status(last_charge, cadence),
            "confidence": 0.9 if rule else min(0.75, 0.35 + (0.12 * len(rows))),
            "source_flags_json": json.dumps(
                {
                    "category": "Subscriptions",
                    "rule_label": rule.get("label") if rule else None,
                    "charge_count": len(rows),
                    "series_key": series_key,
                },
                separators=(",", ":"),
            ),
            "updated_at": now,
        }
        entities.append(entity)
        entity_map[sub_id] = entity
        for row in rows:
            charge_rows.append(
                {
                    "finance_transaction_id": row["finance_transaction_id"],
                    "subscription_id": sub_id,
                    "date": row["date"],
                    "merchant": row.get("merchant"),
                    "amount": row.get("amount"),
                    "series_key": row.get("series_key"),
                    "effective_category": row.get("effective_category"),
                    "category_source": row.get("category_source"),
                    "match_reason": row.get("match_reason") or "finance subscription category",
                }
            )
    return entities, charge_rows, entity_map


def _rule_matches_usage(rule: dict[str, Any] | None, row: dict[str, Any]) -> tuple[bool, str, float]:
    if not rule:
        return False, "", 0.0
    bundle = str(rule.get("bundle_id") or "").lower()
    if bundle and bundle == str(row.get("bundle_id") or "").lower():
        return True, "bundle_id", 0.95
    domain = str(rule.get("domain_pattern") or "").lower()
    row_domain = str(row.get("domain") or "").lower()
    if domain and row_domain and (row_domain == domain or row_domain.endswith("." + domain) or domain in row_domain):
        return True, "domain", 0.9
    app = str(rule.get("app_pattern") or "").lower()
    row_app = str(row.get("app_name") or "").lower()
    if app and row_app and app in row_app:
        return True, "app", 0.8
    return False, "", 0.0


def _usage_evidence(
    con: sqlite3.Connection,
    entities: dict[str, dict[str, Any]],
    rules: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not entities:
        return []
    rule_by_sub: dict[str, dict[str, Any] | None] = {}
    for sub_id, entity in entities.items():
        rule_by_sub[sub_id] = _rule_for_merchant(rules, str(entity.get("label") or ""))
        if rule_by_sub[sub_id] is None:
            merchant = str(entity.get("merchant_key") or "")
            rule_by_sub[sub_id] = _rule_for_merchant(rules, merchant)

    out: list[dict[str, Any]] = []
    if _table_exists(con, "screen_time_app_usage"):
        has_names = _table_exists(con, "screen_time_app_names")
        join_names = "LEFT JOIN screen_time_app_names n ON n.bundle_id = s.bundle_id" if has_names else ""
        app_expr = "n.app_name" if has_names else "NULL"
        for row in _read_rows(
            con,
            f"""
            SELECT 'screen_time:' || s.id AS source_id, s.start_at, s.end_at,
                   s.seconds / 60.0 AS minutes, {app_expr} AS app_name,
                   s.bundle_id, NULL AS domain, NULL AS title
            FROM screen_time_app_usage s
            {join_names}
            """,
        ):
            for sub_id, rule in rule_by_sub.items():
                matched, reason, confidence = _rule_matches_usage(rule, row)
                if matched:
                    out.append(_evidence_row(sub_id, "screen_time", row, confidence, reason))
    if _table_exists(con, "mosspath_lite_events"):
        for row in _read_rows(
            con,
            """
            SELECT 'mosspath_lite:' || id AS source_id, timestamp AS start_at,
                   timestamp AS end_at, 0 AS minutes, app_name, bundle_id,
                   browser_domain AS domain, COALESCE(browser_title, window_title) AS title
            FROM mosspath_lite_events
            """,
        ):
            for sub_id, rule in rule_by_sub.items():
                matched, reason, confidence = _rule_matches_usage(rule, row)
                if matched:
                    out.append(_evidence_row(sub_id, "mosspath_lite", row, confidence, reason))
    if _table_exists(con, "chrome_visits"):
        for row in _read_rows(
            con,
            """
            SELECT 'chrome_history:' || profile || ':' || visit_id AS source_id,
                   visited_at AS start_at, visited_at AS end_at,
                   COALESCE(duration_seconds, 0) / 60.0 AS minutes,
                   'Chrome' AS app_name, 'com.google.Chrome' AS bundle_id,
                   domain, title
            FROM chrome_visits
            """,
        ):
            for sub_id, rule in rule_by_sub.items():
                matched, reason, confidence = _rule_matches_usage(rule, row)
                if matched:
                    out.append(_evidence_row(sub_id, "chrome_history", row, confidence, reason))
    return out


def _evidence_row(
    sub_id: str,
    source: str,
    row: dict[str, Any],
    confidence: float,
    reason: str,
) -> dict[str, Any]:
    evidence_id = f"{sub_id}:{source}:{row['source_id']}"
    return {
        "evidence_id": evidence_id,
        "subscription_id": sub_id,
        "source": source,
        "source_id": row["source_id"],
        "started_at": row["start_at"],
        "ended_at": row.get("end_at"),
        "minutes": round(_coerce_float(row.get("minutes")), 4),
        "event_count": 1,
        "app_name": row.get("app_name"),
        "bundle_id": row.get("bundle_id"),
        "domain": row.get("domain"),
        "title": row.get("title"),
        "confidence": confidence,
        "reason": reason,
    }


def _period_end(start: date, next_start: date | None, cadence: str) -> date:
    if next_start and next_start > start:
        return next_start
    if cadence == "annual":
        return _date_add(start, 365)
    if cadence == "quarterly":
        return _date_add(start, 91)
    return _date_add(start, 31)


def _utilization_label(minutes: float, coverage: float) -> str:
    if coverage < 0.35:
        return "unknown"
    if minutes <= 0:
        return "no_observed_usage"
    hours = minutes / 60.0
    if hours >= 8:
        return "high"
    if hours >= 1:
        return "medium"
    return "low"


def _period_rows(
    entities: dict[str, dict[str, Any]],
    charges: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    now: str,
) -> list[dict[str, Any]]:
    by_sub_charge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_sub_evidence: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in charges:
        by_sub_charge[row["subscription_id"]].append(row)
    for row in evidence:
        by_sub_evidence[row["subscription_id"]].append(row)

    today = datetime.now().astimezone().date()
    rows: list[dict[str, Any]] = []
    for sub_id, entity in entities.items():
        cadence = str(entity.get("cadence") or "monthly")
        sub_charges = sorted(by_sub_charge.get(sub_id, []), key=lambda row: str(row.get("date") or ""))
        for idx, charge in enumerate(sub_charges):
            start = _parse_date(charge.get("date"))
            if start is None:
                continue
            next_start = _parse_date(sub_charges[idx + 1].get("date")) if idx + 1 < len(sub_charges) else None
            end = _period_end(start, next_start, cadence)
            ev_rows = [
                ev for ev in by_sub_evidence.get(sub_id, [])
                if _evidence_in_period(ev, start, end)
            ]
            minutes = sum(_coerce_float(ev.get("minutes")) for ev in ev_rows)
            active_days = len({_parse_dt(ev.get("started_at")).astimezone().date() for ev in ev_rows if _parse_dt(ev.get("started_at"))})
            event_count = sum(int(ev.get("event_count") or 0) for ev in ev_rows)
            cost = _coerce_float(charge.get("amount"))
            elapsed_end = min(today, end)
            coverage = max(0.0, min(1.0, (elapsed_end - start).days / max(1, (end - start).days)))
            top_evidence = sorted(ev_rows, key=lambda ev: (_coerce_float(ev.get("minutes")), str(ev.get("started_at") or "")), reverse=True)[:6]
            rows.append(
                {
                    "period_id": f"{sub_id}:{start.isoformat()}",
                    "subscription_id": sub_id,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "cost": cost,
                    "charge_count": 1,
                    "usage_minutes": round(minutes, 2),
                    "active_days": active_days,
                    "event_count": event_count,
                    "cost_per_hour": round(cost / (minutes / 60.0), 2) if minutes > 0 else None,
                    "cost_per_active_day": round(cost / active_days, 2) if active_days > 0 else None,
                    "coverage_ratio": round(coverage, 3),
                    "utilization_label": _utilization_label(minutes, coverage),
                    "evidence_json": json.dumps(
                        [
                            {
                                "source": ev.get("source"),
                                "app": ev.get("app_name"),
                                "domain": ev.get("domain"),
                                "minutes": ev.get("minutes"),
                            }
                            for ev in top_evidence
                        ],
                        separators=(",", ":"),
                    ),
                    "computed_at": now,
                }
            )
    return rows


def _evidence_in_period(row: dict[str, Any], start: date, end: date) -> bool:
    dt = _parse_dt(row.get("started_at"))
    if dt is None:
        return False
    day = dt.astimezone().date()
    return start <= day < end


def sync(t: Tracker) -> None:
    now = _now_iso()
    con = connect(t.cfg.db_path)
    con.row_factory = sqlite3.Row
    try:
        _ensure_schema_compat(con)
        _clear_materialized(con)
        rules = _rules(con)
        entities, charges, entity_map = _materialize_entities_and_charges(con, rules, now)
        evidence = _usage_evidence(con, entity_map, rules)
        periods = _period_rows(entity_map, charges, evidence, now)
    finally:
        con.close()

    t.upsert("subscription_entities", entities, key=["subscription_id"])
    t.upsert("subscription_charges", charges, key=["finance_transaction_id"])
    t.upsert("subscription_usage_evidence", evidence, key=["evidence_id"])
    t.upsert("subscription_utilization_periods", periods, key=["period_id"])
    t.cursor.set(now)
    t.log.info(
        "subscriptions: %d entities, %d charges, %d evidence rows, %d periods",
        len(entities),
        len(charges),
        len(evidence),
        len(periods),
    )


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    sync(t)
