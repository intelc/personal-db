import warnings
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ManifestError(Exception): ...


class ColumnSpec(BaseModel):
    type: str
    semantic: str


class TableSpec(BaseModel):
    columns: dict[str, ColumnSpec]


class SchemaSpec(BaseModel):
    tables: dict[str, TableSpec]


class ScheduleSpec(BaseModel):
    every: str | None = None
    cron: str | None = None


PermissionType = Literal["none", "api_key", "oauth", "full_disk_access", "manual"]


# Suppress Pydantic's warning that the `schema` field name shadows BaseModel.schema classmethod.
warnings.filterwarnings(
    "ignore",
    message='.*Field name "schema".*shadows an attribute.*',
    category=UserWarning,
    module=__name__,
)


class Manifest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # silence model_* shadowing warnings

    name: str
    description: str
    permission_type: PermissionType
    setup_steps: list[str] = Field(default_factory=list)
    schedule: ScheduleSpec | None = None
    time_column: str
    granularity: Literal["event", "minute", "hour", "day"] = "event"
    schema: SchemaSpec
    related_entities: list[str] = Field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text())
        return Manifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ManifestError(f"{path}: {e}") from e
