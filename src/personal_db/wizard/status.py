"""Status icon computation + persistence for the wizard menu.

Icons:
  —  no setup_steps in manifest (e.g. habits)
  ✗  at least one setup_step's prerequisite is missing
  !  all prerequisites met but last recorded test sync failed
  ✓  all prerequisites met AND last recorded test sync succeeded
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from personal_db.config import Config
from personal_db.manifest import (
    EnvVarStep,
    FdaCheckStep,
    OAuthStep,
    load_manifest,
)
from personal_db.permissions import probe_sqlite_access


def _status_path(cfg: Config) -> Path:
    return cfg.state_dir / "wizard_status.json"


def read_status(cfg: Config) -> dict[str, dict[str, Any]]:
    p = _status_path(cfg)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write_status(cfg: Config, tracker: str, *, success: bool, detail: str) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    data = read_status(cfg)
    data[tracker] = {
        "success": success,
        "detail": detail,
        "ts": datetime.now(UTC).isoformat(),
    }
    _status_path(cfg).write_text(json.dumps(data, indent=2))


def compute_icon(cfg: Config, tracker: str) -> str:
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    if not manifest.setup_steps:
        return "—"
    if not _all_prereqs_met(cfg, manifest.setup_steps):
        return "✗"
    status = read_status(cfg).get(tracker)
    if status is None:
        return "✗"  # no recorded test sync yet
    return "✓" if status.get("success") else "!"


def _all_prereqs_met(cfg: Config, steps) -> bool:
    """Cheap configured-or-not check per step type. No network, no FDA prompts."""
    for step in steps:
        if isinstance(step, EnvVarStep):
            if not os.environ.get(step.name):
                return False
        elif isinstance(step, OAuthStep):
            token_path = cfg.state_dir / "oauth" / f"{step.provider}.json"
            if not token_path.exists():
                return False
        elif isinstance(step, FdaCheckStep):
            r = probe_sqlite_access(Path(step.probe_path).expanduser())
            if not r.granted:
                return False
        # InstructionsStep and CommandTestStep have no prerequisites — they
        # only "fail" by being explicitly run and returning Failed. They're
        # treated as always-met for the purpose of the cheap icon probe.
    return True
