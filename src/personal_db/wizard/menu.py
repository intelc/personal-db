"""questionary-based menu loop for `personal-db tracker setup` (no-arg form)."""

from __future__ import annotations

from importlib import resources

import questionary
import yaml

from personal_db.config import Config
from personal_db.installer import install_template, is_outdated, list_bundled, update_template
from personal_db.manifest import load_manifest
from personal_db.wizard.mcp_setup import run_mcp_setup_menu
from personal_db.wizard.runner import run_tracker
from personal_db.wizard.status import compute_icon, read_status

_DONE = "__DONE__"
_INSTALL_PREFIX = "__INSTALL__:"
_MCP_SETUP = "__MCP_SETUP__"


def _list_trackers(cfg: Config) -> list[str]:
    if not cfg.trackers_dir.exists():
        return []
    return sorted(
        d.name for d in cfg.trackers_dir.iterdir() if d.is_dir() and (d / "manifest.yaml").exists()
    )


def _list_bundled_not_installed(cfg: Config) -> list[str]:
    installed = set(_list_trackers(cfg))
    return [n for n in list_bundled() if n not in installed]


def _format_choice(cfg: Config, name: str) -> str:
    if is_outdated(cfg, name):
        manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
        return f"⟳ {name:18s} update available — {manifest.description}"
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


def _format_bundled_choice(name: str) -> str:
    pkg = resources.files("personal_db.templates.trackers")
    manifest_text = pkg.joinpath(name, "manifest.yaml").read_text()
    description = yaml.safe_load(manifest_text).get("description", "")
    return f"+ {name:18s} not installed       — {description}"


def run_menu(cfg: Config) -> None:
    """Loop: render → select tracker (or Done) → run that tracker → repeat."""
    while True:
        installed = _list_trackers(cfg)
        not_installed = _list_bundled_not_installed(cfg)

        if not installed and not not_installed:
            print("No trackers available (no installed trackers and no bundled templates).")
            return

        choices: list = []
        for name in installed:
            choices.append(questionary.Choice(title=_format_choice(cfg, name), value=name))
        if installed and not_installed:
            choices.append(questionary.Separator("─── available to install ───"))
        for name in not_installed:
            choices.append(
                questionary.Choice(
                    title=_format_bundled_choice(name),
                    value=f"{_INSTALL_PREFIX}{name}",
                )
            )
        choices.append(
            questionary.Choice(
                title="🔌 MCP setup — install into Claude Code / Cursor / Desktop",
                value=_MCP_SETUP,
            )
        )
        choices.append(questionary.Choice(title="✓ Done — exit wizard", value=_DONE))

        selection = questionary.select("Tracker setup:", choices=choices).ask()
        if selection is None or selection == _DONE:
            return

        if selection == _MCP_SETUP:
            run_mcp_setup_menu(cfg)
            continue

        if selection.startswith(_INSTALL_PREFIX):
            name = selection[len(_INSTALL_PREFIX) :]
            try:
                install_template(cfg, name)
                print(f"  Installed {name}")
            except (FileExistsError, ValueError) as e:
                print(f"  ✗ install failed: {e}")
                continue
            run_tracker(cfg, name)
        else:
            if is_outdated(cfg, selection):
                update_template(cfg, selection)
                print(f"  ⟳ Updated {selection} from bundle")
            run_tracker(cfg, selection)
