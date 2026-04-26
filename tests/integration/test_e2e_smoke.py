import subprocess
import sys


def _run(*args, root=None):
    cmd = [sys.executable, "-m", "personal_db.cli.main"]
    if root:
        cmd += ["--root", str(root)]
    cmd += list(args)
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, f"{' '.join(cmd)}\nSTDOUT:{r.stdout}\nSTDERR:{r.stderr}"
    return r


def test_e2e_init_install_log_query(tmp_path):
    root = tmp_path / "personal_db"

    _run("init", root=root)
    _run("tracker", "install", "habits", root=root)
    _run("sync", "habits", root=root)  # applies schema
    _run("log", "habits", "name=meditate", "value=1", "ts=2026-04-25T08:00", root=root)
    _run("log", "habits", "name=meditate", "value=1", "ts=2026-04-26T08:00", root=root)

    # Trackers list shows habits
    r = _run("tracker", "list", root=root)
    assert "habits" in r.stdout

    # Direct SQL via MCP query path
    from personal_db.config import Config
    from personal_db.mcp_server.tools import get_series, list_trackers, query

    cfg = Config(root=root)
    rows = query(cfg, "SELECT name, COUNT(*) AS n FROM habits GROUP BY name")
    assert rows == [{"name": "meditate", "n": 2}]

    series = get_series(
        cfg,
        tracker="habits",
        range_="2026-04-25/2026-04-27",
        granularity="day",
        agg="count",
    )
    by_day = {row["bucket"]: row["value"] for row in series}
    assert by_day["2026-04-25"] == 1
    assert by_day["2026-04-26"] == 1

    # list_trackers
    assert any(t["name"] == "habits" for t in list_trackers(cfg))
