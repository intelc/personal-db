import subprocess
import sys


def _init(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    return root


def test_app_available_and_install_reinstall(tmp_path):
    root = _init(tmp_path)

    available = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "app",
            "available",
        ],
        capture_output=True,
        text=True,
    )
    assert available.returncode == 0, available.stderr
    assert "finance" in available.stdout

    # Bundled apps are an explicit install catalog, not preinstalled runtime
    # surfaces. The normal list mirrors the dashboard/sidebar and must stay
    # empty until the user opts in.
    listed_before_install = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "app",
            "list",
        ],
        capture_output=True,
        text=True,
    )
    assert listed_before_install.returncode == 0, listed_before_install.stderr
    assert "No apps discovered" in listed_before_install.stdout

    installed = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "app",
            "install",
            "finance",
        ],
        capture_output=True,
        text=True,
    )
    assert installed.returncode == 0, installed.stderr
    app_dir = root / "apps" / "finance"
    assert (app_dir / "app.yaml").exists()
    assert (app_dir / "schema.sql").exists()
    assert (app_dir / "queries.sql").exists()
    import sqlite3

    con = sqlite3.connect(root / "db.sqlite")
    try:
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_finance_reviews'"
        ).fetchone() == (1,)
        assert con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_finance_burn_rules'"
        ).fetchone() == (1,)
    finally:
        con.close()

    extra = app_dir / "local_note.md"
    extra.write_text("keep")
    (app_dir / "views.py").write_text("# stale\n")
    reinstalled = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "app",
            "reinstall",
            "finance",
        ],
        capture_output=True,
        text=True,
    )
    assert reinstalled.returncode == 0, reinstalled.stderr
    assert extra.read_text() == "keep"
    assert "# stale" not in (app_dir / "views.py").read_text()

    listed = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "app",
            "list",
        ],
        capture_output=True,
        text=True,
    )
    assert listed.returncode == 0, listed.stderr
    assert "finance" in listed.stdout
    assert "installed" in listed.stdout
