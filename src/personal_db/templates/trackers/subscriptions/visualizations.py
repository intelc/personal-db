"""Visualizations for subscription utilization."""

from __future__ import annotations

from personal_db.config import Config


def render_app_link(cfg: Config) -> str:
    return '<p><a class="button" href="/a/subscriptions">Open Subscriptions app</a></p>'


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "subscriptions:app",
            "name": "Subscriptions app",
            "description": "Subscription charges and utilization evidence",
            "render": render_app_link,
        }
    ]
