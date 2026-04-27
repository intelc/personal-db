"""Smoke tests for `personal-db setup` (the bundled top-level command)."""

from __future__ import annotations

import subprocess
import sys


def test_setup_help_is_registered():
    """`personal-db setup --help` exits 0 and references the wizard mode."""
    r = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "setup", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "configuration" in r.stdout.lower() or "setup" in r.stdout.lower()


def test_top_level_help_lists_setup():
    """`personal-db --help` lists the setup command alongside init."""
    r = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    assert "setup" in r.stdout
    assert "init" in r.stdout
