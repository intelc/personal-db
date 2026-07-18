"""Install pack-declared `python_deps` into `<root>/lib` (see core/runtime_env.py
for why that directory exists instead of the bundle's own site-packages).

`personal-db tracker deps <name>` / `personal-db app deps <name>` are the CLI
surface over this module (cli/tracker_cmd.py, cli/app_cmd.py).
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass

from personal_db.core.config import Config
from personal_db.core.runtime_env import activate_lib_dir


class DepsInstallError(RuntimeError):
    """Raised when `pip install --target <root>/lib` fails."""


@dataclass(frozen=True)
class DepsResult:
    name: str
    deps: list[str]
    installed: bool  # False when there was nothing declared to install
    detail: str


def _pip_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _ensure_pip() -> None:
    """python-build-standalone interpreters ship pip already, so this is
    normally a no-op; the `pip --version` probe is cheap insurance against a
    stripped-down interpreter that doesn't."""
    if _pip_available():
        return
    subprocess.run(
        [sys.executable, "-m", "ensurepip", "--default-pip"],
        check=True,
        capture_output=True,
        timeout=120,
    )


def install_python_deps(cfg: Config, name: str, deps: list[str]) -> DepsResult:
    """Install `deps` (PEP 508 requirement strings) into `cfg.lib_dir` via
    `pip install --target`. No-ops (`installed=False`) when `deps` is empty.

    Uses `--upgrade` because `pip --target` does not reliably upgrade a
    dist that's already present under the target directory (a plain
    `pip install --target` with no `--upgrade` silently keeps the old
    version installed) -- always passing it makes re-running this after a
    manifest's `python_deps` changes actually pick up the new pin.

    Never uses `uv`: the sealed bundle only ships whatever the frozen
    interpreter carries (pip, via python-build-standalone), not uv.
    """
    if not deps:
        return DepsResult(name=name, deps=[], installed=False, detail="no python_deps declared")

    cfg.lib_dir.mkdir(parents=True, exist_ok=True)
    _ensure_pip()

    cmd = [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--target",
        str(cfg.lib_dir),
        "--upgrade",
        *deps,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = result.stderr[-4000:] if result.stderr else result.stdout[-4000:]
        raise DepsInstallError(
            f"pip install failed for {name} (exit {result.returncode}): {tail}"
        )
    # cfg.lib_dir may not have existed (and so wouldn't have been added to
    # sys.path) when this process started -- re-activate now that it's
    # populated, so anything installed here is importable immediately in
    # this same process (not just after a fresh entrypoint restart).
    activate_lib_dir(cfg)
    return DepsResult(
        name=name,
        deps=deps,
        installed=True,
        detail=f"installed {len(deps)} package(s) into {cfg.lib_dir}",
    )


def tracker_python_deps(cfg: Config, name: str) -> list[str]:
    from personal_db.core.manifest import load_manifest

    manifest_path = cfg.trackers_dir / name / "manifest.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no such tracker: {name}")
    return list(load_manifest(manifest_path).python_deps)


def install_tracker_deps(cfg: Config, name: str) -> DepsResult:
    return install_python_deps(cfg, name, tracker_python_deps(cfg, name))


def app_python_deps(cfg: Config, name: str) -> list[str]:
    from personal_db.core.apps import load_app_manifest

    manifest_path = cfg.apps_dir / name / "app.yaml"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"no such app: {name}")
    return list(load_app_manifest(manifest_path).python_deps)


def install_app_deps(cfg: Config, name: str) -> DepsResult:
    return install_python_deps(cfg, name, app_python_deps(cfg, name))
