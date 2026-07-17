"""Test helper: enable the agent terminal in a scratch root's config.yaml.

Phase 2c makes the agent terminal (routes/agent.py's session routes + the
terminal websocket) 403 unless `config.yaml: agent_terminal.enabled` is set.
Tests that exercise session creation/the websocket need to opt in explicitly,
the same way a real user would.
"""

from __future__ import annotations

import yaml

from personal_db.core.config import Config


def enable_agent_terminal(cfg: Config, *, auto_approve: bool = False) -> None:
    path = cfg.root / "config.yaml"
    data = yaml.safe_load(path.read_text()) if path.is_file() else None
    if not isinstance(data, dict):
        data = {}
    data["agent_terminal"] = {"enabled": True, "auto_approve": auto_approve}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data))
