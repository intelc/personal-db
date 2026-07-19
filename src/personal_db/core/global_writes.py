"""Guard for writes that escape the data root (launchd plists, ~/.claude/settings.json, MCP configs).

A daemon serving a scratch root (e.g. `--root /tmp/uiwork` during UI review)
must not rewrite the user's real global config: the launchd plist, Claude Code
hook settings, and MCP target configs all live outside <root> and are shared
with the user's real install.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

OVERRIDE_ENV = "PERSONAL_DB_ALLOW_GLOBAL_WRITES"

_TEMP_PARENTS = ("/tmp", "/private/tmp", "/var/folders", "/private/var/folders")


def blocked_reason(root: Path | None) -> str | None:
    """Why global-config writes are blocked for this data root, or None if allowed."""
    if os.environ.get(OVERRIDE_ENV) == "1":
        return None
    if root is None:
        return None
    resolved = Path(root).resolve()
    parents = {Path(p).resolve() for p in (tempfile.gettempdir(), *_TEMP_PARENTS)}
    for parent in parents:
        if resolved == parent or resolved.is_relative_to(parent):
            return (
                f"data root {root} is in a temp directory — refusing to modify global "
                f"config (launchd plist / ~/.claude/settings.json / MCP configs) from a "
                f"scratch root. Set {OVERRIDE_ENV}=1 to override."
            )
    return None
