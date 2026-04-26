import subprocess
import sys

import yaml


def _init(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    return root


def test_tracker_new_scaffolds_files(tmp_path):
    root = _init(tmp_path)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "new",
            "my_metric",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    d = root / "trackers" / "my_metric"
    assert (d / "manifest.yaml").exists()
    assert (d / "schema.sql").exists()
    assert (d / "ingest.py").exists()
    m = yaml.safe_load((d / "manifest.yaml").read_text())
    assert m["name"] == "my_metric"


def test_tracker_list_empty(tmp_path):
    root = _init(tmp_path)
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "list",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "No trackers" in r.stdout
