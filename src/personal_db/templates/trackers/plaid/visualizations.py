from __future__ import annotations

import html
from datetime import date, timedelta

from personal_db.db import connect
from personal_db.ui.charts import horizontal_bars, line_chart, multi_line_chart


def _rows(cfg, sql: str, params: tuple = ()) -> list[tuple]:
    con = connect(cfg.db_path, read_only=True)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def _table(rows: list, headers: list[str]) -> str:
    if not rows:
        return '<p class="meta">no data</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    body = []
    for row in rows:
        cells = "".join("<td>{}</td>".format(html.escape("" if v is None else str(v))) for v in row)
        body.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _money(value) -> str:
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        return "$0"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


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
    return f'<table class="kv"><tbody>{rows}</tbody></table>'


def _owner_options(cfg) -> list[str]:
    rows = _rows(
        cfg,
        """
        SELECT owner FROM plaid_daily_net_worth
        UNION
        SELECT owner FROM plaid_daily_cashflow
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
        FROM plaid_daily_net_worth
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
            "<h2>Plaid Finance Overview</h2>"
            '<p class="meta">No transformed Plaid finance data yet. Run a Plaid sync first.</p>'
        )
    latest_date, cash, investments, credit_debt, other, assets, debts, net_worth = latest
    net_rows = _rows(
        cfg,
        """
        SELECT date, net_worth
        FROM plaid_daily_net_worth
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
    cashflow = _rows(
        cfg,
        """
        SELECT date, income, spending, net
        FROM plaid_daily_cashflow
        WHERE owner='all'
          AND date >= ?
        ORDER BY date
        """,
        ((date.today() - timedelta(days=90)).isoformat(),),
    )
    cf_labels = [r[0][5:] for r in cashflow]
    income = [float(r[1] or 0) for r in cashflow]
    spending = [float(r[2] or 0) for r in cashflow]
    net = [float(r[3] or 0) for r in cashflow]
    return (
        "<h2>Plaid Finance Overview</h2>"
        + _metric_table(
            [
                ("As of", str(latest_date)),
                ("Net worth", _money(net_worth)),
                ("Assets", _money(assets)),
                ("Debts", _money(debts)),
                ("Cash/checking", _money(cash)),
                ("Investments", _money(investments)),
                ("Credit cards", _money(-float(credit_debt or 0))),
            ]
        )
        + "<h3>Net Worth</h3>"
        + line_chart(
            list(zip(labels, values, strict=False)),
            color="var(--chart-fg)",
            height_px=180,
            show_every_nth_label=max(len(labels) // 8, 1),
            value_attr="data-usd",
        )
        + "<h3>Account Groups</h3>"
        + horizontal_bars(
            [(label, abs(value)) for label, value in account_breakdown if value],
            value_fmt=_money,
            color="var(--chart-accent)",
        )
        + "<h3>Daily Cashflow</h3>"
        + multi_line_chart(
            cf_labels,
            [
                ("Income", income, "var(--chart-green)"),
                ("Spending", spending, "var(--chart-red)"),
                ("Net", net, "var(--chart-fg)", {"width": 2.2}),
            ],
            height_px=180,
            show_every_nth_label=max(len(cf_labels) // 8, 1),
            value_attr="data-usd",
        )
        + '<p class="meta">Credit-card payments and internal transfers are excluded from income, spending, and net.</p>'
    )


def _render_accounts(cfg) -> str:
    rows = _rows(
        cfg,
        """
        SELECT COALESCE(l.account_group, 'other') AS account_group,
               COALESCE(l.owner, 'self') AS owner,
               a.institution_name,
               COALESCE(l.label, a.official_name, a.name) AS account,
               a.subtype,
               printf('$%,.2f', COALESCE(a.current_balance, 0)),
               printf('$%,.2f', COALESCE(a.available_balance, 0)),
               a.iso_currency_code,
               a.balance_as_of
        FROM plaid_accounts a
        LEFT JOIN plaid_account_labels l ON l.account_id = a.account_id
        ORDER BY account_group, owner, institution_name, account
        """,
    )
    return "<h2>Plaid Source Accounts</h2>" + _table(
        rows,
        [
            "Group",
            "Owner",
            "Institution",
            "Account",
            "Subtype",
            "Current",
            "Available",
            "Currency",
            "As Of",
        ],
    )


def _render_cashflow(cfg) -> str:
    rows = _rows(
        cfg,
        """
        SELECT date, income, spending, net, parent_draw, credit_card_payments, internal_transfers
        FROM plaid_daily_cashflow
        WHERE owner='all'
        ORDER BY date DESC
        LIMIT 120
        """,
    )
    ordered = list(reversed(rows))
    labels = [r[0][5:] for r in ordered]
    income = [float(r[1] or 0) for r in ordered]
    spending = [float(r[2] or 0) for r in ordered]
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
        + multi_line_chart(
            labels,
            [
                ("Income", income, "var(--chart-green)"),
                ("Spending", spending, "var(--chart-red)"),
                ("Net", net, "var(--chart-fg)", {"width": 2.2}),
            ],
            height_px=210,
            show_every_nth_label=max(len(labels) // 10, 1),
            value_attr="data-usd",
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
            latest_rows.append((owner, _money(latest[7]), _money(latest[1]), _money(latest[2]), _money(latest[3])))
    rows = _rows(
        cfg,
        """
        SELECT date, cash, investments, -credit_card_debt AS cards, net_worth
        FROM plaid_daily_net_worth
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
        + multi_line_chart(
            labels,
            [
                ("Net worth", net, "var(--chart-fg)", {"width": 2.4}),
                ("Investments", investments, "var(--chart-accent)"),
                ("Cash", cash, "var(--chart-green)"),
                ("Credit cards", cards, "var(--chart-red)"),
            ],
            height_px=220,
            show_every_nth_label=max(len(labels) // 10, 1),
            value_attr="data-usd",
        )
        + _table(latest_rows, ["Owner", "Net Worth", "Cash", "Investments", "Credit Card Debt"])
    )


def _render_investments(cfg) -> str:
    account_rows = _rows(
        cfg,
        """
        SELECT COALESCE(l.owner, 'self') AS owner,
               a.institution_name,
               COALESCE(l.label, a.official_name, a.name) AS account,
               printf('$%,.2f', COALESCE(a.current_balance, 0)) AS balance,
               a.balance_as_of
        FROM plaid_accounts a
        LEFT JOIN plaid_account_labels l ON l.account_id = a.account_id
        WHERE COALESCE(l.account_group, a.type) = 'investments'
           OR a.type = 'investment'
        ORDER BY COALESCE(a.current_balance, 0) DESC
        """,
    )
    holding_rows = _rows(
        cfg,
        """
        SELECT a.institution_name, a.name, COALESCE(s.ticker_symbol, s.name, h.security_id) AS holding,
               printf('%.4f', COALESCE(h.quantity, 0)),
               printf('$%,.2f', COALESCE(h.institution_value, 0)),
               h.as_of
        FROM plaid_investment_holdings h
        LEFT JOIN plaid_accounts a ON a.account_id = h.account_id
        LEFT JOIN plaid_investment_securities s ON s.security_id = h.security_id
        WHERE h.as_of = (SELECT MAX(as_of) FROM plaid_investment_holdings)
        ORDER BY COALESCE(h.institution_value, 0) DESC
        LIMIT 80
        """,
    )
    return (
        "<h2>Plaid Source Investments</h2>"
        + _table(account_rows, ["Owner", "Institution", "Account", "Balance", "As Of"])
        + "<h3>Latest Holdings</h3>"
        + _table(holding_rows, ["Institution", "Account", "Holding", "Quantity", "Value", "As Of"])
    )


def _render_parent_draws(cfg) -> str:
    daily = _rows(
        cfg,
        """
        SELECT date, parent_draw
        FROM plaid_daily_cashflow
        WHERE owner='all'
          AND parent_draw > 0
        ORDER BY date
        LIMIT 180
        """,
    )
    labels = [r[0][5:] for r in daily]
    draws = [float(r[1] or 0) for r in daily]
    recent = _rows(
        cfg,
        """
        SELECT date, owner, institution, account_name, COALESCE(merchant_name, name), printf('$%,.2f', amount), category
        FROM plaid_parent_draws
        ORDER BY date DESC
        LIMIT 80
        """,
    )
    return (
        "<h2>Parent Account Draws</h2>"
        + line_chart(
            list(zip(labels, draws, strict=False)),
            color="#7a3e9d",
            height_px=180,
            show_every_nth_label=max(len(labels) // 8, 1),
            value_attr="data-usd",
        )
        + '<p class="meta">Set <code>owner: parents</code> in <code>account_labels.yaml</code> to classify accounts here.</p>'
        + _table(recent, ["Date", "Owner", "Institution", "Account", "Merchant", "Amount", "Category"])
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "plaid:accounts",
            "name": "Plaid source accounts",
            "description": "Plaid-linked accounts and labels before combined finance transforms.",
            "render": _render_accounts,
        },
        {
            "slug": "plaid:investments",
            "name": "Plaid source investments",
            "description": "Plaid investment accounts and latest Plaid holdings.",
            "render": _render_investments,
        },
    ]
