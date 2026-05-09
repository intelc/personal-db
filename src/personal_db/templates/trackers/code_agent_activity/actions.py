"""User-initiated actions for the code_agent_activity tracker.

Exposed handlers (called via the daemon's POST /api/trackers/{name}/actions/{action}
in upcoming Task 8):

  install_hooks(cfg)   — write our hooks block into ~/.claude/settings.json
  uninstall_hooks(cfg) — remove only entries we tagged with _personal_db_managed
  verify_hooks(cfg)    — report whether our hooks are present
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

# Each entry is one Claude Code hook command we manage. async: true keeps
# the writer off Claude Code's critical path.
_HOOK_EVENTS = ("SessionStart", "UserPromptSubmit", "Stop", "SessionEnd", "PreToolUse", "PostToolUse")
_MANAGED_KEY = "_personal_db_managed"


def _resolve_hook_command(cfg) -> str:
    explicit = getattr(cfg, "hook_command", None)
    if explicit:
        return explicit
    bin_path = shutil.which("personal-db")
    if bin_path:
        return f"{bin_path} code-agent-hook-write"
    return f"{sys.executable} -m personal_db code-agent-hook-write"


def _settings_path(cfg) -> Path:
    explicit = getattr(cfg, "claude_settings_path", None)
    if explicit:
        return Path(explicit)
    return Path("~/.claude/settings.json").expanduser()


def _load_settings(path: Path) -> dict | None:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return None


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)


def _managed_entry(command: str) -> dict:
    return {"hooks": [{"type": "command", "command": command, "async": True, _MANAGED_KEY: True}]}


def install_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"ok": False, "message": f"~/.claude/settings.json is malformed JSON; refusing to overwrite. Fix manually then retry."}

    command = _resolve_hook_command(cfg)
    settings.setdefault("hooks", {})
    for event in _HOOK_EVENTS:
        existing = settings["hooks"].setdefault(event, [])
        # Drop any prior managed entries (idempotent reinstall + command-string refresh).
        existing[:] = [
            entry
            for entry in existing
            if not any(h.get(_MANAGED_KEY) for h in entry.get("hooks", []))
        ]
        existing.append(_managed_entry(command))

    _atomic_write(path, settings)
    return {"ok": True, "message": f"Installed {len(_HOOK_EVENTS)} Claude Code hooks via `{command}`."}


def uninstall_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"ok": False, "message": "~/.claude/settings.json is malformed JSON; cannot edit safely."}
    if not settings or "hooks" not in settings:
        return {"ok": True, "message": "No hooks block — nothing to uninstall."}

    removed = 0
    for event in _HOOK_EVENTS:
        existing = settings["hooks"].get(event, [])
        before = len(existing)
        existing[:] = [
            entry
            for entry in existing
            if not any(h.get(_MANAGED_KEY) for h in entry.get("hooks", []))
        ]
        removed += before - len(existing)
        if not existing:
            settings["hooks"].pop(event, None)

    _atomic_write(path, settings)
    return {"ok": True, "message": f"Removed {removed} managed hook entries."}


def verify_hooks(cfg) -> dict:
    path = _settings_path(cfg)
    settings = _load_settings(path)
    if settings is None:
        return {"installed": False, "ours_present": False, "message": "settings.json is malformed."}
    if not path.exists():
        return {"installed": False, "ours_present": False, "message": "settings.json does not exist."}

    hooks = settings.get("hooks", {})
    found = sum(
        1
        for event in _HOOK_EVENTS
        for entry in hooks.get(event, [])
        for h in entry.get("hooks", [])
        if h.get(_MANAGED_KEY)
    )
    ours_present = found >= len(_HOOK_EVENTS)
    return {
        "installed": bool(hooks),
        "ours_present": ours_present,
        "message": f"Found {found}/{len(_HOOK_EVENTS)} managed hook entries.",
    }
