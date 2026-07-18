import sys
import warnings
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class ManifestError(Exception): ...


class PlatformUnsupportedError(RuntimeError):
    """Raised when a manifest declares a `platform` list that excludes the
    OS personal-db is currently running on."""


PlatformName = Literal["darwin", "linux", "win32"]

_PLATFORM_LABELS: dict[str, str] = {"darwin": "macOS", "linux": "Linux", "win32": "Windows"}


def platform_label(name: str) -> str:
    """Human-friendly name for a `sys.platform` value (falls back to the raw value)."""
    return _PLATFORM_LABELS.get(name, name)


def check_platform_supported(manifest: "Manifest", *, current: str | None = None) -> None:
    """Raise PlatformUnsupportedError if `manifest.platform` is set and excludes
    the current OS. `manifest.platform is None` means portable (no gate)."""
    if manifest.platform is None:
        return
    current = sys.platform if current is None else current
    if current in manifest.platform:
        return
    names = " or ".join(platform_label(p) for p in manifest.platform)
    raise PlatformUnsupportedError(f"tracker {manifest.name} requires {names}")


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


class BackgroundJobSpec(BaseModel):
    """A periodic job a tracker or app declares for the daemon to schedule.

    ``entrypoint`` is ``"<module_file>:<function>"``, resolved relative to the
    installed extension directory (``<root>/trackers/<name>/`` or
    ``<root>/apps/<name>/``) via ``core.entrypoints.load_entrypoint``. The
    function receives a single ``cfg: Config`` argument and may return any
    JSON-serializable value (or ``None``); its return value is logged by the
    daemon but otherwise unused.
    """

    name: str
    every: str
    entrypoint: str


class McpToolSpec(BaseModel):
    """An MCP tool a tracker/app/source declares for the MCP server to expose.

    ``entrypoint`` is ``"<module_file>:<function>"``, resolved the same way as
    ``BackgroundJobSpec.entrypoint``. The function receives ``(cfg: Config,
    arguments: dict)`` and must return a JSON-serializable value.
    """

    name: str
    description: str
    entrypoint: str
    input_schema: dict[str, Any] = Field(default_factory=dict)


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
    adapter: str | None = None  # "<module>:<class>" loaded from the tracker dir
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
    # Some providers (notably Instagram Login) require an HTTPS redirect URI
    # even for localhost. When scheme=="https" the callback server wraps its
    # socket in a self-signed cert auto-generated under state_dir; the user
    # has to click through the browser's cert warning once per browser.
    scheme: Literal["http", "https"] = "http"
    # RFC 6749 says scopes are space-separated, but Instagram Login wants
    # commas. Adapters that need a non-standard separator set this.
    scope_separator: str = " "


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


class InstallHooksStep(BaseModel):
    type: Literal["install_hooks"]
    title: str
    description: str | None = None


class VerifyHooksStep(BaseModel):
    type: Literal["verify_hooks"]
    title: str


class NoteStep(BaseModel):
    type: Literal["note"]
    title: str
    body: str


class TrackerActionStep(BaseModel):
    type: Literal["action"]
    title: str
    action: str
    button_label: str
    description: str | None = None
    status_action: str | None = None
    status_label: str | None = None


SetupStep = Annotated[
    EnvVarStep
    | OAuthStep
    | FdaCheckStep
    | InstructionsStep
    | CommandTestStep
    | InstallHooksStep
    | VerifyHooksStep
    | NoteStep
    | TrackerActionStep,
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
    # Declared background jobs the daemon discovers and schedules at `every`
    # cadence, and declared MCP tools the MCP server discovers and dispatches.
    # See BackgroundJobSpec/McpToolSpec docstrings for the entrypoint contract.
    background_jobs: list[BackgroundJobSpec] = Field(default_factory=list)
    mcp_tools: list[McpToolSpec] = Field(default_factory=list)
    # manifest_version: the shape of this manifest file itself (bumped to 2
    # when a manifest gains `platform`/`schema_version`-era fields). Not the
    # data schema version -- see `schema_version` for that.
    manifest_version: int = 1
    # None (default) = portable, runs on any OS. A non-None list gates
    # sync/install via `check_platform_supported` -- see that function.
    platform: list[PlatformName] | None = None
    # Version of this tracker's *data* schema (schema.sql + any migrations/
    # dir). Bump when a migration is added; see core/migrations.py.
    schema_version: int = 1
    # PEP 508 requirement strings (e.g. "requests>=2.0") this tracker's
    # ingest.py/transforms need beyond what the bundle ships. `personal-db
    # tracker deps <name>` installs these into <root>/lib (core/pack_deps.py,
    # core/runtime_env.py) -- required because the signed app bundle's
    # embedded Python is sealed and can't have packages added to its own
    # site-packages. Rides along in the validation hash automatically (see
    # core/validation.py's module docstring: the whole manifest.yaml file is
    # hashed) -- editing this list re-requires validation before sync, same
    # as any other manifest change.
    python_deps: list[str] = Field(default_factory=list)


def load_manifest(path: Path) -> Manifest:
    try:
        data = yaml.safe_load(path.read_text())
        return Manifest.model_validate(data)
    except (yaml.YAMLError, ValidationError) as e:
        raise ManifestError(f"{path}: {e}") from e
