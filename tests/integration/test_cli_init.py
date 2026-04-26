import subprocess
import sys


def test_pdb_init_creates_root(tmp_path):
    root = tmp_path / "personal_db"
    result = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (root / "config.yaml").exists()
    assert (root / "trackers").exists()
    assert (root / "entities" / "people.yaml").exists()
    assert (root / "entities" / "topics.yaml").exists()
    assert (root / "notes").exists()
    assert (root / "state").exists()
    assert (root / "db.sqlite").exists()
