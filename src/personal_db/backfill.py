"""Spawn a detached `personal-db backfill <tracker>` so setup can return
immediately while historical data populates in the background.

Used at the end of every successful tracker setup (CLI wizard + web wizard).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from personal_db.config import Config


def start_async(cfg: Config, tracker: str) -> Path:
    """Launch `personal-db backfill <tracker>` in a detached subprocess.

    Returns the log file path. The subprocess survives the parent exiting
    via start_new_session=True. Output is captured to
    `<root>/state/backfill_<tracker>.log` so the user can tail it later.
    """
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    log_path = cfg.state_dir / f"backfill_{tracker}.log"
    # Open the log file and pass to Popen. After Popen returns the child has
    # its own dup'd FD, so closing the parent's reference (when the `with`
    # exits) is safe.
    with open(log_path, "w") as log_fd:
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "personal_db.cli.main",
                "--root",
                str(cfg.root),
                "backfill",
                tracker,
            ],
            start_new_session=True,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
        )
    return log_path
