from __future__ import annotations

import html

from personal_db.db import connect
from personal_db.ui.charts import horizontal_bars


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


def _render_accounts(cfg) -> str:
    rows = _rows(
        cfg,
        """
        SELECT COALESCE(l.account_group, 'other') AS account_group,
               CASE WHEN COALESCE(e.export_enabled, 0) = 1 THEN 'yes' ELSE 'no' END AS export,
               COALESCE(l.owner, 'self') AS owner,
               a.institution_name,
               a.display_name,
               printf('$%,.2f', COALESCE(a.current_balance, a.display_balance, 0)),
               a.type_display,
               a.subtype_display,
               a.display_last_updated_at
        FROM monarch_accounts a
        LEFT JOIN monarch_account_exports e ON e.account_id = a.account_id
        LEFT JOIN monarch_account_labels l ON l.account_id = a.account_id
        ORDER BY account_group, export DESC, owner, institution_name, display_name
        """,
    )
    return "<h2>Monarch Accounts</h2>" + _table(
        rows,
        ["Group", "Export", "Owner", "Institution", "Account", "Balance", "Type", "Subtype", "Updated"],
    )


def _render_overview(cfg) -> str:
    group_rows = _rows(
        cfg,
        """
        SELECT COALESCE(l.account_group, 'other') AS account_group,
               SUM(COALESCE(a.current_balance, a.display_balance, 0)) AS value
        FROM monarch_accounts a
        LEFT JOIN monarch_account_exports e ON e.account_id = a.account_id
        LEFT JOIN monarch_account_labels l ON l.account_id = a.account_id
        WHERE COALESCE(e.export_enabled, 0) = 1
        GROUP BY account_group
        ORDER BY ABS(value) DESC
        """,
    )
    recent = _rows(
        cfg,
        """
        SELECT t.date, a.institution_name, t.account_name, t.merchant_name,
               printf('$%,.2f', COALESCE(t.amount, 0)), t.category_name, t.pending
        FROM monarch_transactions t
        LEFT JOIN monarch_accounts a ON a.account_id = t.account_id
        ORDER BY t.date DESC
        LIMIT 50
        """,
    )
    chart = horizontal_bars(
        [(r[0], abs(float(r[1] or 0))) for r in group_rows],
        value_fmt=_money,
        color="var(--chart-accent)",
    )
    return (
        "<h2>Monarch Overview</h2>"
        + "<h3>Selected Account Groups</h3>"
        + chart
        + "<h3>Recent Transactions</h3>"
        + _table(recent, ["Date", "Institution", "Account", "Merchant", "Amount", "Category", "Pending"])
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "monarch:overview",
            "name": "Monarch overview",
            "description": "Selected Monarch accounts and recent transactions.",
            "render": _render_overview,
        },
        {
            "slug": "monarch:accounts",
            "name": "Monarch accounts",
            "description": "Monarch accounts and finance export settings.",
            "render": _render_accounts,
        },
    ]
