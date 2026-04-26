from pathlib import Path

# Global state — set by the app callback, read by commands via get_root().
_state: dict[str, Path | None] = {"root": None}


def get_root() -> Path:
    if _state["root"] is None:
        return Path("~/personal_db").expanduser()
    return _state["root"]
