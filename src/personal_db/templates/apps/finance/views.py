from __future__ import annotations

import html
import sqlite3
from datetime import date, timedelta
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.db import connect
from personal_db.enrichments.core import apply_enrichment_schema
from personal_db.enrichments.finance import DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS
from personal_db.migrations import ensure_columns
from personal_db.ui import agcharts, aggrid
from personal_db.ui import components as c


def _q(ctx: AppContext, name: str, **params: Any) -> list[dict[str, Any]]:
    try:
        return ctx.query(name, **params)
    except sqlite3.Error:
        return []


def _money(value: Any, *, cents: bool = False) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0
    sign = "-" if v < 0 else ""
    precision = 2 if cents else 0
    return f"{sign}${abs(v):,.{precision}f}"


def _signed_money(value: Any) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0
    if v > 0:
        return f"+${v:,.0f}"
    if v < 0:
        return f"-${abs(v):,.0f}"
    return "$0"


def _float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _compact_time(value: Any) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text.split(".", 1)[0]


def _nav(ctx: AppContext, active: str) -> list[tuple[str, str, bool] | tuple[str, str, bool, dict[str, str]]]:
    items: list[tuple[str, str, bool] | tuple[str, str, bool, dict[str, str]]] = []
    for page in ctx.manifest.pages:
        attrs = {"data-finance-parent-tab": "1"} if page.slug == "parents" else {}
        if attrs:
            items.append((page.title, f"/a/{ctx.manifest.name}/{page.slug}", page.slug == active, attrs))
        else:
            items.append((page.title, f"/a/{ctx.manifest.name}/{page.slug}", page.slug == active))
    return items


def _finance_controls() -> str:
    return (
        '<div class="finance-app-controls" data-finance-page>'
        '<label class="finance-toggle">'
        '<input type="checkbox" data-finance-self-only checked> '
        "<span>Only show self</span>"
        "</label>"
        "</div>"
    )


def _finance_page(ctx: AppContext, active: str, title: str, *children: str, subtitle: str = "") -> str:
    return c.page(
        title,
        *children,
        subtitle=subtitle,
        header_extra=_finance_controls(),
        nav=_nav(ctx, active),
    )


def _review_map(ctx: AppContext) -> dict[str, dict[str, Any]]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return {str(row["review_key"]): row for row in _q(ctx, "review_states")}


def _review_key_for_recurring(row: dict[str, Any]) -> str:
    merchant = str(row.get("merchant") or "").strip().lower()
    owner = str(row.get("owner") or "").strip().lower()
    category = str(row.get("category") or "").strip().lower()
    return f"recurring:{owner}:{merchant}:{category}"


def _review_status_cell(state: dict[str, Any] | None) -> str:
    if not state:
        return "Needs review"
    status = str(state.get("status") or "reviewed")
    note = str(state.get("note") or "")
    label = "Ignored" if status == "ignored" else "Reviewed"
    if note:
        return f"{label} - {note}"
    return label


def _review_controls(
    ctx: AppContext, review_key: str, kind: str, state: dict[str, Any] | None
) -> str:
    escaped_key = html.escape(review_key, quote=True)
    escaped_kind = html.escape(kind, quote=True)
    action = html.escape(ctx.action_url("mark_reviewed"), quote=True)
    clear = html.escape(ctx.action_url("clear_review"), quote=True)
    reviewed = (
        f'<form class="review-action" method="post" action="{action}">'
        f'<input type="hidden" name="review_key" value="{escaped_key}">'
        f'<input type="hidden" name="kind" value="{escaped_kind}">'
        '<input type="hidden" name="status" value="reviewed">'
        '<button type="submit">reviewed</button></form>'
    )
    ignored = (
        f'<form class="review-action" method="post" action="{action}">'
        f'<input type="hidden" name="review_key" value="{escaped_key}">'
        f'<input type="hidden" name="kind" value="{escaped_kind}">'
        '<input type="hidden" name="status" value="ignored">'
        '<button type="submit">ignore</button></form>'
    )
    clear_form = ""
    if state:
        clear_form = (
            f'<form class="review-action" method="post" action="{clear}">'
            f'<input type="hidden" name="review_key" value="{escaped_key}">'
            '<button type="submit">clear</button></form>'
        )
    return f'<div class="review-actions">{reviewed}{ignored}{clear_form}</div>'


def _category_map(ctx: AppContext) -> dict[str, dict[str, Any]]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return {
        str(row["finance_transaction_id"]): row for row in _q(ctx, "transaction_category_states")
    }


def _category_presets(ctx: AppContext) -> list[str]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return [str(row["category"]) for row in _q(ctx, "category_presets") if row.get("category")]


def _category_datalist(categories: list[str]) -> str:
    options = "".join(
        f'<option value="{html.escape(category, quote=True)}"></option>' for category in categories
    )
    return f'<datalist id="finance-category-presets">{options}</datalist>'


def _category_controls(ctx: AppContext, transaction_id: str, state: dict[str, Any] | None) -> str:
    escaped_id = html.escape(transaction_id, quote=True)
    set_action = html.escape(ctx.action_url("set_transaction_category"), quote=True)
    clear_action = html.escape(ctx.action_url("clear_transaction_category"), quote=True)
    current = html.escape(str(state.get("category") or "") if state else "", quote=True)
    set_form = (
        f'<form class="category-action" method="post" action="{set_action}">'
        f'<input type="hidden" name="finance_transaction_id" value="{escaped_id}">'
        f'<input type="text" name="category" value="{current}" '
        'list="finance-category-presets" placeholder="category">'
        '<button type="submit">save</button></form>'
    )
    clear_form = ""
    if state:
        clear_form = (
            f'<form class="category-action" method="post" action="{clear_action}">'
            f'<input type="hidden" name="finance_transaction_id" value="{escaped_id}">'
            '<button type="submit">clear</button></form>'
        )
    return f'<div class="category-actions">{set_form}{clear_form}</div>'


def _scope_owner(scope: str) -> str:
    return "parents" if scope == "parents" else "self"


def _account_summary(ctx: AppContext, scope: str) -> dict[str, float]:
    rows = _q(ctx, "account_rows", scope=scope)
    out = {
        "accounts": float(len(rows)),
        "cash": 0.0,
        "investments": 0.0,
        "credit_card_debt": 0.0,
        "other": 0.0,
        "assets": 0.0,
        "debts": 0.0,
        "net_worth": 0.0,
    }
    for row in rows:
        group = row.get("account_group")
        balance = _float(row.get("current_balance"))
        if group == "credit_card":
            debt = abs(balance)
            out["credit_card_debt"] += debt
            out["debts"] += debt
            out["net_worth"] -= debt
            continue
        if group == "cash":
            out["cash"] += balance
        elif group == "investments":
            out["investments"] += balance
        else:
            out["other"] += balance
        if balance >= 0:
            out["assets"] += balance
        else:
            out["debts"] += abs(balance)
        out["net_worth"] += balance
    return out


def _latest_metrics(ctx: AppContext, scope: str) -> tuple[list[tuple[str, str, str, bool]], str]:
    owner = _scope_owner(scope)
    latest = _q(ctx, "latest_net_worth", owner=owner)
    if latest:
        row = latest[0]
        return [
            (
                "Net worth" if scope == "self" else "Managed total",
                _money(row["net_worth"]),
                f"as of {row['date']}",
                True,
            ),
            ("Assets", _money(row["assets"]), "", True),
            ("Debts", _money(row["debts"]), "", True),
            ("Cash", _money(row["cash"]), "", True),
            ("Investments", _money(row["investments"]), "", True),
            ("Credit cards", _money(-_float(row["credit_card_debt"])), "", True),
        ], str(row["date"])
    summary = _account_summary(ctx, scope)
    return [
        (
            "Net worth" if scope == "self" else "Managed total",
            _money(summary["net_worth"]),
            "from latest accounts",
            True,
        ),
        ("Assets", _money(summary["assets"]), "", True),
        ("Debts", _money(summary["debts"]), "", True),
        ("Cash", _money(summary["cash"]), "", True),
        ("Investments", _money(summary["investments"]), "", True),
        # Account count, not a currency amount -- not sensitive.
        ("Accounts", str(int(summary["accounts"])), "", False),
    ], ""


def _account_table(ctx: AppContext, scope: str, *, investments: bool = False) -> str:
    rows = []
    for row in _q(ctx, "account_rows", scope=scope):
        group = row.get("account_group")
        if investments and group != "investments":
            continue
        if not investments and group == "investments":
            continue
        rows.append(
            (
                row.get("institution_name") or "",
                row.get("account_name") or "",
                row.get("source") or "",
                row.get("account_group") or "",
                row.get("subtype") or "",
                _money(row.get("current_balance"), cents=True),
                row.get("iso_currency_code") or "",
                _compact_time(row.get("as_of")),
            )
        )
    return c.data_grid(
        rows,
        ["Institution", "Account", "Source", "Group", "Subtype", "Current", "Currency", "As Of"],
        class_name="finance-grid finance-accounts-table",
        page_size=20,
    )


_BURN_RATE_EVIDENCE_DAYS = 90
_BURN_RATE_SMOOTHING_DAYS = 180
_BURN_RATE_MONTHLY_BUCKETS = {"rent", "subscriptions"}
_BASE_BURN_BUCKETS = [
    ("rent", "Rent", "🏠"),
    ("food", "Food", "🍽️"),
    ("transportation", "Transportation", "🚕"),
    ("ai", "AI spending", "🤖"),
    ("health", "Health", "🩺"),
    ("entertainment", "Entertainment", "🎬"),
    ("subscriptions", "Other subscriptions", "🔁"),
]
_OTHER_BURN_BUCKET = ("other", "Other", "📦")
_DEFAULT_BURN_BUCKETS = [*_BASE_BURN_BUCKETS, _OTHER_BURN_BUCKET, ("wasted", "Wasted", "🗑️")]
_BURN_BUCKET_LABELS = {key: label for key, label, _emoji in _DEFAULT_BURN_BUCKETS}
_BURN_BUCKET_COLORS = [
    ("", "None"),
    ("red", "Red"),
    ("orange", "Orange"),
    ("yellow", "Yellow"),
    ("green", "Green"),
    ("blue", "Blue"),
    ("purple", "Purple"),
    ("pink", "Pink"),
]


def _ensure_burn_bucket_metadata(ctx: AppContext) -> None:
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
        ensure_columns(con, "app_finance_burn_buckets", {"color": "TEXT", "emoji": "TEXT"})
        con.executemany(
            """
            INSERT OR IGNORE INTO app_finance_burn_buckets(
              bucket, label, emoji, sort_order, source, color
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                ("rent", "Rent", "🏠", 10, "system", ""),
                ("food", "Food", "🍽️", 20, "system", ""),
                ("transportation", "Transportation", "🚕", 30, "system", ""),
                ("ai", "AI spending", "🤖", 40, "system", ""),
                ("health", "Health", "🩺", 50, "system", ""),
                ("entertainment", "Entertainment", "🎬", 55, "system", ""),
                ("subscriptions", "Other subscriptions", "🔁", 60, "system", ""),
                ("other", "Other", "📦", 800, "system", ""),
                ("wasted", "Wasted", "🗑️", 900, "user", "red"),
            ],
        )
        con.executemany(
            """
            UPDATE app_finance_burn_buckets
               SET emoji=?
             WHERE bucket=?
               AND COALESCE(emoji, '') = ''
            """,
            [(emoji, key) for key, _label, emoji in _DEFAULT_BURN_BUCKETS],
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


def _burn_buckets(ctx: AppContext) -> list[tuple[str, str, str, str]]:
    _ensure_burn_bucket_metadata(ctx)
    bucket_rows = _q(ctx, "burn_buckets")
    metadata = {
        str(row["bucket"]): {
            "label": str(row["label"]),
            "emoji": str(row.get("emoji") or ""),
            "color": str(row.get("color") or ""),
        }
        for row in bucket_rows
        if row.get("bucket") and row.get("label")
    }
    base_keys = {key for key, _label, _emoji in [*_BASE_BURN_BUCKETS, _OTHER_BURN_BUCKET]}
    base = [
        (
            key,
            label,
            str(metadata.get(key, {}).get("emoji") or emoji),
            str(metadata.get(key, {}).get("color") or ""),
        )
        for key, label, emoji in _BASE_BURN_BUCKETS
    ]
    custom = [
        (key, str(item["label"]), str(item.get("emoji") or ""), str(item.get("color") or ""))
        for key, item in metadata.items()
        if key not in base_keys
    ]
    custom_order = {
        str(row["bucket"]): index
        for index, row in enumerate(bucket_rows)
        if row.get("bucket")
    }
    custom.sort(key=lambda item: (custom_order.get(item[0], 9999), item[1].lower()))
    other_key, other_label, other_emoji = _OTHER_BURN_BUCKET
    other = (
        other_key,
        other_label,
        str(metadata.get(other_key, {}).get("emoji") or other_emoji),
        str(metadata.get(other_key, {}).get("color") or ""),
    )
    return [*base, *custom, other]


def _burn_rules(ctx: AppContext) -> list[dict[str, Any]]:
    apply_app_schema(ctx.cfg, ctx.app_dir)
    return _q(ctx, "burn_rules")


def _burn_overrides(ctx: AppContext) -> dict[str, dict[str, Any]]:
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


def _burn_rule_matches(row: dict[str, Any], rule: dict[str, Any]) -> bool:
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


def _classify_burn_row(
    row: dict[str, Any], rules: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]
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
        if not _burn_rule_matches(row, rule):
            continue
        bucket = str(rule.get("bucket") or "")
        if bucket == "exclude":
            return None
        return (bucket, amount, str(rule.get("reason") or rule.get("label") or "burn rule"))
    if amount <= 0:
        return None
    return ("other", amount, "fallback")


def _classified_burn_rows(
    rows: list[dict[str, Any]], rules: list[dict[str, Any]], overrides: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    classified_rows = []
    for row in rows:
        classified = _classify_burn_row(row, rules, overrides)
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
                "category": row.get("category") or "",
                "reason": reason,
            }
        )
    return classified_rows


def _is_complete_burn_month(month_key: str, today: date) -> bool:
    year_text, month_text = month_key.split("-", 1)
    year = int(year_text)
    month = int(month_text)
    if (year, month) < (today.year, today.month):
        return True
    return (year, month) == (today.year, today.month) and today.day >= 25


def _smoothed_monthly_burn(bucket: str, rows: list[dict[str, Any]], today: date) -> float:
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
            month for month in sorted(by_month) if _is_complete_burn_month(month, today)
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


def _burn_bucket_color_select(current: str) -> str:
    options = []
    for value, label in _BURN_BUCKET_COLORS:
        selected = " selected" if value == current else ""
        options.append(
            f'<option value="{html.escape(value, quote=True)}"{selected}>{html.escape(label)}</option>'
        )
    return '<select name="color" aria-label="Bucket color">' + "".join(options) + "</select>"


def _burn_bucket_display(label: str, emoji: str) -> str:
    return f"{emoji} {label}" if emoji else label


def _burn_rate_add_card(ctx: AppContext) -> str:
    action = html.escape(ctx.action_url("create_burn_bucket"), quote=True)
    return (
        '<div class="burn-rate-add" data-burn-add>'
        '<button type="button" class="burn-rate-card burn-rate-add-button" data-burn-add-button '
        'aria-label="Add burn category">+</button>'
        f'<form class="burn-rate-add-form" method="post" action="{action}" data-burn-add-form hidden>'
        '<input type="text" name="emoji" placeholder="Emoji" maxlength="12">'
        '<input type="text" name="label" placeholder="New category" maxlength="40" required>'
        f"{_burn_bucket_color_select('')}"
        '<button type="submit">add</button>'
        "</form>"
        "</div>"
    )


def _burn_rate_cards(
    ctx: AppContext, by_bucket: dict[str, dict[str, Any]], buckets: list[tuple[str, str, str, str]]
) -> str:
    cards = []
    for key, label, emoji, color in buckets:
        item = by_bucket[key]
        monthly = _float(item["monthly"])
        escaped_key = html.escape(key, quote=True)
        display_label = _burn_bucket_display(label, emoji)
        escaped_label = html.escape(display_label)
        color_class = " has-color" if color else ""
        color_style = (
            f' style="--burn-bucket-color:{html.escape(color, quote=True)}"' if color else ""
        )
        cards.append(
            f'<button type="button" class="burn-rate-card{color_class}" data-burn-bucket="{escaped_key}" '
            f'data-burn-label="{html.escape(display_label, quote=True)}" '
            f'aria-pressed="false"{color_style}>'
            f'<span>{escaped_label}</span>'
            f"<strong>{html.escape(_money(monthly))}</strong>"
            f"<small>smoothed / mo - {int(item['count'])} txns / {_BURN_RATE_EVIDENCE_DAYS}d</small>"
            "</button>"
        )
    cards.append(_burn_rate_add_card(ctx))
    return f'<div class="burn-rate-grid" data-burn-rate-cards>{"".join(cards)}</div>'


def _burn_bucket_select(current: str, buckets: list[tuple[str, str, str, str]]) -> str:
    options = []
    bucket_options = [
        (bucket, _burn_bucket_display(label, emoji)) for bucket, label, emoji, _color in buckets
    ]
    for bucket, label in [*bucket_options, ("exclude", "Exclude")]:
        selected = " selected" if bucket == current else ""
        options.append(
            f'<option value="{html.escape(bucket, quote=True)}"{selected}>{html.escape(label)}</option>'
        )
    return '<select name="bucket">' + "".join(options) + "</select>"


def _burn_classification_controls(
    ctx: AppContext, row: dict[str, Any], buckets: list[tuple[str, str, str, str]]
) -> str:
    action = html.escape(ctx.action_url("set_burn_classification"), quote=True)
    transaction_id = html.escape(str(row["finance_transaction_id"]), quote=True)
    merchant = html.escape(str(row["merchant"]), quote=True)
    category = html.escape(str(row["category"]), quote=True)
    bucket = str(row["bucket"])
    return (
        f'<form class="burn-action" method="post" action="{action}">'
        f'<input type="hidden" name="finance_transaction_id" value="{transaction_id}">'
        f'<input type="hidden" name="merchant" value="{merchant}">'
        f'<input type="hidden" name="source_category" value="{category}">'
        f"{_burn_bucket_select(bucket, buckets)}"
        '<select name="scope">'
        '<option value="transaction">this txn</option>'
        '<option value="merchant">merchant</option>'
        '<option value="category">category</option>'
        "</select>"
        '<button type="submit">save</button>'
        "</form>"
    )


def _burn_rate_table(
    ctx: AppContext, rows: list[dict[str, Any]], buckets: list[tuple[str, str, str, str]]
) -> str:
    if not rows:
        return c.notice("No burn-rate transactions matched the current rule set.")
    grid_rows = []
    bucket_labels = {
        bucket: _burn_bucket_display(label, emoji) for bucket, label, emoji, _color in buckets
    }
    for row in rows:
        bucket = str(row["bucket"])
        grid_rows.append(
            {
                "__burnBucket": bucket,
                "bucket": bucket_labels.get(bucket, bucket),
                "date": row["date"],
                "merchant": row["merchant"],
                "amount": _money(row["amount"], cents=True),
                "source_category": row["category"],
                "matched_rule": row["reason"],
                "classify": _burn_classification_controls(ctx, row, buckets),
            }
        )
    columns = [
        {"field": "bucket", "headerName": "Bucket", "minWidth": 105},
        {"field": "date", "headerName": "Date", "minWidth": 110},
        {"field": "merchant", "headerName": "Merchant", "minWidth": 240},
        {"field": "amount", "headerName": "Amount", "minWidth": 100},
        {"field": "source_category", "headerName": "Source Category", "minWidth": 220},
        {
            "field": "matched_rule",
            "headerName": "Matched Rule",
            "minWidth": 150,
            "headerTooltip": "The rule or override that placed this transaction in its burn bucket.",
        },
        {
            "field": "classify",
            "headerName": "Classify",
            "cellRenderer": "html",
            "sortable": False,
            "filter": False,
            "minWidth": 310,
        },
    ]
    return (
        '<div class="burn-rate-detail" data-burn-rate-detail>'
        '<p class="meta" data-burn-rate-status>Showing all burn-rate transactions</p>'
        + aggrid.grid(
            columns,
            grid_rows,
            class_name="finance-grid burn-rate-tx-grid",
            page_size=25,
            height_px=560,
        )
        + "</div>"
    )


def _render_burn_rate(ctx: AppContext) -> str:
    buckets = _burn_buckets(ctx)
    rules = _burn_rules(ctx)
    overrides = _burn_overrides(ctx)
    smoothing_rows = _classified_burn_rows(
        _q(ctx, "burn_rate_transactions", days=_BURN_RATE_SMOOTHING_DAYS), rules, overrides
    )
    evidence_rows = _classified_burn_rows(
        _q(ctx, "burn_rate_transactions", days=_BURN_RATE_EVIDENCE_DAYS), rules, overrides
    )
    today = date.today()
    by_bucket = {
        key: {
            "monthly": _smoothed_monthly_burn(key, smoothing_rows, today),
            "count": 0,
        }
        for key, _label, _emoji, _color in buckets
    }
    detail_rows = []
    for row in evidence_rows:
        bucket = str(row["bucket"])
        if bucket not in by_bucket:
            by_bucket[bucket] = {"monthly": _smoothed_monthly_burn(bucket, smoothing_rows, today), "count": 0}
            buckets.insert(-1, (bucket, bucket.replace("_", " ").title(), "", ""))
        entry = by_bucket[bucket]
        entry["count"] = int(entry["count"]) + 1
        detail_rows.append(row)

    return c.section(
        "Personal Burn Rate",
        '<div data-burn-rate data-pdb-island="finance-burn-rate" '
        f'data-burn-rate-state-url="{html.escape(ctx.model_url("burn_rate"), quote=True)}" '
        f'data-burn-classification-action="{html.escape(ctx.action_url("set_burn_classification"), quote=True)}" '
        f'data-burn-create-bucket-action="{html.escape(ctx.action_url("create_burn_bucket"), quote=True)}">'
        f"{_burn_rate_cards(ctx, by_bucket, buckets)}"
        f"{_burn_rate_table(ctx, detail_rows, buckets)}</div>",
        subtitle=(
            "Smoothed monthly estimate from up to 180 days; table shows the last 90 days. "
            "Rent is net of Curiosity Research and Oliver Zou reimbursements."
        ),
    )


def _burn_rule_match_label(rule: dict[str, Any]) -> str:
    parts = []
    merchant = str(rule.get("merchant_pattern") or "")
    category = str(rule.get("category_pattern") or "")
    flag = str(rule.get("flag_name") or "")
    direction = str(rule.get("amount_direction") or "any")
    min_amount = rule.get("min_amount")
    if flag:
        parts.append(f"{flag} is true")
    if merchant:
        parts.append(f'merchant contains "{merchant}"')
    if category:
        match_type = str(rule.get("category_match_type") or "contains")
        parts.append(f"category {match_type} {category}")
    if direction != "any":
        parts.append(f"amount is {direction}")
    if min_amount is not None:
        parts.append(f"amount >= {_money(min_amount)}")
    return " + ".join(parts) if parts else "always"


def _burn_rules_section(ctx: AppContext) -> str:
    bucket_labels = {
        bucket: _burn_bucket_display(label, emoji)
        for bucket, label, emoji, _color in _burn_buckets(ctx)
    }
    rows = [
        (
            int(rule.get("priority") or 0),
            rule.get("label") or "",
            bucket_labels.get(str(rule.get("bucket") or ""), str(rule.get("bucket") or "")),
            _burn_rule_match_label(rule),
            rule.get("reason") or "",
            rule.get("source") or "",
        )
        for rule in _burn_rules(ctx)
    ]
    return c.section(
        "Burn Rate Rules",
        c.data_grid(
            rows,
            ["Priority", "Label", "Bucket", "Match", "Reason", "Source"],
            class_name="finance-grid",
            page_size=25,
        ),
        subtitle="Seed rules plus inline merchant/category rules created from the overview.",
    )


def _burn_bucket_color_controls(
    ctx: AppContext, bucket: str, label: str, emoji: str, color: str
) -> str:
    action = html.escape(ctx.action_url("set_burn_bucket_color"), quote=True)
    escaped_bucket = html.escape(bucket, quote=True)
    escaped_label = html.escape(label, quote=True)
    escaped_emoji = html.escape(emoji, quote=True)
    return (
        f'<form class="burn-bucket-color-form" method="post" action="{action}">'
        f'<input type="hidden" name="bucket" value="{escaped_bucket}">'
        f'<input type="hidden" name="label" value="{escaped_label}">'
        f'<input type="text" name="emoji" value="{escaped_emoji}" maxlength="12" '
        'aria-label="Bucket emoji">'
        f"{_burn_bucket_color_select(color)}"
        '<button type="submit">save</button>'
        "</form>"
    )


def _burn_buckets_section(ctx: AppContext) -> str:
    buckets = _burn_buckets(ctx)
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(emoji)}</td>"
        f"<td>{html.escape(label)}</td>"
        f"<td>{html.escape(color.title() if color else 'None')}</td>"
        f"<td>{_burn_bucket_color_controls(ctx, bucket, label, emoji, color)}</td>"
        "</tr>"
        for bucket, label, emoji, color in buckets
    )
    table = (
        '<table class="recent-rows burn-bucket-table">'
        "<thead><tr><th>Emoji</th><th>Bucket</th><th>Color</th><th>Customize</th></tr></thead>"
        f"<tbody>{rows}</tbody>"
        "</table>"
    )
    return c.section(
        "Burn Rate Buckets",
        table,
        subtitle="Bucket colors are optional. Colored buckets show up on the overview cards.",
    )


def render_rules(ctx: AppContext) -> str:
    rules_body = (
        '<div data-finance-rules data-pdb-island="finance-rules">'
        '<div class="finance-rules-feedback" data-finance-rules-status></div>'
        f"{_burn_buckets_section(ctx)}{_burn_rules_section(ctx)}"
        "</div>"
    )
    return _finance_page(
        ctx,
        "rules",
        "Finance Rules",
        rules_body,
        subtitle="Auditable burn-rate classification rules. Inline overview changes create transaction overrides or user rules here.",
    )


def _cashflow_section(ctx: AppContext, scope: str, title: str, *, show_table: bool = True) -> str:
    rows = _q(ctx, "cashflow_rows", owner=_scope_owner(scope), limit=180)
    ordered = list(reversed(rows))
    labels = [str(row["date"])[5:] for row in ordered]
    dates = [str(row["date"]) for row in ordered]
    net = [_float(row["net"]) for row in ordered]
    tooltip_fields = [
        {"key": "income", "label": "Income", "format": "usd"},
        {"key": "spending", "label": "Spending", "format": "usd"},
        {"key": "net", "label": "Net", "format": "usd"},
        {"key": "parent_draw", "label": "Parent draw", "format": "usd"},
        {"key": "credit_card_payments", "label": "Card payments", "format": "usd"},
        {"key": "internal_transfers", "label": "Transfers", "format": "usd"},
        {"key": "txn_count", "label": "Transactions", "format": "integer"},
    ]
    chart = ""
    if rows:
        chart = agcharts.gain_loss_area_chart(
            labels,
            net,
            extra_values={
                "income": [_float(row["income"]) for row in ordered],
                "spending": [_float(row["spending"]) for row in ordered],
                "parent_draw": [_float(row["parent_draw"]) for row in ordered],
                "credit_card_payments": [_float(row["credit_card_payments"]) for row in ordered],
                "internal_transfers": [_float(row["internal_transfers"]) for row in ordered],
                "txn_count": [_float(row["txn_count"]) for row in ordered],
            },
            tooltip_fields=tooltip_fields,
            height_px=250,
            value_attr="data-usd",
            month_markers=True,
            zoom_default_window=90,
            date_values=dates,
            aggregation=True,
            aggregation_default_mode="week",
            aggregation_sum_keys=[
                "net",
                "income",
                "spending",
                "parent_draw",
                "credit_card_payments",
                "internal_transfers",
                "txn_count",
            ],
            scale_default_mode="full",
        )
    if not show_table:
        return c.section(title, chart)
    table_rows = [
        (
            row["date"],
            _money(row["income"]),
            _money(row["spending"]),
            _signed_money(row["net"]),
            _money(row["parent_draw"]),
            _money(row["credit_card_payments"]),
            _money(row["internal_transfers"]),
            str(row["txn_count"]),
        )
        for row in rows[:90]
    ]
    return c.section(
        title,
        chart,
        c.data_grid(
            table_rows,
            [
                "Date",
                "Income",
                "Spending",
                "Net",
                "Parent Draw",
                "Card Payments",
                "Transfers",
                "Transactions",
            ],
            class_name="finance-grid",
            page_size=20,
        ),
    )


def _parent_draw_section(ctx: AppContext) -> str:
    daily = _q(ctx, "parent_draw_daily_rows", limit=180)
    labels = [str(row["date"])[5:] for row in daily]
    dates = [str(row["date"]) for row in daily]
    draws = [-_float(row["parent_draw"]) for row in daily]
    chart = ""
    if daily:
        # AG Charts canvas options (rendered client-side, not CSS) -- can't consume
        # var(). positive/negative are categorical semantics left as-is out of
        # scope; line_color="#111111" is auto-remapped to a light gray in dark
        # mode by pdb-chart.js's literal-black remap.
        chart = agcharts.gain_loss_area_chart(
            labels,
            draws,
            height_px=220,
            value_attr="data-usd",
            positive_color="#167a3f",
            negative_color="#b23a48",
            line_color="#111111",
            month_markers=True,
            zoom_default_window=90,
            date_values=dates,
            aggregation=True,
            aggregation_default_mode="week",
            scale_default_mode="full",
        )
    rows = [
        (
            row.get("date") or "",
            row.get("institution") or "",
            row.get("account_name") or "",
            row.get("source") or "",
            row.get("merchant") or "",
            _money(row.get("amount"), cents=True),
            row.get("category") or "",
        )
        for row in _q(ctx, "parent_draw_rows", limit=300)
    ]
    return c.section(
        "Parent Account Draws",
        chart,
        c.data_grid(
            rows,
            ["Date", "Institution", "Account", "Source", "Merchant", "Amount", "Category"],
            class_name="finance-grid",
            page_size=25,
        ),
        subtitle="Outflows from parent-managed accounts. Parent accounts stay out of personal net worth.",
    )


def _net_worth_section(ctx: AppContext, scope: str, title: str) -> str:
    rows = list(reversed(_q(ctx, "net_worth_rows", owner=_scope_owner(scope), limit=370)))
    if not rows:
        return c.section(
            title,
            c.empty_state(
                "No net worth history yet",
                hint="Net worth builds from Plaid or Monarch account balances — sync finance to populate it.",
            ),
        )
    labels = [str(row["date"])[5:] for row in rows]
    return c.section(
        title,
        # AG Charts canvas series colors -- can't consume var(). "#111111" is
        # auto-remapped to a light gray in dark mode by pdb-chart.js; the rest
        # are categorical series colors intentionally left fixed.
        agcharts.multi_line_chart(
            labels,
            [
                (
                    "Net worth",
                    [_float(row["net_worth"]) for row in rows],
                    "#111111",
                    {"width": 2.4},
                ),
                ("Investments", [_float(row["investments"]) for row in rows], "#2364aa"),
                ("Cash", [_float(row["cash"]) for row in rows], "#167a3f"),
                ("Credit cards", [_float(row["cards"]) for row in rows], "#b23a48"),
            ],
            height_px=240,
            value_attr="data-usd",
            zoom_default_window=180,
        ),
    )


def _holding_section(ctx: AppContext, scope: str, title: str) -> str:
    holdings = _q(ctx, "holding_rows", scope=scope, limit=300)
    allocation: dict[str, float] = {}
    table_rows = []
    for row in holdings:
        holding = row.get("holding") or "Unknown"
        value = _float(row.get("value"))
        if value > 0:
            allocation[str(holding)] = allocation.get(str(holding), 0.0) + value
        table_rows.append(
            (
                row.get("institution_name") or "",
                row.get("account_name") or "",
                row.get("source") or "",
                holding,
                f"{_float(row.get('quantity')):,.4f}",
                _money(value, cents=True),
                _compact_time(row.get("as_of")),
            )
        )
    ranked = sorted(allocation.items(), key=lambda item: item[1], reverse=True)
    pie_items = ranked[:8]
    if len(ranked) > 8:
        other = sum(value for _, value in ranked[8:])
        if other:
            pie_items.append(("Other", other))
    pie = (
        agcharts.pie_chart(pie_items, value_format="usd")
        if pie_items
        else c.empty_state(
            "No holdings to chart",
            hint="Investment holdings sync from Plaid or Monarch brokerage accounts.",
        )
    )
    return c.section(
        title,
        c.section("Investment Accounts", _account_table(ctx, scope, investments=True)),
        c.section("Holdings Allocation", pie),
        c.section(
            "Latest Holdings",
            c.data_grid(
                table_rows,
                ["Institution", "Account", "Source", "Holding", "Quantity", "Value", "As Of"],
                class_name="finance-grid",
                page_size=25,
            ),
        ),
    )


def _scope_page(ctx: AppContext, scope: str, title: str) -> str:
    metrics, latest_date = _latest_metrics(ctx, scope)
    subtitle = f"Latest finance mart snapshot: {latest_date}" if latest_date else ""
    sections = [
        c.metric_grid(metrics),
        _net_worth_section(ctx, scope, f"{title} Net Worth"),
        _cashflow_section(ctx, scope, f"{title} Cashflow"),
        c.section(f"{title} Accounts", _account_table(ctx, scope)),
        _holding_section(ctx, scope, f"{title} Investments"),
    ]
    if scope == "parents":
        sections.append(_parent_draw_section(ctx))
    return _finance_page(ctx, scope, title, *sections, subtitle=subtitle)


def metrics(cfg) -> list[dict]:
    """Dashboard tile metrics for the Finance app: cashflow (7d) and spend
    this month, both from the combined ('all' owner) daily cashflow mart.

    Deliberately different from the `finance` tracker's own tile (net worth
    + cash, see templates/trackers/finance/visualizations.py) so the two
    tiles don't just duplicate each other on the dashboard.
    """
    try:
        con = connect(cfg.db_path, read_only=True)
    except sqlite3.OperationalError:
        return []
    try:
        has_any = con.execute(
            "SELECT 1 FROM finance_daily_cashflow WHERE owner = 'all' LIMIT 1"
        ).fetchone()
        if not has_any:
            return []
        cutoff_7d = (date.today() - timedelta(days=7)).isoformat()
        month_start = date.today().replace(day=1).isoformat()
        (cashflow_7d,) = con.execute(
            "SELECT SUM(net) FROM finance_daily_cashflow WHERE owner = 'all' AND date >= ?",
            (cutoff_7d,),
        ).fetchone()
        (spend_month,) = con.execute(
            "SELECT SUM(spending) FROM finance_daily_cashflow WHERE owner = 'all' AND date >= ?",
            (month_start,),
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    cashflow_7d = float(cashflow_7d or 0)
    spend_month = float(spend_month or 0)
    good = True if cashflow_7d > 0 else (False if cashflow_7d < 0 else None)
    return [
        {
            "label": "Cashflow (7d)",
            "value": _signed_money(cashflow_7d),
            "detail": None,
            "delta": None,
            "good": good,
            "sensitive": True,
        },
        {
            "label": "Spend this month",
            "value": _money(spend_month),
            "detail": None,
            "delta": None,
            "good": None,
            "sensitive": True,
        },
    ]


def render_overview(ctx: AppContext) -> str:
    self_metrics, _self_date = _latest_metrics(ctx, "self")
    parent_metrics, _parent_date = _latest_metrics(ctx, "parents")
    has_data = bool(_q(ctx, "account_rows", scope="all"))
    if not has_data:
        return _finance_page(
            ctx,
            "overview",
            "Finance Overview",
            c.empty_state(
                "No finance data yet",
                hint="Finance combines transactions from Plaid or Monarch. First sync may take a few minutes.",
                # Note: link to the /setup overview, not a per-tracker deep link
                # like /setup/plaid -- that route 404s until the tracker is
                # installed, which is exactly the state a first-time user is in.
                action=("Set up Plaid", "/setup"),
            ),
        )
    dashboard = (
        '<div class="finance-dashboard" data-finance-dashboard>'
        + c.section("Self", c.metric_grid(self_metrics))
        + '<div data-finance-parent="1">'
        + c.section("Parents", c.metric_grid(parent_metrics), class_name="finance-parent-section")
        + "</div>"
        + _cashflow_section(ctx, "self", "Self Cashflow", show_table=False)
        + '<div data-finance-parent="1">'
        + _cashflow_section(ctx, "parents", "Parent Cashflow", show_table=False)
        + "</div>"
        + _render_burn_rate(ctx)
        + "</div>"
    )
    return _finance_page(
        ctx,
        "overview",
        "Finance Overview",
        dashboard,
        subtitle="A local app surface over the finance mart.",
    )


def render_self(ctx: AppContext) -> str:
    return _scope_page(ctx, "self", "Self")


def render_parents(ctx: AppContext) -> str:
    return _scope_page(ctx, "parents", "Parents")


def render_review(ctx: AppContext) -> str:
    categories = _category_map(ctx)
    reviews = _review_map(ctx)
    presets = _category_presets(ctx)
    transaction_rows = []
    for row in _q(ctx, "transaction_category_candidates", limit=250):
        transaction_id = str(row["finance_transaction_id"])
        state = categories.get(transaction_id)
        transaction_rows.append(
            (
                row.get("date") or "",
                row.get("merchant") or "",
                row.get("owner") or "",
                row.get("source") or "",
                _money(row.get("amount"), cents=True),
                row.get("category") or "",
                state.get("category") if state else "",
                _category_controls(ctx, transaction_id, state),
            )
        )
    parent_rows = []
    for row in _q(ctx, "parent_draw_rows", limit=250):
        review_key = str(row["finance_transaction_id"])
        state = reviews.get(review_key)
        parent_rows.append(
            (
                row.get("date") or "",
                row.get("institution") or "",
                row.get("account_name") or "",
                row.get("merchant") or "",
                _money(row.get("amount"), cents=True),
                row.get("category") or "",
                _review_status_cell(state),
                _review_controls(ctx, review_key, "parent_draw", state),
            )
        )
    recurring_rows = []
    for row in _q(ctx, "recurring_candidates", limit=100):
        review_key = _review_key_for_recurring(row)
        state = reviews.get(review_key)
        recurring_rows.append(
            (
                row.get("merchant") or "",
                row.get("owner") or "",
                str(row.get("txn_count") or ""),
                _money(row.get("avg_amount"), cents=True),
                row.get("first_seen") or "",
                row.get("last_seen") or "",
                row.get("category") or "",
                _review_status_cell(state),
                _review_controls(ctx, review_key, "recurring_candidate", state),
            )
        )
    review_body = "".join(
        (
            '<div data-finance-categorize data-pdb-island="finance-categorize" '
            f'data-categorize-state-url="{html.escape(ctx.model_url("categorize"), quote=True)}">'
            '<div class="finance-categorize-feedback" data-finance-categorize-status></div>',
            c.section(
                "Transaction Categorization",
                c.data_grid(
                    transaction_rows,
                    [
                        "Date",
                        "Merchant",
                        "Owner",
                        "Source",
                        "Amount",
                        "Source Category",
                        "App Category",
                        "Actions",
                    ],
                    class_name="finance-grid",
                    page_size=25,
                    html_columns={7},
                ),
                subtitle="App categories are local overrides; source transactions are not mutated.",
                class_name="finance-categorize-transactions",
            ),
            c.section(
                "Parent Draws",
                c.data_grid(
                    parent_rows,
                    [
                        "Date",
                        "Institution",
                        "Account",
                        "Merchant",
                        "Amount",
                        "Category",
                        "Status",
                        "Actions",
                    ],
                    class_name="finance-grid",
                    page_size=25,
                    html_columns={7},
                ),
                subtitle="Review parent-managed outflows without mutating source transactions.",
                class_name="finance-categorize-parent-draws",
            ),
            c.section(
                "Recurring Candidates",
                c.data_grid(
                    recurring_rows,
                    [
                        "Merchant",
                        "Owner",
                        "Count",
                        "Average",
                        "First Seen",
                        "Last Seen",
                        "Category",
                        "Status",
                        "Actions",
                    ],
                    class_name="finance-grid",
                    page_size=25,
                    html_columns={8},
                ),
                subtitle="Repeated merchants from the last 180 days.",
                class_name="finance-categorize-recurring",
            ),
            "</div>",
        )
    )
    return _finance_page(
        ctx,
        "review",
        "Finance Review",
        _category_datalist(presets),
        review_body,
    )


def _receipt_rows(ctx: AppContext) -> list[dict[str, Any]]:
    apply_enrichment_schema(ctx.cfg)
    return _q(ctx, "receipt_enrichment_rows", limit=200)


def _receipt_metrics(rows: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("receipt_status") or "missing")
        counts[status] = counts.get(status, 0) + 1
    attention = counts.get("no_match", 0) + counts.get("uncertain", 0) + counts.get("no_context", 0)
    return [
        ("Enriched", str(counts.get("enriched", 0)), "latest v1 matched"),
        ("Needs attention", str(attention), "no match, uncertain, or no context"),
        ("Missing", str(counts.get("missing", 0)), "not run yet"),
        ("Visible rows", str(len(rows)), "recent positive transactions"),
    ]


def _confidence(value: Any) -> str:
    if value is None:
        return ""
    try:
        return f"{float(value) * 100:.0f}%"
    except (TypeError, ValueError):
        return ""


def _evidence_cell(refs_text: Any) -> str:
    refs = [ref.strip() for ref in str(refs_text or "").split(",") if ref.strip()]
    if not refs:
        return ""
    title = html.escape("\n".join(refs), quote=True)
    first = html.escape(refs[0])
    suffix = f" +{len(refs) - 1}" if len(refs) > 1 else ""
    return f'<span title="{title}">{first}{html.escape(suffix)}</span>'


def _receipt_rerun_controls(ctx: AppContext, transaction_id: str) -> str:
    action = html.escape(ctx.action_url("rerun_receipt_enrichment"), quote=True)
    escaped_id = html.escape(transaction_id, quote=True)
    return (
        f'<form class="receipt-rerun-action" method="post" action="{action}">'
        f'<input type="hidden" name="finance_transaction_id" value="{escaped_id}">'
        '<input type="hidden" name="window_days" value="7">'
        '<input type="hidden" name="max_threads" value="3">'
        f'<input type="hidden" name="max_candidate_threads" value="{DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS}">'
        '<input type="hidden" name="snippet_window_chars" value="300">'
        '<button type="submit">rerun</button>'
        "</form>"
    )


def render_receipts(ctx: AppContext) -> str:
    rows = _receipt_rows(ctx)
    if not rows:
        body = c.empty_state(
            "No finance transactions yet",
            hint="Receipts match against synced Plaid/Monarch transactions — sync finance first.",
            action=("Go to Finance Overview", "/a/finance"),
        )
    else:
        grid_rows = []
        for row in rows:
            transaction_id = str(row.get("finance_transaction_id") or "")
            grid_rows.append(
                {
                    "status": row.get("receipt_status") or "missing",
                    "date": row.get("date") or "",
                    "merchant": row.get("merchant") or "",
                    "amount": _money(row.get("amount"), cents=True),
                    "match": row.get("agent_match") or row.get("decision") or "",
                    "confidence": _confidence(row.get("confidence")),
                    "reasoning": row.get("reasoning") or row.get("result_summary") or "",
                    "evidence": _evidence_cell(row.get("evidence_refs")),
                    "updated": _compact_time(row.get("updated_at")),
                    "action": _receipt_rerun_controls(ctx, transaction_id),
                }
            )
        columns = [
            {"field": "status", "headerName": "Status", "minWidth": 120},
            {"field": "date", "headerName": "Date", "minWidth": 110},
            {"field": "merchant", "headerName": "Merchant", "minWidth": 180},
            {"field": "amount", "headerName": "Amount", "minWidth": 100},
            {"field": "match", "headerName": "Agent", "minWidth": 100},
            {"field": "confidence", "headerName": "Conf", "minWidth": 90},
            {"field": "reasoning", "headerName": "Reasoning", "minWidth": 360},
            {
                "field": "evidence",
                "headerName": "Evidence",
                "cellRenderer": "html",
                "minWidth": 220,
            },
            {"field": "updated", "headerName": "Updated", "minWidth": 170},
            {
                "field": "action",
                "headerName": "",
                "cellRenderer": "html",
                "sortable": False,
                "filter": False,
                "minWidth": 110,
            },
        ]
        body = c.section(
            "Receipt Enrichment",
            c.metric_grid(_receipt_metrics(rows)),
            aggrid.grid(
                columns,
                grid_rows,
                class_name="finance-grid receipt-enrichment-grid",
                page_size=25,
                height_px=650,
            ),
        )
    return _finance_page(
        ctx,
        "receipts",
        "Finance Receipts",
        body,
        subtitle="Latest v1 email receipt enrichment results and manual reruns.",
    )


def render_settings(ctx: AppContext) -> str:
    reads = "".join(
        f"<li><code>{html.escape(table)}</code></li>" for table in ctx.manifest.reads.tables
    )
    actions = "".join(
        f"<li><code>{html.escape(action)}</code></li>" for action in ctx.manifest.writes.actions
    )
    if not actions:
        actions = "<li>no app actions declared yet</li>"
    return _finance_page(
        ctx,
        "settings",
        "Finance Settings",
        c.section("Data Contract", f"<ul>{reads}</ul>"),
        c.section("Actions", f"<ul>{actions}</ul>"),
        c.notice(
            "Display preferences will live here; source account ownership stays in trackers and marts."
        ),
    )
