from __future__ import annotations

import html
from datetime import date, timedelta

from personal_db.db import connect
from personal_db.ui import agcharts
from personal_db.ui.aggrid import table_grid
from personal_db.ui.charts import horizontal_bars

SELF_OWNERS = {"self", "me", "personal"}


def _rows(cfg, sql: str, params: tuple = ()) -> list[tuple]:
    con = connect(cfg.db_path, read_only=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _table_exists(cfg, table: str) -> bool:
    rows = _rows(
        cfg,
        "SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?",
        (table,),
    )
    return bool(rows)


class _SafeHtml(str):
    pass


def _html_columns(rows: list[tuple]) -> set[int]:
    return {
        i
        for row in rows
        for i, value in enumerate(row)
        if isinstance(value, _SafeHtml)
    }


def _table(rows: list, headers: list[str], *, class_name: str = "", page_size: int = 15) -> str:
    if not rows:
        return '<p class="meta">no data</p>'
    return table_grid(
        rows,
        headers,
        class_name=f"finance-grid {class_name}".strip(),
        page_size=page_size,
        html_columns=_html_columns(rows),
    )


def _grouped_table(
    rows: list[tuple],
    headers: list[str],
    *,
    group_index: int,
    group_label: str = "Bank",
    item_label: str = "rows",
    class_name: str = "",
    page_size: int = 15,
) -> str:
    if not rows:
        return '<p class="meta">no data</p>'
    return table_grid(
        rows,
        headers,
        class_name=f"finance-grid finance-grid-grouped {class_name}".strip(),
        page_size=page_size,
        html_columns=_html_columns(rows),
        group_index=group_index,
        group_label=group_label,
        item_label=item_label,
    )


def _money(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "$0"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _coerce_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money2(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "$0.00"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _compact_time(value) -> str:
    text = str(value or "")
    if "T" in text:
        text = text.replace("T", " ")
    for suffix in ("+00:00", "Z"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    if "." in text:
        text = text.split(".", 1)[0]
    return text


def _owner_badge(owner: str | None) -> _SafeHtml:
    value = (owner or "self").strip().lower() or "self"
    label = "parents" if value == "parents" else "self"
    klass = "owner-parents" if label == "parents" else "owner-self"
    text = "Parents" if label == "parents" else "Self"
    return _SafeHtml(
        f'<span class="finance-badge owner-badge {klass}">'
        f'<span class="finance-badge-dot"></span>{html.escape(text)}</span>'
    )


def _source_badge(source: str | None) -> _SafeHtml:
    value = (source or "").strip().lower() or "source"
    label = value.title()
    klass = "source-monarch" if value == "monarch" else "source-plaid" if value == "plaid" else "source-other"
    return _SafeHtml(f'<span class="finance-badge source-badge {klass}">{html.escape(label)}</span>')


def _group_cell(group: str | None, subtype: str | None) -> _SafeHtml:
    group_text = html.escape(str(group or "other"))
    subtype_text = html.escape(str(subtype or ""))
    extra = f'<span class="finance-cell-sub">{subtype_text}</span>' if subtype_text else ""
    return _SafeHtml(f'<span class="finance-cell-main">{group_text}</span>{extra}')


def _balance_cell(balance, *, sublabel: str | None = None, subvalue=None) -> _SafeHtml:
    extra = ""
    if sublabel and subvalue is not None:
        extra = f'<span class="finance-cell-sub">{html.escape(sublabel)}: {html.escape(_money2(subvalue))}</span>'
    return _SafeHtml(f'<span class="finance-cell-main">{html.escape(_money2(balance))}</span>{extra}')


def _account_cell(account: str | None, source: str | None) -> _SafeHtml:
    return _SafeHtml(
        f'<span class="finance-cell-main">{html.escape(str(account or ""))}</span>'
        '<span class="finance-account-meta">'
        f"{_source_badge(source)}"
        "</span>"
    )


def _signed_money(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "$0"
    if v > 0:
        return f"+${v:,.0f}"
    if v < 0:
        return f"-${abs(v):,.0f}"
    return "$0"


def _metric_table(items: list[tuple[str, str]]) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(label)}</th><td>{html.escape(value)}</td></tr>"
        for label, value in items
    )
    return f'<div class="finance-table-wrap finance-kv-wrap"><table class="finance-table kv"><tbody>{rows}</tbody></table></div>'


def _pie_items(raw_items: list[tuple[str, float]], *, top_n: int = 8) -> list[tuple[str, float]]:
    totals: dict[str, float] = {}
    for label, value in raw_items:
        amount = _coerce_float(value)
        if amount <= 0:
            continue
        key = str(label or "Unknown")
        totals[key] = totals.get(key, 0.0) + amount
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    if len(ranked) <= top_n:
        return ranked
    shown = ranked[:top_n]
    other = sum(value for _, value in ranked[top_n:])
    if other > 0:
        shown.append(("Other", other))
    return shown


def _pie_chart(items: list[tuple[str, float]]) -> str:
    if not items:
        return '<p class="meta">no holdings to chart</p>'
    total = sum(value for _, value in items)
    if total <= 0:
        return '<p class="meta">no holdings to chart</p>'
    return agcharts.pie_chart(items, value_format="usd")


def _owner_options(cfg) -> list[str]:
    rows = _rows(
        cfg,
        """
        SELECT owner FROM finance_daily_net_worth
        UNION
        SELECT owner FROM finance_daily_cashflow
        ORDER BY owner
        """,
    )
    owners = [r[0] for r in rows if r[0]]
    return ["all", *[o for o in owners if o != "all"]]


def _latest_net_worth(cfg, owner: str = "all") -> tuple | None:
    rows = _rows(
        cfg,
        """
        SELECT date, cash, investments, credit_card_debt, other, assets, debts, net_worth
        FROM finance_daily_net_worth
        WHERE owner=?
        ORDER BY date DESC
        LIMIT 1
        """,
        (owner,),
    )
    return rows[0] if rows else None


def _render_overview(cfg) -> str:
    latest = _latest_net_worth(cfg, "all")
    if not latest:
        return (
            "<h2>Finance Overview</h2>"
            '<p class="meta">No combined finance data yet. Sync Plaid and/or Monarch, then sync finance.</p>'
        )
    latest_date, cash, investments, credit_debt, other, assets, debts, net_worth = latest
    net_rows = _rows(
        cfg,
        """
        SELECT date, net_worth
        FROM finance_daily_net_worth
        WHERE owner='all'
        ORDER BY date
        LIMIT 370
        """,
    )
    labels = [r[0][5:] for r in net_rows]
    values = [float(r[1] or 0) for r in net_rows]
    account_breakdown = [
        ("Cash/checking", float(cash or 0)),
        ("Investments", float(investments or 0)),
        ("Other assets", float(other or 0)),
        ("Credit card debt", float(credit_debt or 0)),
    ]
    managed_parent = _rows(
        cfg,
        """
        SELECT account_group, SUM(COALESCE(current_balance, 0))
        FROM finance_accounts
        WHERE owner NOT IN ('self', 'me', 'personal')
        GROUP BY account_group
        ORDER BY ABS(SUM(COALESCE(current_balance, 0))) DESC
        """,
    )
    cashflow = _rows(
        cfg,
        """
        SELECT date, income, spending, net
        FROM finance_daily_cashflow
        WHERE owner='all'
          AND date >= ?
        ORDER BY date
        """,
        ((date.today() - timedelta(days=90)).isoformat(),),
    )
    cf_dates = [r[0] for r in cashflow]
    cf_labels = [r[0][5:] for r in cashflow]
    net = [float(r[3] or 0) for r in cashflow]
    return (
        "<h2>Finance Overview</h2>"
        + _metric_table(
            [
                ("As of", str(latest_date)),
                ("Personal net worth", _money(net_worth)),
                ("Assets", _money(assets)),
                ("Debts", _money(debts)),
                ("Cash/checking", _money(cash)),
                ("Investments", _money(investments)),
                ("Credit cards", _money(-float(credit_debt or 0))),
            ]
        )
        + "<h3>Net Worth</h3>"
        + agcharts.line_chart(
            list(zip(labels, values, strict=False)),
            color="#111111",
            height_px=180,
            show_every_nth_label=max(len(labels) // 8, 1),
            value_attr="data-usd",
        )
        + "<h3>Personal Account Groups</h3>"
        + horizontal_bars(
            [(label, abs(value)) for label, value in account_breakdown if value],
            value_fmt=_money,
            color="#2364aa",
        )
        + "<h3>Managed Parent Accounts</h3>"
        + horizontal_bars(
            [(str(group), abs(float(value or 0))) for group, value in managed_parent],
            value_fmt=_money,
            color="#6b6f2a",
        )
        + "<h3>Daily Cashflow</h3>"
        + agcharts.gain_loss_area_chart(
            cf_labels,
            net,
            height_px=230,
            show_every_nth_label=max(len(cf_labels) // 8, 1),
            value_attr="data-usd",
            month_markers=True,
            zoom_default_window=90,
            date_values=cf_dates,
            aggregation=True,
            aggregation_default_mode="week",
            scale_default_mode="full",
        )
        + '<p class="meta">Credit-card payments and internal transfers are excluded from income, spending, and net.</p>'
    )


def _render_accounts(cfg) -> str:
    raw_rows = _rows(
        cfg,
        """
        SELECT source,
               account_group,
               owner,
               institution_name,
               account_name,
               subtype,
               current_balance,
               iso_currency_code,
               as_of
        FROM finance_accounts
        WHERE account_group != 'investments'
        ORDER BY institution_name, account_group, owner, source, account_name
        """,
    )
    rows = [
        (
            _group_cell(group, subtype),
            institution,
            _account_cell(account, source),
            _money2(current),
            currency,
            _compact_time(as_of),
        )
        for source, group, owner, institution, account, subtype, current, currency, as_of in raw_rows
    ]
    return "<h2>Finance Accounts</h2>" + _grouped_table(
        rows,
        [
            "Group",
            "Institution",
            "Account",
            "Current",
            "Currency",
            "As Of",
        ],
        group_index=1,
        group_label="Bank",
        item_label="accounts",
        class_name="finance-accounts-table",
    )


def _render_cashflow(cfg) -> str:
    rows = _rows(
        cfg,
        """
        SELECT date, income, spending, net, parent_draw, credit_card_payments, internal_transfers
        FROM finance_daily_cashflow
        WHERE owner='all'
        ORDER BY date DESC
        LIMIT 120
        """,
    )
    ordered = list(reversed(rows))
    dates = [r[0] for r in ordered]
    labels = [r[0][5:] for r in ordered]
    net = [float(r[3] or 0) for r in ordered]
    table_rows = [
        (
            r[0],
            _money(r[1]),
            _money(r[2]),
            _signed_money(r[3]),
            _money(r[4]),
            _money(r[5]),
            _money(r[6]),
        )
        for r in rows[:45]
    ]
    return (
        "<h2>Daily Cashflow</h2>"
        + agcharts.gain_loss_area_chart(
            labels,
            net,
            height_px=260,
            show_every_nth_label=max(len(labels) // 10, 1),
            value_attr="data-usd",
            month_markers=True,
            zoom_default_window=90,
            date_values=dates,
            aggregation=True,
            aggregation_default_mode="week",
            scale_default_mode="full",
        )
        + '<p class="meta">Cashflow excludes credit-card payments and internal transfers, so paying a card does not double-count spending.</p>'
        + _table(
            table_rows,
            [
                "Date",
                "Income",
                "Spending",
                "Net",
                "Parent Draw",
                "Card Payments Excluded",
                "Transfers Excluded",
            ],
        )
    )


def _render_net_worth(cfg) -> str:
    owners = _owner_options(cfg)
    latest_rows = []
    for owner in owners:
        latest = _latest_net_worth(cfg, owner)
        if latest:
            latest_rows.append(
                (owner, _money(latest[7]), _money(latest[1]), _money(latest[2]), _money(latest[3]))
            )
    rows = _rows(
        cfg,
        """
        SELECT date, cash, investments, -credit_card_debt AS cards, net_worth
        FROM finance_daily_net_worth
        WHERE owner='all'
        ORDER BY date
        LIMIT 370
        """,
    )
    labels = [r[0][5:] for r in rows]
    cash = [float(r[1] or 0) for r in rows]
    investments = [float(r[2] or 0) for r in rows]
    cards = [float(r[3] or 0) for r in rows]
    net = [float(r[4] or 0) for r in rows]
    return (
        "<h2>Net Worth</h2>"
        + agcharts.multi_line_chart(
            labels,
            [
                ("Net worth", net, "#111111", {"width": 2.4}),
                ("Investments", investments, "#2364aa"),
                ("Cash", cash, "#167a3f"),
                ("Credit cards", cards, "#b23a48"),
            ],
            height_px=220,
            show_every_nth_label=max(len(labels) // 10, 1),
            value_attr="data-usd",
        )
        + _table(latest_rows, ["Owner", "Net Worth", "Cash", "Investments", "Credit Card Debt"])
    )


def _render_investments(cfg) -> str:
    raw_account_rows = _rows(
        cfg,
        """
        SELECT owner, source, institution_name, account_name, current_balance,
               as_of
        FROM finance_accounts
        WHERE account_group = 'investments'
        ORDER BY institution_name, owner, source, account_name
        """,
    )
    account_rows = [
        (
            _owner_badge(owner),
            _source_badge(source),
            institution,
            account,
            _money2(balance),
            _compact_time(as_of),
        )
        for owner, source, institution, account, balance, as_of in raw_account_rows
    ]
    holding_rows = []
    if _table_exists(cfg, "finance_holdings"):
        raw_holding_rows = _rows(
            cfg,
            """
            SELECT source, institution_name, account_name, COALESCE(ticker, security_name, security_id) AS holding,
                   quantity,
                   value,
                   as_of
            FROM finance_holdings fh
            WHERE as_of = (
              SELECT MAX(h2.as_of)
              FROM finance_holdings h2
              WHERE h2.finance_account_id = fh.finance_account_id
            )
            ORDER BY institution_name, account_name, COALESCE(value, 0) DESC
            LIMIT 80
            """,
        )
        holding_rows = [
            (
                _source_badge(source),
                institution,
                account,
                holding,
                f"{float(quantity or 0):,.4f}",
                _money2(value),
                _compact_time(as_of),
            )
            for source, institution, account, holding, quantity, value, as_of in raw_holding_rows
        ]
    return (
        "<h2>Investments</h2>"
        + _grouped_table(
            account_rows,
            ["Owner", "Source", "Institution", "Account", "Balance", "As Of"],
            group_index=2,
            group_label="Bank",
            item_label="accounts",
        )
        + "<h3>Latest Holdings</h3>"
        + _grouped_table(
            holding_rows,
            ["Source", "Institution", "Account", "Holding", "Quantity", "Value", "As Of"],
            group_index=1,
            group_label="Bank",
            item_label="holdings",
        )
    )


def _render_parent_draws(cfg) -> str:
    daily = _rows(
        cfg,
        """
        SELECT date, parent_draw
        FROM finance_daily_cashflow
        WHERE owner='all'
          AND parent_draw > 0
        ORDER BY date
        LIMIT 180
        """,
    )
    dates = [r[0] for r in daily]
    labels = [r[0][5:] for r in daily]
    draws = [-float(r[1] or 0) for r in daily]
    raw_recent = _rows(
        cfg,
        """
        SELECT date, owner, source, institution, account_name, COALESCE(merchant_name, name),
               amount, category
        FROM finance_parent_draws
        ORDER BY institution, date DESC
        LIMIT 80
        """,
    )
    recent = [
        (
            date_,
            _owner_badge(owner),
            _source_badge(source),
            institution,
            account,
            merchant,
            _money2(amount),
            category,
        )
        for date_, owner, source, institution, account, merchant, amount, category in raw_recent
    ]
    return (
        "<h2>Parent Account Draws</h2>"
        + agcharts.gain_loss_area_chart(
            labels,
            draws,
            height_px=220,
            show_every_nth_label=max(len(labels) // 8, 1),
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
        + '<p class="meta">Accounts with owner <code>parents</code> are excluded from personal net worth and tracked here.</p>'
        + _grouped_table(
            recent,
            ["Date", "Owner", "Source", "Institution", "Account", "Merchant", "Amount", "Category"],
            group_index=3,
            group_label="Bank",
            item_label="transactions",
        )
    )


def _owner_where(scope: str, *, alias: str = "") -> str:
    column = f"{alias}owner"
    if scope == "self":
        return f"LOWER(COALESCE({column}, 'self')) IN ('self', 'me', 'personal')"
    if scope == "parents":
        return f"LOWER(COALESCE({column}, 'self')) NOT IN ('self', 'me', 'personal')"
    return "1 = 1"


def _account_summary(cfg, scope: str) -> dict[str, float]:
    rows = _rows(
        cfg,
        f"""
        SELECT account_group, current_balance
        FROM finance_accounts
        WHERE {_owner_where(scope)}
        """,
    )
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
    for group, raw_balance in rows:
        balance = _coerce_float(raw_balance)
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


def _overview_card(title: str, summary: dict[str, float], *, parent: bool = False) -> str:
    headline = "Managed total" if parent else "Net worth"
    rows = [
        (headline, _money(summary["net_worth"])),
        ("Assets", _money(summary["assets"])),
        ("Debts", _money(summary["debts"])),
        ("Cash", _money(summary["cash"])),
        ("Investments", _money(summary["investments"])),
        ("Credit cards", _money(-summary["credit_card_debt"])),
        ("Accounts", str(int(summary["accounts"]))),
    ]
    attrs = ' data-finance-parent="1"' if parent else ""
    return (
        f'<section class="finance-overview-card"{attrs}>'
        f"<h3>{html.escape(title)}</h3>"
        + _metric_table(rows)
        + "</section>"
    )


def _render_dashboard_overview(cfg) -> str:
    self_summary = _account_summary(cfg, "self")
    parent_summary = _account_summary(cfg, "parents")
    return (
        '<div class="finance-overview-grid">'
        + _overview_card("Self", self_summary)
        + _overview_card("Parents", parent_summary, parent=True)
        + "</div>"
    )


def _cashflow_block(cfg, scope: str, title: str) -> str:
    rows = _rows(
        cfg,
        """
        SELECT date, income, spending, net, parent_draw, credit_card_payments, internal_transfers
        FROM finance_daily_cashflow
        WHERE owner=?
        ORDER BY date DESC
        LIMIT 180
        """,
        (scope,),
    )
    ordered = list(reversed(rows))
    dates = [r[0] for r in ordered]
    labels = [r[0][5:] for r in ordered]
    net = [float(r[3] or 0) for r in ordered]
    table_rows = [
        (r[0], _money(r[1]), _money(r[2]), _signed_money(r[3]), _money(r[4]), _money(r[5]), _money(r[6]))
        for r in rows
    ]
    chart = ""
    if rows:
        chart = agcharts.gain_loss_area_chart(
            labels,
            net,
            height_px=250,
            show_every_nth_label=max(len(labels) // 8, 1),
            value_attr="data-usd",
            month_markers=True,
            zoom_default_window=90,
            date_values=dates,
            aggregation=True,
            aggregation_default_mode="week",
            scale_default_mode="full",
        )
    attrs = ' data-finance-parent="1"' if scope == "parents" else ""
    return (
        f'<section class="finance-section"{attrs}><h3>{html.escape(title)}</h3>'
        + chart
        + _table(
            table_rows,
            ["Date", "Income", "Spending", "Net", "Parent Draw", "Card Payments", "Transfers"],
        )
        + "</section>"
    )


def _accounts_block(cfg, scope: str, title: str) -> str:
    raw_rows = _rows(
        cfg,
        f"""
        SELECT source, account_group, owner, institution_name, account_name, subtype,
               current_balance, iso_currency_code, as_of
        FROM finance_accounts
        WHERE {_owner_where(scope)}
          AND account_group != 'investments'
        ORDER BY institution_name, account_group, source, account_name
        """,
    )
    rows = [
        (
            _group_cell(group, subtype),
            institution,
            _account_cell(account, source),
            _money2(current),
            currency,
            _compact_time(as_of),
        )
        for source, group, owner, institution, account, subtype, current, currency, as_of in raw_rows
    ]
    attrs = ' data-finance-parent="1"' if scope == "parents" else ""
    return (
        f'<section class="finance-section"{attrs}><h3>{html.escape(title)}</h3>'
        + _grouped_table(
            rows,
            ["Group", "Institution", "Account", "Current", "Currency", "As Of"],
            group_index=1,
            group_label="Bank",
            item_label="accounts",
            class_name="finance-accounts-table",
        )
        + "</section>"
    )


def _investments_block(cfg, scope: str, title: str) -> str:
    raw_accounts = _rows(
        cfg,
        f"""
        SELECT source, owner, institution_name, account_name, current_balance,
               available_balance, as_of
        FROM finance_accounts
        WHERE account_group='investments'
          AND {_owner_where(scope)}
        ORDER BY institution_name, source, account_name
        """,
    )
    account_rows = [
        (
            institution,
            _account_cell(account, source),
            _balance_cell(balance, sublabel="cash", subvalue=available),
            _compact_time(as_of),
        )
        for source, owner, institution, account, balance, available, as_of in raw_accounts
    ]
    holding_rows = []
    allocation_items: list[tuple[str, float]] = []
    if _table_exists(cfg, "finance_holdings"):
        raw_holdings = _rows(
            cfg,
            f"""
            SELECT source, owner, institution_name, account_name,
                   COALESCE(ticker, security_name, security_id) AS holding,
                   quantity, value, as_of
            FROM finance_holdings fh
            WHERE {_owner_where(scope, alias='fh.')}
              AND as_of = (
                SELECT MAX(h2.as_of)
                FROM finance_holdings h2
                WHERE h2.finance_account_id = fh.finance_account_id
              )
            ORDER BY institution_name, account_name, COALESCE(value, 0) DESC
            LIMIT 300
            """,
        )
        allocation_items = _pie_items([(holding, value) for *_, holding, _quantity, value, _as_of in raw_holdings])
        holding_rows = [
            (
                institution,
            _account_cell(account, source),
                holding,
                f"{float(quantity or 0):,.4f}",
                _money2(value),
                _compact_time(as_of),
            )
            for source, owner, institution, account, holding, quantity, value, as_of in raw_holdings
        ]
    attrs = ' data-finance-parent="1"' if scope == "parents" else ""
    return (
        f'<section class="finance-section"{attrs}><h3>{html.escape(title)}</h3>'
        + "<h4>Accounts</h4>"
        + _grouped_table(
            account_rows,
            ["Institution", "Account", "Balance", "As Of"],
            group_index=0,
            group_label="Bank",
            item_label="accounts",
        )
        + "<h4>Holdings Allocation</h4>"
        + _pie_chart(allocation_items)
        + "<h4>Holdings</h4>"
        + _grouped_table(
            holding_rows,
            ["Institution", "Account", "Holding", "Quantity", "Value", "As Of"],
            group_index=0,
            group_label="Bank",
            item_label="holdings",
        )
        + "</section>"
    )


def _parent_draws_block(cfg) -> str:
    daily = _rows(
        cfg,
        """
        SELECT date, parent_draw
        FROM finance_daily_cashflow
        WHERE owner='all'
          AND parent_draw > 0
        ORDER BY date
        LIMIT 180
        """,
    )
    dates = [r[0] for r in daily]
    labels = [r[0][5:] for r in daily]
    draws = [-float(r[1] or 0) for r in daily]
    raw_recent = _rows(
        cfg,
        """
        SELECT date, source, owner, institution, account_name, COALESCE(merchant_name, name),
               amount, category
        FROM finance_parent_draws
        ORDER BY date DESC, institution, account_name
        LIMIT 300
        """,
    )
    recent = [
        (date_, institution, _account_cell(account, source), merchant, _money2(amount), category)
        for date_, source, owner, institution, account, merchant, amount, category in raw_recent
    ]
    chart = ""
    if daily:
        chart = agcharts.gain_loss_area_chart(
            labels,
            draws,
            height_px=220,
            show_every_nth_label=max(len(labels) // 8, 1),
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
    return (
        '<section class="finance-section"><h3>Parent Account Draws</h3>'
        + chart
        + '<p class="meta">Outflows from parent-managed accounts. Parent accounts stay out of personal net worth.</p>'
        + _grouped_table(
            recent,
            ["Date", "Institution", "Account", "Merchant", "Amount", "Category"],
            group_index=1,
            group_label="Bank",
            item_label="transactions",
        )
        + "</section>"
    )


def _render_dashboard(cfg) -> str:
    has_accounts = _rows(cfg, "SELECT 1 FROM finance_accounts LIMIT 1")
    if not has_accounts:
        return '<p class="meta">No combined finance data yet. Sync Plaid and/or Monarch, then sync finance.</p>'
    return (
        '<div class="finance-dashboard finance-self-only" data-finance-dashboard>'
        '<div class="finance-dashboard-controls">'
        '<label class="finance-toggle">'
        '<input type="checkbox" data-finance-self-only checked> '
        '<span>Only show self</span>'
        '</label>'
        '</div>'
        + _render_dashboard_overview(cfg)
        + '<h2 class="finance-dashboard-band">Self</h2>'
        + _cashflow_block(cfg, "self", "Self Cashflow")
        + _accounts_block(cfg, "self", "Self Accounts")
        + _investments_block(cfg, "self", "Self Investments")
        + _parent_draws_block(cfg)
        + '<h2 class="finance-dashboard-band" data-finance-parent="1">Parents</h2>'
        + _cashflow_block(cfg, "parents", "Parent Cashflow")
        + _accounts_block(cfg, "parents", "Parent Accounts")
        + _investments_block(cfg, "parents", "Parent Investments")
        + "</div>"
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "overview",
            "name": "Finance dashboard",
            "description": "Self and parent finances separated into overview, cashflow, accounts, investments, and parent draw sections.",
            "render": _render_dashboard,
        },
    ]
