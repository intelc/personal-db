"""Discovery of declared background jobs across installed trackers and apps.

Replaces the daemon's old hardwired finance-specific periodic loops: a
tracker/app manifest declares `background_jobs: [{name, every, entrypoint}]`
and the daemon schedules each one generically (see
`services.daemon.server`). Core only *discovers* jobs here; running them
(threads, logging, cadence enforcement) is the daemon's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from personal_db.core.apps import discover_apps
from personal_db.core.config import Config
from personal_db.core.manifest import BackgroundJobSpec, ManifestError, load_manifest


@dataclass(frozen=True)
class DeclaredBackgroundJob:
    extension_kind: str  # "tracker" | "app"
    extension_name: str
    base_dir: Path
    spec: BackgroundJobSpec

    @property
    def qualified_name(self) -> str:
        return f"{self.extension_kind}:{self.extension_name}:{self.spec.name}"


def discover_background_jobs(cfg: Config) -> list[DeclaredBackgroundJob]:
    """Discover every declared background job on installed trackers + apps."""
    out: list[DeclaredBackgroundJob] = []
    if cfg.trackers_dir.exists():
        for entry in sorted(cfg.trackers_dir.iterdir()):
            if not entry.is_dir():
                continue
            manifest_path = entry / "manifest.yaml"
            if not manifest_path.is_file():
                continue
            try:
                manifest = load_manifest(manifest_path)
            except ManifestError:
                continue
            for spec in manifest.background_jobs:
                out.append(DeclaredBackgroundJob("tracker", manifest.name, entry, spec))
    for definition in discover_apps(cfg, include_bundled=False).values():
        for spec in definition.manifest.background_jobs:
            out.append(DeclaredBackgroundJob("app", definition.name, definition.root, spec))
    return out
