import warnings
from pathlib import Path
from typing import Annotated, Literal

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


class EnvVarStep(BaseModel):
    type: Literal["env_var"]
    name: str
    prompt: str
    secret: bool = False
    optional: bool = False  # if True, empty input returns Skipped instead of Failed


class OAuthStep(BaseModel):
    type: Literal["oauth"]
    provider: str
    client_id_env: str
    client_secret_env: str
    auth_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    redirect_path: str = "/callback"
    redirect_port: int | None = (
        None  # None → OS picks; set when provider requires exact pre-registered URI
    )
    redirect_host: str = "127.0.0.1"  # provider may require "localhost" in the URI string


class FdaCheckStep(BaseModel):
    type: Literal["fda_check"]
    probe_path: str


class InstructionsStep(BaseModel):
    type: Literal["instructions"]
    text: str


class CommandTestStep(BaseModel):
    type: Literal["command_test"]
    command: list[str]
    expect_pattern: str | None = None
    expect_returncode: int = 0


SetupStep = Annotated[
    EnvVarStep | OAuthStep | FdaCheckStep | InstructionsStep | CommandTestStep,
    Field(discriminator="type"),
]


class Manifest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # silence model_* shadowing warnings

    name: str
    description: str
    permission_type: PermissionType
    setup_steps: list[SetupStep] = Field(default_factory=list)
    schedule: ScheduleSpec | None = None
    time_column: str
    granularity: Literal["event", "minute", "hour", "day"] = "event"
    schema: SchemaSpec
    related_entities: list[str] = Field(default_factory=list)
    # local_only: this tracker reads from local files that don't survive a system
    # reinstall (~/Library/..., local app DBs, etc.). The framework records the
    # earliest available date after each sync so derived trackers can flag days
    # before that horizon as "no_data" rather than misleadingly attributing them.
    local_only: bool = False


def load_manifest(path: Path) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text())
        return Manifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ManifestError(f"{path}: {e}") from e
