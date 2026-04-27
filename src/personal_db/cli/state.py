import os
from pathlib import Path

# Global state — set by the app callback, read by commands via get_root().
_state: dict[str, Path | None] = {"root": None}


def get_root() -> Path:
    """Resolve the data root with precedence: --root flag > PERSONAL_DB_ROOT > default.

    The env var lets demo recordings, CI runs, and users with multiple
    installs (e.g. work + personal) point at a non-default root without
    needing to pass --root on every invocation.
    """
    if _state["root"] is not None:
        return _state["root"]
    env_root = os.environ.get("PERSONAL_DB_ROOT")
    if env_root:
        return Path(env_root).expanduser()
    return Path("~/personal_db").expanduser()
