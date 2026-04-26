"""FastAPI dashboard for personal_db.

Renders B&W pixel-aesthetic HTML pages backed by direct SQL queries against
db.sqlite. Designed to run as a localhost-only background server, launched
by the rumps menu bar app.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from personal_db.config import Config
from personal_db.data_horizon import get_all as get_all_horizons
from personal_db.mcp_server.tools import log_life_context

_HERE = Path(__file__).parent

# Category palette — desaturated pixel-art tones, used only inside the data
# visualization (the rest of the page chrome stays pure B&W per the spec).
_CAT_COLORS = {
    "sleep": "#1a3a5e",
    "workout": "#cc6600",
    "work": "#2e5c34",
    "communication": "#3a7a7a",
    "leisure": "#a04a6a",
    "other_screen": "#666666",
    "_unaccounted": "#cccccc",
    "_no_data": "url(#hatch)",  # rendered as a CSS pattern in the template
}


def _local_today() -> date:
    return datetime.now().astimezone().date()


def _query_today_breakdown(con: sqlite3.Connection, day: date) -> list[dict]:
    """Returns [{category, hours, color}] for a given local date, ordered for stack.

    Returns empty list if the table doesn't exist yet (fresh install before the
    first daily_time_accounting sync).
    """
    try:
        rows = con.execute(
            "SELECT category, hours FROM daily_time_accounting "
            "WHERE date = ? ORDER BY hours DESC",
            (day.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    out = []
    for cat, hours in rows:
        if hours <= 0.01:
            continue
        out.append(
            {
                "category": cat,
                "hours": round(hours, 2),
                "color": _CAT_COLORS.get(cat, "#888"),
            }
        )
    return out


def _query_recent_breakdown(con: sqlite3.Connection, days: int = 7) -> list[dict]:
    """Last N days, each as [{date, segments: [{category, hours, color, pct}]}]."""
    today = _local_today()
    out = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        breakdown = _query_today_breakdown(con, d)
        total = sum(s["hours"] for s in breakdown)
        for s in breakdown:
            s["pct"] = (s["hours"] / 24.0) * 100.0 if total else 0.0
        out.append({"date": d.isoformat(), "weekday": d.strftime("%a"), "segments": breakdown})
    return out


def _query_today_life_context(con: sqlite3.Connection, day: date) -> list[dict]:
    try:
        rows = con.execute(
            "SELECT id, state, note, logged_at FROM life_context "
            "WHERE date = ? ORDER BY id ASC",
            (day.isoformat(),),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": r[0], "state": r[1], "note": r[2], "logged_at": r[3]} for r in rows]


def _query_recent_life_context(con: sqlite3.Connection, days: int = 14) -> list[dict]:
    today = _local_today()
    cutoff = (today - timedelta(days=days - 1)).isoformat()
    try:
        rows = con.execute(
            "SELECT date, state, note FROM life_context WHERE date >= ? "
            "ORDER BY date DESC, id DESC",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"date": r[0], "state": r[1], "note": r[2]} for r in rows]


def _last_run_summary(cfg: Config) -> dict:
    """Read the framework's last_run.json plus tracker_horizons; build a health snapshot."""
    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}
    horizons = get_all_horizons(cfg)
    now = datetime.now(timezone.utc)
    out = []
    for tracker, ts in sorted(last_runs.items()):
        try:
            age = now - datetime.fromisoformat(ts)
            age_str = _humanize_age(age)
        except ValueError:
            age_str = "?"
        out.append(
            {
                "name": tracker,
                "last_run": ts,
                "age": age_str,
                "horizon": horizons.get(tracker),
            }
        )
    return {"trackers": out}


def _humanize_age(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def build_app(cfg: Config) -> FastAPI:
    app = FastAPI(title="personal_db", openapi_url=None, docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(_HERE / "templates"))
    app.mount("/static", StaticFiles(directory=str(_HERE / "static")), name="static")

    def _connect() -> sqlite3.Connection:
        return sqlite3.connect(cfg.db_path)

    @app.get("/", response_class=HTMLResponse)
    async def today(request: Request):
        today_d = _local_today()
        with _connect() as con:
            breakdown = _query_today_breakdown(con, today_d)
            recent = _query_recent_breakdown(con, days=7)
            today_lc = _query_today_life_context(con, today_d)
            recent_lc = _query_recent_life_context(con, days=14)
        total_today = round(sum(s["hours"] for s in breakdown), 1)
        return templates.TemplateResponse(
            request=request,
            name="today.html",
            context={
                "today": today_d.isoformat(),
                "weekday": today_d.strftime("%A"),
                "breakdown": breakdown,
                "total_today": total_today,
                "recent": recent,
                "today_lc": today_lc,
                "recent_lc": recent_lc,
                "health": _last_run_summary(cfg),
                "states": ["well", "sick", "recovering", "traveling",
                           "focused", "distracted", "system_event"],
            },
        )

    @app.post("/log_life_context")
    async def post_life_context(
        start_date: str = Form(...),
        end_date: str = Form(""),
        state: str = Form(""),
        note: str = Form(""),
    ):
        # Coerce empties to None to satisfy the tool's "at least one of" check
        log_life_context(
            cfg,
            start_date=start_date,
            end_date=end_date or None,
            state=state or None,
            note=note or None,
        )
        return RedirectResponse(url="/", status_code=303)

    return app
