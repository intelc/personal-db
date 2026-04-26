"""Tests for the FastAPI dashboard endpoints."""

import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient

from personal_db.config import Config
from personal_db.ui.server import build_app


def _setup(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True, capture_output=True,
    )
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root),
         "tracker", "install", "life_context"],
        check=True, capture_output=True,
    )
    return Config(root=root)


def test_today_page_renders_with_empty_db(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "personal_db" in r.text
    assert "TODAY" not in r.text  # h1 is local weekday, not literal "TODAY"
    # Should still render section headers even with no data
    assert "LIFE_CONTEXT" in r.text
    assert "HEALTH" in r.text


def test_today_page_renders_categories(tmp_path):
    cfg = _setup(tmp_path)
    # Seed daily_time_accounting with a row for today
    from datetime import datetime
    today = datetime.now().astimezone().date().isoformat()
    con = sqlite3.connect(cfg.db_path)
    con.executescript(
        "CREATE TABLE IF NOT EXISTS daily_time_accounting "
        "(date TEXT, category TEXT, hours REAL, PRIMARY KEY (date, category))"
    )
    con.execute(
        "INSERT INTO daily_time_accounting VALUES (?, 'work', 4.5)", (today,)
    )
    con.execute(
        "INSERT INTO daily_time_accounting VALUES (?, 'sleep', 8.0)", (today,)
    )
    con.commit()
    con.close()
    client = TestClient(build_app(cfg))
    r = client.get("/")
    assert r.status_code == 200
    assert "work" in r.text
    assert "sleep" in r.text
    assert "4.5h" in r.text
    assert "8.0h" in r.text


def test_log_life_context_via_post(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.post(
        "/log_life_context",
        data={
            "start_date": "2026-04-13",
            "end_date": "2026-04-15",
            "state": "sick",
            "note": "fever",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/"
    # Verify rows were inserted (3 days = 3 rows)
    con = sqlite3.connect(cfg.db_path)
    n = con.execute("SELECT count(*) FROM life_context WHERE state='sick'").fetchone()[0]
    assert n == 3
    con.close()


def test_log_life_context_rejects_empty(tmp_path):
    """Empty state+note should fail validation. TestClient propagates the
    underlying ValueError; in production it would surface as a 500."""
    import pytest

    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg), raise_server_exceptions=True)
    with pytest.raises(ValueError, match="at least one of"):
        client.post(
            "/log_life_context",
            data={"start_date": "2026-04-13"},
        )


def test_static_css_served(tmp_path):
    cfg = _setup(tmp_path)
    client = TestClient(build_app(cfg))
    r = client.get("/static/style.css")
    assert r.status_code == 200
    # Sanity: pixel aesthetic markers
    assert "monospace" in r.text
    assert "#000" in r.text or "#fff" in r.text
