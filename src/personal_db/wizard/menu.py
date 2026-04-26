"""questionary-based menu loop for `personal-db tracker setup` (no-arg form)."""

from __future__ import annotations

import questionary

from personal_db.config import Config
from personal_db.manifest import load_manifest
from personal_db.wizard.runner import run_tracker
from personal_db.wizard.status import compute_icon, read_status

_DONE = "__DONE__"


def _list_trackers(cfg: Config) -> list[str]:
    if not cfg.trackers_dir.exists():
        return []
    return sorted(
        d.name for d in cfg.trackers_dir.iterdir() if d.is_dir() and (d / "manifest.yaml").exists()
    )


def _format_choice(cfg: Config, name: str) -> str:
    icon = compute_icon(cfg, name)
    manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
    status = read_status(cfg).get(name)
    if icon == "—":
        suffix = "no setup needed"
    elif icon == "✓":
        suffix = "configured · last test passed"
    elif icon == "!":
        detail = (status or {}).get("detail", "test sync failed")
        suffix = f"configured · {detail}"
    else:  # ✗
        suffix = "needs setup"
    return f"{icon} {name:18s} {suffix} — {manifest.description}"


def run_menu(cfg: Config) -> None:
    """Loop: render → select tracker (or Done) → run that tracker → repeat."""
    while True:
        names = _list_trackers(cfg)
        if not names:
            print("No trackers installed. Use `personal-db tracker install <name>` first.")
            return
        choices = [questionary.Choice(title=_format_choice(cfg, n), value=n) for n in names]
        choices.append(questionary.Choice(title="✓ Done — exit wizard", value=_DONE))
        selection = questionary.select("Tracker setup:", choices=choices).ask()
        if selection is None or selection == _DONE:
            return
        run_tracker(cfg, selection)
