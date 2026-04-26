"""Built-in dashboard visualizations that don't belong to any single tracker.

Use the `_builtin:` slug prefix.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from html import escape

from personal_db.config import Config
from personal_db.data_horizon import get_all as _get_all_horizons


def _humanize_age(d: timedelta) -> str:
    s = int(d.total_seconds())
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def render_health(cfg: Config) -> str:
    last_run_path = cfg.state_dir / "last_run.json"
    last_runs: dict[str, str] = {}
    if last_run_path.exists():
        try:
            last_runs = json.loads(last_run_path.read_text())
        except json.JSONDecodeError:
            last_runs = {}
    horizons = _get_all_horizons(cfg)
    if not last_runs:
        return '<p class="meta">no syncs recorded yet</p>'
    now = datetime.now(timezone.utc)
    rows = []
    for tracker, ts in sorted(last_runs.items()):
        try:
            age = _humanize_age(now - datetime.fromisoformat(ts))
        except ValueError:
            age = "?"
        horizon = horizons.get(tracker)
        horizon_cell = escape(horizon[:10]) if horizon else "—"
        # Tracker name links to its dedicated page so users can click straight
        # from "this looks stale" to the tracker's recent rows / viz.
        name_link = f'<a href="/t/{escape(tracker)}">{escape(tracker)}</a>'
        rows.append(
            f"<tr><td>{name_link}</td>"
            f"<td>{escape(age)}</td>"
            f'<td class="meta">{horizon_cell}</td></tr>'
        )
    return (
        '<table class="health">'
        "<thead><tr><th>tracker</th><th>last sync</th><th>data horizon</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "health",
            "name": "Tracker Health",
            "description": "Last sync age and recorded data horizon for every installed tracker.",
            "render": render_health,
        },
    ]
