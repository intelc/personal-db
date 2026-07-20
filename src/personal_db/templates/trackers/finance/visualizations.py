from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta


def _abbrev_money(value: float) -> str:
    """Round/abbreviate to a short dashboard-tile string, e.g. -$1.2M, $203k, $840."""
    v = float(value or 0)
    sign = "-" if v < 0 else ""
    v = abs(v)
    if v >= 1_000_000:
        return f"{sign}${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"{sign}${v / 1_000:.0f}k"
    return f"{sign}${v:.0f}"


def metrics(cfg) -> list[dict]:
    """Dashboard tile metrics: total net worth (vs 30 days ago) and cash on
    hand, sourced from the finance app's daily net-worth rollup
    (finance_daily_net_worth, owner='all' combines every labeled owner)."""
    try:
        con = sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return []
    out: list[dict] = []
    try:
        latest = con.execute(
            "SELECT date, net_worth, cash FROM finance_daily_net_worth "
            "WHERE owner = 'all' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if not latest:
            return []
        latest_date, net_worth, cash = latest
        cutoff = (datetime.now() - timedelta(days=30)).date().isoformat()
        prior = con.execute(
            "SELECT net_worth FROM finance_daily_net_worth "
            "WHERE owner = 'all' AND date <= ? ORDER BY date DESC LIMIT 1",
            (cutoff,),
        ).fetchone()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()

    detail = None
    try:
        d = date.fromisoformat(str(latest_date)[:10])
        if d != datetime.now().date():
            detail = f"as of {d.isoformat()}"
    except ValueError:
        pass

    delta = None
    good = None
    if prior and prior[0]:
        prior_net_worth = prior[0]
        diff = net_worth - prior_net_worth
        if abs(diff) >= 1:
            pct = (diff / abs(prior_net_worth) * 100) if prior_net_worth else None
            if pct is not None:
                sign = "+" if pct >= 0 else ""
                delta = f"{sign}{pct:.0f}% vs 30d ago"
            good = True if diff > 0 else (False if diff < 0 else None)

    out.append(
        {
            "label": "Net worth",
            "value": _abbrev_money(net_worth),
            "detail": detail,
            "delta": delta,
            "good": good,
        }
    )
    out.append(
        {
            "label": "Cash",
            "value": _abbrev_money(cash),
            "detail": detail,
            "delta": None,
            "good": None,
        }
    )
    return out


def _render_finance_app_redirect(_cfg) -> str:
    return (
        '<p class="notice notice-info">'
        'Finance visualizations now live in the Finance app. '
        '<a href="/a/finance">Open Finance</a>.'
        "</p>"
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "overview",
            "name": "Finance app",
            "description": "Finance charts, tables, and review workflows have moved to /a/finance.",
            "render": _render_finance_app_redirect,
        },
    ]
