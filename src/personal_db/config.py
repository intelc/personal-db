from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_ROOT = "~/personal_db"


@dataclass(frozen=True)
class Config:
    root: Path

    @property
    def db_path(self) -> Path:
        return self.root / "db.sqlite"

    @property
    def trackers_dir(self) -> Path:
        return self.root / "trackers"

    @property
    def entities_dir(self) -> Path:
        return self.root / "entities"

    @property
    def notes_dir(self) -> Path:
        return self.root / "notes"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml; fall back to defaults if missing."""
    if path is None:
        path = Path(DEFAULT_ROOT).expanduser() / "config.yaml"
    data = yaml.safe_load(path.read_text()) or {} if path.exists() else {}
    root = Path(data.get("root", DEFAULT_ROOT)).expanduser()
    return Config(root=root)
