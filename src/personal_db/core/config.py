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
    def apps_dir(self) -> Path:
        return self.root / "apps"

    @property
    def sources_dir(self) -> Path:
        return self.root / "sources"

    @property
    def entities_dir(self) -> Path:
        return self.root / "entities"

    @property
    def notes_dir(self) -> Path:
        return self.root / "notes"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def user_name_tokens(self) -> tuple[str, ...]:
        """User-configured merchant-token exclusions: config.yaml `user.name_tokens`.

        Computed on demand (like the path properties above) rather than
        stored, because most call sites construct `Config(root=...)` directly
        rather than via `load_config()` — this way the setting works
        regardless of how `Config` was constructed. Returns () if config.yaml
        is absent or doesn't declare `user.name_tokens`.
        """
        config_path = self.root / "config.yaml"
        if not config_path.is_file():
            return ()
        try:
            data = yaml.safe_load(config_path.read_text()) or {}
        except yaml.YAMLError:
            return ()
        if not isinstance(data, dict):
            return ()
        user_cfg = data.get("user")
        if not isinstance(user_cfg, dict):
            return ()
        tokens = user_cfg.get("name_tokens")
        if not isinstance(tokens, list):
            return ()
        return tuple(str(t).strip().lower() for t in tokens if str(t).strip())


def load_config(path: Path | None = None) -> Config:
    """Load config.yaml; fall back to defaults if missing."""
    if path is None:
        path = Path(DEFAULT_ROOT).expanduser() / "config.yaml"
    data = yaml.safe_load(path.read_text()) or {} if path.exists() else {}
    root = Path(data.get("root", DEFAULT_ROOT)).expanduser()
    return Config(root=root)
