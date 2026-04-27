import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from personal_db.config import Config
from personal_db.oauth import save_token
from personal_db.sync import sync_one

FIXTURES = Path("tests/fixtures/whoop")


def _make_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _install_whoop(root: Path) -> Config:
    """Run CLI init + tracker install and return a Config pointing at root."""
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "whoop",
        ],
        check=True,
        capture_output=True,
    )
    cfg = Config(root=root)
    save_token(
        cfg,
        "whoop",
        {"access_token": "x", "refresh_token": "r", "expires_at": 9999999999},
    )
    return cfg


def _empty_page() -> dict:
    return {"records": [], "next_token": None}


def test_whoop_sync_inserts_cycles(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    cfg = _install_whoop(root)
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")

    cycles = json.loads((FIXTURES / "cycles.json").read_text())

    with patch("requests.get") as mock_get:
        # cycles page → recovery empty → sleep empty → workouts empty
        mock_get.side_effect = [
            _make_response({"records": cycles, "next_token": None}),
            _make_response(_empty_page()),
            _make_response(_empty_page()),
            _make_response(_empty_page()),
        ]
        sync_one(cfg, "whoop")

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_cycles").fetchone()[0] == len(cycles)
    # Verify new fields are captured
    row = con.execute(
        "SELECT timezone_offset, score_state, kilojoule, max_heart_rate "
        "FROM whoop_cycles WHERE id='12345'"
    ).fetchone()
    assert row == ("-05:00", "SCORED", 7500.0, 135)


def test_whoop_sync_inserts_recovery(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    cfg = _install_whoop(root)
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")

    cycles = json.loads((FIXTURES / "cycles.json").read_text())
    recovery = json.loads((FIXTURES / "recovery.json").read_text())

    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            _make_response({"records": cycles, "next_token": None}),
            _make_response({"records": recovery, "next_token": None}),
            _make_response(_empty_page()),
            _make_response(_empty_page()),
        ]
        sync_one(cfg, "whoop")

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_recovery").fetchone()[0] == len(recovery)

    # Verify denormalized start is populated from whoop_cycles
    row = con.execute(
        "SELECT cycle_id, start, recovery_score, hrv_rmssd_milli "
        "FROM whoop_recovery WHERE cycle_id='12346'"
    ).fetchone()
    assert row[0] == "12346"
    assert row[1] == "2026-04-24T07:00:00Z"  # denormalized from cycle 12346
    assert row[2] == 44
    assert abs(row[3] - 31.81) < 0.001


def test_whoop_sync_inserts_sleep(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    cfg = _install_whoop(root)
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")

    sleep = json.loads((FIXTURES / "sleep.json").read_text())

    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            _make_response(_empty_page()),  # cycles
            _make_response(_empty_page()),  # recovery
            _make_response({"records": sleep, "next_token": None}),
            _make_response(_empty_page()),  # workouts
        ]
        sync_one(cfg, "whoop")

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_sleep").fetchone()[0] == len(sleep)

    # Verify stage breakdown and nap flag
    nap_row = con.execute(
        "SELECT nap, total_rem_sleep_milli, sleep_efficiency_pct FROM whoop_sleep WHERE id='93004'"
    ).fetchone()
    assert nap_row[0] == 1  # nap=true → 1
    assert nap_row[1] == 0
    assert abs(nap_row[2] - 83.3) < 0.01

    main_row = con.execute(
        "SELECT nap, sleep_performance_pct FROM whoop_sleep WHERE id='93002'"
    ).fetchone()
    assert main_row[0] == 0
    assert abs(main_row[1] - 98.0) < 0.01


def test_whoop_sync_inserts_workouts(tmp_path, monkeypatch):
    root = tmp_path / "personal_db"
    cfg = _install_whoop(root)
    monkeypatch.setenv("WHOOP_CLIENT_ID", "fake_id")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "fake_secret")

    workouts = json.loads((FIXTURES / "workouts.json").read_text())

    with patch("requests.get") as mock_get:
        mock_get.side_effect = [
            _make_response(_empty_page()),  # cycles
            _make_response(_empty_page()),  # recovery
            _make_response(_empty_page()),  # sleep
            _make_response({"records": workouts, "next_token": None}),
        ]
        sync_one(cfg, "whoop")

    con = sqlite3.connect(root / "db.sqlite")
    assert con.execute("SELECT COUNT(*) FROM whoop_workouts").fetchone()[0] == len(workouts)

    row = con.execute(
        "SELECT sport_id, strain, max_heart_rate, kilojoule, "
        "zone_three_milli, zone_five_milli "
        "FROM whoop_workouts WHERE id='1043'"
    ).fetchone()
    assert row[0] == 1
    assert abs(row[1] - 8.25) < 0.001
    assert row[2] == 146
    assert abs(row[3] - 1791.7) < 0.01
    assert row[4] == 779465
    assert row[5] == 0
