from __future__ import annotations


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
