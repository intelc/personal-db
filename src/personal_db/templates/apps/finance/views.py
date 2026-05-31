from __future__ import annotations

import html
import sqlite3
from typing import Any

from personal_db.apps import AppContext, apply_app_schema
from personal_db.ui import agcharts
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


def _latest_metrics(ctx: AppContext, scope: str) -> tuple[list[tuple[str, str, str]], str]:
    owner = _scope_owner(scope)
    latest = _q(ctx, "latest_net_worth", owner=owner)
    if latest:
        row = latest[0]
        return [
            (
                "Net worth" if scope == "self" else "Managed total",
                _money(row["net_worth"]),
                f"as of {row['date']}",
            ),
            ("Assets", _money(row["assets"]), ""),
            ("Debts", _money(row["debts"]), ""),
            ("Cash", _money(row["cash"]), ""),
            ("Investments", _money(row["investments"]), ""),
            ("Credit cards", _money(-_float(row["credit_card_debt"])), ""),
        ], str(row["date"])
    summary = _account_summary(ctx, scope)
    return [
        (
            "Net worth" if scope == "self" else "Managed total",
            _money(summary["net_worth"]),
            "from latest accounts",
        ),
        ("Assets", _money(summary["assets"]), ""),
        ("Debts", _money(summary["debts"]), ""),
        ("Cash", _money(summary["cash"]), ""),
        ("Investments", _money(summary["investments"]), ""),
        ("Accounts", str(int(summary["accounts"])), ""),
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


def _cashflow_section(ctx: AppContext, scope: str, title: str) -> str:
    rows = _q(ctx, "cashflow_rows", owner=_scope_owner(scope), limit=180)
    ordered = list(reversed(rows))
    labels = [str(row["date"])[5:] for row in ordered]
    dates = [str(row["date"]) for row in ordered]
    net = [_float(row["net"]) for row in ordered]
    chart = ""
    if rows:
        chart = agcharts.gain_loss_area_chart(
            labels,
            net,
            height_px=250,
            value_attr="data-usd",
            month_markers=True,
            zoom_default_window=90,
            date_values=dates,
            aggregation=True,
            aggregation_default_mode="week",
            scale_default_mode="full",
        )
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


def _net_worth_section(ctx: AppContext, scope: str, title: str) -> str:
    rows = list(reversed(_q(ctx, "net_worth_rows", owner=_scope_owner(scope), limit=370)))
    if not rows:
        return c.section(title, c.empty_state("No net worth history yet"))
    labels = [str(row["date"])[5:] for row in rows]
    return c.section(
        title,
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
        else c.empty_state("No holdings to chart")
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
    return c.page(
        title,
        c.metric_grid(metrics),
        _net_worth_section(ctx, scope, f"{title} Net Worth"),
        _cashflow_section(ctx, scope, f"{title} Cashflow"),
        c.section(f"{title} Accounts", _account_table(ctx, scope)),
        _holding_section(ctx, scope, f"{title} Investments"),
        subtitle=subtitle,
    )


def render_overview(ctx: AppContext) -> str:
    self_metrics, _self_date = _latest_metrics(ctx, "self")
    parent_metrics, _parent_date = _latest_metrics(ctx, "parents")
    has_data = bool(_q(ctx, "account_rows", scope="all"))
    if not has_data:
        return c.page(
            "Finance Overview",
            c.notice("No combined finance data yet. Sync Plaid and/or Monarch, then sync finance."),
        )
    return c.page(
        "Finance Overview",
        c.section("Self", c.metric_grid(self_metrics)),
        c.section("Parents", c.metric_grid(parent_metrics), class_name="finance-parent-section"),
        _cashflow_section(ctx, "self", "Self Cashflow"),
        _cashflow_section(ctx, "parents", "Parent Cashflow"),
        subtitle="A local app surface over the finance mart.",
    )


def render_self(ctx: AppContext) -> str:
    return _scope_page(ctx, "self", "Self")


def render_parents(ctx: AppContext) -> str:
    return _scope_page(ctx, "parents", "Parents")


def render_review(ctx: AppContext) -> str:
    categories = _category_map(ctx)
    presets = _category_presets(ctx)
    rows = []
    for row in _q(ctx, "transaction_category_candidates", limit=250):
        transaction_id = str(row["finance_transaction_id"])
        state = categories.get(transaction_id)
        rows.append(
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
    return c.page(
        "Transaction Categorization",
        _category_datalist(presets),
        c.section(
            "Recent Transactions",
            c.data_grid(
                rows,
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
        ),
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
    return c.page(
        "Finance Settings",
        c.section("Data Contract", f"<ul>{reads}</ul>"),
        c.section("Actions", f"<ul>{actions}</ul>"),
        c.notice(
            "Display preferences will live here; source account ownership stays in trackers and marts."
        ),
    )
