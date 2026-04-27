# Tracker Setup Wizard Implementation Plan (v0.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a menu-driven `personal-db tracker setup` wizard that walks the user through configuring all 5 v0 connectors (env vars, OAuth, FDA permissions, supplementary instructions) and verifies each with a test sync — collapsing first-use friction from "read 5 manifests and figure it out" to "run one command."

**Architecture:** Manifest's `setup_steps` field migrates from `list[str]` (prose) to a Pydantic discriminated union of typed steps (`env_var`, `oauth`, `fda_check`, `instructions`, `command_test`). A new `wizard/` package interprets steps via per-type handlers. Credentials land in a `<root>/.env` file loaded by `python-dotenv` at CLI startup (override=False so shell env still wins). The menu uses `questionary.select` in a loop, with status icons computed from cheap configured-or-not probes plus the most recent test sync result persisted to `<root>/state/wizard_status.json`.

**Tech Stack:** Python 3.11+, `questionary>=2.0` (NEW top-level dep), `python-dotenv>=1.0` (promoted from transitive), Pydantic v2 discriminated unions, existing `personal_db.oauth` / `personal_db.permissions` / `personal_db.sync` modules.

**Spec:** [`docs/superpowers/specs/2026-04-26-tracker-setup-wizard-design.md`](../specs/2026-04-26-tracker-setup-wizard-design.md)

---

## File structure (lock-in)

**Spec deviation called out:** the spec's §10 lists `wizard/schema.py` for SetupStep types. The plan moves these to `manifest.py` instead, because `Manifest.setup_steps: list[SetupStep]` would otherwise force `manifest.py` to import from `wizard/`, which inverts the dependency direction (framework depends on wizard). Co-locating SetupStep with the rest of the manifest's Pydantic models in `manifest.py` matches the spec's *intent* (typed setup steps as part of the manifest schema) without the import cycle.

```
src/personal_db/
  manifest.py              MODIFY  add SetupStep union; change setup_steps field type
  oauth.py                 MODIFY  add exchange_code() helper
  cli/
    main.py                MODIFY  load <root>/.env in _global callback
    tracker_cmd.py         MODIFY  add `setup [name]` subcommand
  wizard/                  NEW package
    __init__.py
    env_file.py            read/write/update <root>/.env preserving comments + atomic writes
    status.py              read/write wizard_status.json + compute_icon()
    steps.py               5 step handlers + StepResult + WizardContext
    runner.py              run_tracker(cfg, name) — execute steps + test sync + persist status
    menu.py                questionary loop

src/personal_db/templates/trackers/
  github_commits/manifest.yaml      MODIFY  setup_steps to new schema
  whoop/manifest.yaml               MODIFY  setup_steps to new schema
  screen_time/manifest.yaml         MODIFY  setup_steps to new schema
  imessage/manifest.yaml            MODIFY  setup_steps to new schema
  habits/manifest.yaml              already setup_steps: [] — no change

tests/
  fixtures/manifest_valid.yaml      MODIFY  setup_steps to new schema
  unit/
    test_manifest.py                MODIFY  add test for SetupStep parsing + reject prose
    test_env_file.py                NEW
    test_wizard_status.py           NEW
    test_wizard_steps.py            NEW (one test class per handler)
    test_oauth.py                   MODIFY  add test for exchange_code
    test_dotenv_loading.py          NEW (3-line test that load_dotenv runs)
  integration/
    test_cli_setup.py               NEW
```

---

## Conventions used by every task

- **Branch:** `main` (continuing single-developer mode from v0).
- **Commits:** `feat(area): summary` per task.
- **Tests:** `uv run pytest -q` (or just `pytest -q` with venv active).
- **Lint:** `uv run ruff check . && uv run ruff format .` before each commit.
- **TDD:** write failing test → run, see it fail → implement → run, see it pass → ruff → commit.
- **Tmp-root fixture** from `tests/conftest.py` continues to be the shared filesystem fixture.

---

## Task 1: Schema migration — SetupStep union + field change + manifest migrations

This is intentionally one atomic task. The schema change breaks every existing manifest until they're migrated; doing it in one commit keeps the test suite green throughout.

**Files:**
- Modify: `src/personal_db/manifest.py`
- Modify: `tests/unit/test_manifest.py`
- Modify: `tests/fixtures/manifest_valid.yaml`
- Modify: `src/personal_db/templates/trackers/github_commits/manifest.yaml`
- Modify: `src/personal_db/templates/trackers/whoop/manifest.yaml`
- Modify: `src/personal_db/templates/trackers/screen_time/manifest.yaml`
- Modify: `src/personal_db/templates/trackers/imessage/manifest.yaml`
- Create: `tests/fixtures/manifest_invalid_step_type.yaml`

- [ ] **Step 1: Pre-scan tests for inline manifests with non-empty `setup_steps`**

```bash
grep -rn '"setup_steps":' tests/ src/personal_db/templates/
```
Most existing call sites use `"setup_steps": []` (already valid under both schemas — empty list parses fine for either type). Note any non-empty inline manifests that need updating in subsequent steps. Expected: only the 4 connector manifests + the `manifest_valid.yaml` fixture have non-empty setup_steps.

- [ ] **Step 2: Add a failing test for the new structured schema**

Add to `tests/unit/test_manifest.py`:
```python
def test_load_manifest_parses_env_var_step():
    """v0.1: setup_steps is now a list of typed steps, not prose strings."""
    m = load_manifest(FIXTURES / "manifest_valid.yaml")
    assert len(m.setup_steps) >= 1
    step = m.setup_steps[0]
    assert step.type == "env_var"
    assert step.name == "GITHUB_TOKEN"
    assert step.secret is True

def test_load_manifest_rejects_prose_setup_steps(tmp_path):
    """A v0-style prose setup_steps must fail validation under v0.1."""
    p = tmp_path / "bad.yaml"
    p.write_text(
        "name: x\n"
        "description: x\n"
        "permission_type: api_key\n"
        'setup_steps: ["just a string"]\n'
        "time_column: ts\n"
        "schema:\n"
        "  tables:\n"
        "    x: {columns: {ts: {type: TEXT, semantic: ts}}}\n"
    )
    with pytest.raises(ManifestError):
        load_manifest(p)

def test_load_manifest_rejects_unknown_step_type():
    """A typo'd step type must fail validation."""
    with pytest.raises(ManifestError):
        load_manifest(FIXTURES / "manifest_invalid_step_type.yaml")
```

Create `tests/fixtures/manifest_invalid_step_type.yaml`:
```yaml
name: bad
description: unknown step type
permission_type: api_key
setup_steps:
  - type: nonexistent_step_type
    foo: bar
time_column: ts
schema:
  tables:
    bad:
      columns:
        ts: {type: TEXT, semantic: ts}
```

- [ ] **Step 3: Run, confirm failures**

```bash
pytest tests/unit/test_manifest.py -v
```
Expected: 3 new tests fail (no SetupStep types yet, fixture still in v0 format).

- [ ] **Step 4: Implement SetupStep union in `src/personal_db/manifest.py`**

Add above the `Manifest` class (and below the existing imports):

```python
from typing import Annotated, Literal, Union
from pydantic import Field

class EnvVarStep(BaseModel):
    type: Literal["env_var"]
    name: str
    prompt: str
    secret: bool = False

class OAuthStep(BaseModel):
    type: Literal["oauth"]
    provider: str
    client_id_env: str
    client_secret_env: str
    auth_url: str
    token_url: str
    scopes: list[str] = Field(default_factory=list)
    redirect_path: str = "/callback"

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
    Union[EnvVarStep, OAuthStep, FdaCheckStep, InstructionsStep, CommandTestStep],
    Field(discriminator="type"),
]
```

Then change the `Manifest` class's `setup_steps` annotation:
```python
class Manifest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())  # existing
    name: str
    description: str
    permission_type: PermissionType
    setup_steps: list[SetupStep] = Field(default_factory=list)  # CHANGED from list[str]
    schedule: ScheduleSpec | None = None
    time_column: str
    granularity: Literal["event", "minute", "hour", "day"] = "event"
    schema: SchemaSpec
    related_entities: list[str] = Field(default_factory=list)
```

- [ ] **Step 5: Migrate `tests/fixtures/manifest_valid.yaml`**

Replace the entire file with:
```yaml
name: github_commits
description: Commits authored by the user across GitHub
permission_type: api_key
setup_steps:
  - type: env_var
    name: GITHUB_TOKEN
    prompt: "GitHub personal access token (scopes: read:user, repo)"
    secret: true
  - type: env_var
    name: GITHUB_USER
    prompt: "Your GitHub username"
schedule:
  every: 4h
time_column: committed_at
granularity: event
schema:
  tables:
    github_commits:
      columns:
        sha: {type: TEXT, semantic: "commit SHA, primary key"}
        repo: {type: TEXT, semantic: "owner/name"}
        committed_at: {type: TEXT, semantic: "ISO-8601 commit timestamp (UTC)"}
        message: {type: TEXT, semantic: "first line of commit message"}
        additions: {type: INTEGER, semantic: "lines added"}
        deletions: {type: INTEGER, semantic: "lines deleted"}
related_entities: []
```

- [ ] **Step 6: Migrate the 4 connector manifests with non-empty setup_steps**

Rewrite `src/personal_db/templates/trackers/github_commits/manifest.yaml`'s `setup_steps`:
```yaml
setup_steps:
  - type: env_var
    name: GITHUB_TOKEN
    prompt: "GitHub personal access token (scopes: read:user, repo)"
    secret: true
  - type: env_var
    name: GITHUB_USER
    prompt: "Your GitHub username"
```

Rewrite `src/personal_db/templates/trackers/whoop/manifest.yaml`'s `setup_steps`:
```yaml
setup_steps:
  - type: env_var
    name: WHOOP_CLIENT_ID
    prompt: "Whoop OAuth client ID (from developer.whoop.com)"
  - type: env_var
    name: WHOOP_CLIENT_SECRET
    prompt: "Whoop OAuth client secret"
    secret: true
  - type: oauth
    provider: whoop
    client_id_env: WHOOP_CLIENT_ID
    client_secret_env: WHOOP_CLIENT_SECRET
    auth_url: "https://api.prod.whoop.com/oauth/oauth2/auth"
    token_url: "https://api.prod.whoop.com/oauth/oauth2/token"
    scopes: ["read:profile", "read:cycles"]
```

Rewrite `src/personal_db/templates/trackers/screen_time/manifest.yaml`'s `setup_steps`:
```yaml
setup_steps:
  - type: fda_check
    probe_path: "~/Library/Application Support/Knowledge/knowledgeC.db"
```

Rewrite `src/personal_db/templates/trackers/imessage/manifest.yaml`'s `setup_steps`:
```yaml
setup_steps:
  - type: fda_check
    probe_path: "~/Library/Messages/chat.db"
  - type: instructions
    text: |
      Add aliases for known people in `<root>/entities/people.yaml`, e.g.:

        - display_name: Marko
          aliases: ["marko@example.com", "+15551234567"]

      Aliases let messages from emails/phones resolve to a single person_id
      across all trackers. Without aliases, every handle becomes its own
      auto-created person.
```

Leave `habits/manifest.yaml` as-is (`setup_steps: []`).

- [ ] **Step 7: Run, confirm pass**

```bash
pytest tests/unit/test_manifest.py -v
pytest -q  # full suite — should still be 46/46
```
Expected: all green. The test_manifest tests pass; existing connector + integration tests pass because they use empty `setup_steps: []` in their inline manifests.

- [ ] **Step 8: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(manifest): migrate setup_steps to discriminated union of typed steps"
```

---

## Task 2: env_file module

**Files:**
- Create: `src/personal_db/wizard/__init__.py` (empty)
- Create: `src/personal_db/wizard/env_file.py`
- Create: `tests/unit/test_env_file.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_env_file.py`:
```python
from pathlib import Path

from personal_db.wizard.env_file import read_env, upsert_env


def test_read_env_missing_file_returns_empty(tmp_path):
    assert read_env(tmp_path / "nope.env") == {}


def test_upsert_env_creates_file_mode_0600(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "KEY", "value")
    assert read_env(p) == {"KEY": "value"}
    assert oct(p.stat().st_mode)[-3:] == "600"


def test_upsert_env_updates_existing_key(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "KEY", "v1")
    upsert_env(p, "KEY", "v2")
    assert read_env(p) == {"KEY": "v2"}


def test_upsert_env_appends_new_key_preserving_existing(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "A", "1")
    upsert_env(p, "B", "2")
    assert read_env(p) == {"A": "1", "B": "2"}


def test_upsert_env_preserves_comments_and_blank_lines(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# header\n\nA=1\n# section\nB=2\n")
    upsert_env(p, "B", "two")
    text = p.read_text()
    assert "# header" in text
    assert "# section" in text
    assert "B=two" in text
    assert "A=1" in text


def test_read_env_strips_quotes(tmp_path):
    p = tmp_path / ".env"
    p.write_text('A="hello world"\nB=plain\n')
    assert read_env(p) == {"A": "hello world", "B": "plain"}


def test_upsert_env_quotes_values_with_spaces(tmp_path):
    p = tmp_path / ".env"
    upsert_env(p, "K", "hello world")
    assert "K=\"hello world\"" in p.read_text()
```

- [ ] **Step 2: Run, confirm fail**

```bash
pytest tests/unit/test_env_file.py -v
```
Expected: 7 failures (module doesn't exist).

- [ ] **Step 3: Implement `src/personal_db/wizard/__init__.py` (empty)**

```python
```

- [ ] **Step 4: Implement `src/personal_db/wizard/env_file.py`**

```python
"""Read/write/update <root>/.env preserving comments and ordering.

Atomic writes (write tmp + rename) so partial-write corruption is impossible.
No external dependency — handwritten parser/writer for portability.
"""
from __future__ import annotations

import os
from pathlib import Path


def read_env(path: Path) -> dict[str, str]:
    """Parse a .env file. Returns {} if file is missing."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = _strip_quotes(v.strip())
    return out


def upsert_env(path: Path, key: str, value: str) -> None:
    """Insert or update a single key in .env, preserving existing structure.

    - File created with mode 0600 if missing.
    - Comments and blank lines preserved.
    - Atomic write via tmp + rename.
    """
    lines = path.read_text().splitlines() if path.exists() else []
    formatted = f"{key}={_quote_if_needed(value)}"
    replaced = False
    new_lines: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and "=" in stripped
            and stripped.split("=", 1)[0].strip() == key
        ):
            new_lines.append(formatted)
            replaced = True
        else:
            new_lines.append(raw)
    if not replaced:
        new_lines.append(formatted)
    body = "\n".join(new_lines) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(body)
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _strip_quotes(s: str) -> str:
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
        return s[1:-1]
    return s


def _quote_if_needed(value: str) -> str:
    if any(ch in value for ch in (" ", "\t", "#", '"')):
        # double-quote and escape any embedded double quotes
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value
```

- [ ] **Step 5: Run, confirm pass**

```bash
pytest tests/unit/test_env_file.py -v
```

- [ ] **Step 6: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): env_file module — read/upsert .env preserving comments"
```

---

## Task 3: wizard.status — persistence + icon computation

**Files:**
- Create: `src/personal_db/wizard/status.py`
- Create: `tests/unit/test_wizard_status.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_wizard_status.py`:
```python
import json

import yaml

from personal_db.config import Config
from personal_db.wizard.status import (
    compute_icon,
    read_status,
    write_status,
)


def _install_tracker(root, name, setup_steps):
    d = root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} tracker",
                "permission_type": "none" if not setup_steps else "api_key",
                "setup_steps": setup_steps,
                "time_column": "ts",
                "schema": {
                    "tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )


def test_read_status_missing_returns_empty(tmp_root):
    assert read_status(Config(root=tmp_root)) == {}


def test_write_then_read_status_roundtrip(tmp_root):
    cfg = Config(root=tmp_root)
    write_status(cfg, "github_commits", success=True, detail="3 rows")
    s = read_status(cfg)
    assert s["github_commits"]["success"] is True
    assert s["github_commits"]["detail"] == "3 rows"


def test_compute_icon_no_setup_steps_returns_dash(tmp_root):
    cfg = Config(root=tmp_root)
    _install_tracker(tmp_root, "habits", [])
    assert compute_icon(cfg, "habits") == "—"


def test_compute_icon_unconfigured_env_var_returns_x(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert compute_icon(cfg, "github_commits") == "✗"


def test_compute_icon_configured_no_test_recorded_returns_x(tmp_root, monkeypatch):
    """env var is set but we've never run a test sync → still ✗ until tested."""
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    assert compute_icon(cfg, "github_commits") == "✗"


def test_compute_icon_configured_and_test_passed_returns_check(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    write_status(cfg, "github_commits", success=True, detail="ok")
    assert compute_icon(cfg, "github_commits") == "✓"


def test_compute_icon_configured_but_test_failed_returns_bang(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _install_tracker(
        tmp_root,
        "github_commits",
        [{"type": "env_var", "name": "GITHUB_TOKEN", "prompt": "tok"}],
    )
    monkeypatch.setenv("GITHUB_TOKEN", "abc")
    write_status(cfg, "github_commits", success=False, detail="401 Unauthorized")
    assert compute_icon(cfg, "github_commits") == "!"
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement `src/personal_db/wizard/status.py`**

```python
"""Status icon computation + persistence for the wizard menu.

Icons:
  —  no setup_steps in manifest (e.g. habits)
  ✗  at least one setup_step's prerequisite is missing
  !  all prerequisites met but last recorded test sync failed
  ✓  all prerequisites met AND last recorded test sync succeeded
"""
from __future__ import annotations

import json
import os
from datetime import datetime, UTC
from pathlib import Path
from typing import Any

from personal_db.config import Config
from personal_db.manifest import (
    EnvVarStep,
    FdaCheckStep,
    OAuthStep,
    load_manifest,
)
from personal_db.permissions import probe_sqlite_access


def _status_path(cfg: Config) -> Path:
    return cfg.state_dir / "wizard_status.json"


def read_status(cfg: Config) -> dict[str, dict[str, Any]]:
    p = _status_path(cfg)
    if not p.exists():
        return {}
    return json.loads(p.read_text())


def write_status(cfg: Config, tracker: str, *, success: bool, detail: str) -> None:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    data = read_status(cfg)
    data[tracker] = {
        "success": success,
        "detail": detail,
        "ts": datetime.now(UTC).isoformat(),
    }
    _status_path(cfg).write_text(json.dumps(data, indent=2))


def compute_icon(cfg: Config, tracker: str) -> str:
    manifest = load_manifest(cfg.trackers_dir / tracker / "manifest.yaml")
    if not manifest.setup_steps:
        return "—"
    if not _all_prereqs_met(cfg, manifest.setup_steps):
        return "✗"
    status = read_status(cfg).get(tracker)
    if status is None:
        return "✗"  # no recorded test sync yet
    return "✓" if status.get("success") else "!"


def _all_prereqs_met(cfg: Config, steps) -> bool:
    """Cheap configured-or-not check per step type. No network, no FDA prompts."""
    for step in steps:
        if isinstance(step, EnvVarStep):
            if not os.environ.get(step.name):
                return False
        elif isinstance(step, OAuthStep):
            token_path = cfg.state_dir / "oauth" / f"{step.provider}.json"
            if not token_path.exists():
                return False
        elif isinstance(step, FdaCheckStep):
            r = probe_sqlite_access(Path(step.probe_path).expanduser())
            if not r.granted:
                return False
        # InstructionsStep and CommandTestStep have no prerequisites — they
        # only "fail" by being explicitly run and returning Failed. They're
        # treated as always-met for the purpose of the cheap icon probe.
    return True
```

- [ ] **Step 4: Run, confirm pass**

```bash
pytest tests/unit/test_wizard_status.py -v
```

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): status persistence + compute_icon for menu"
```

---

## Task 4: dotenv loading at CLI startup

**Files:**
- Modify: `src/personal_db/cli/main.py`
- Modify: `pyproject.toml` (promote python-dotenv to top-level dependency)
- Create: `tests/unit/test_dotenv_loading.py`

- [ ] **Step 1: Add `python-dotenv>=1.0` to `[project]` dependencies in `pyproject.toml`**

In the `dependencies = [...]` block, add `"python-dotenv>=1.0",` (alphabetical order — between `pyyaml` and `requests`).

Then run:
```bash
uv pip install -e .
```

- [ ] **Step 2: Write failing test**

`tests/unit/test_dotenv_loading.py`:
```python
import os
import subprocess
import sys


def test_dotenv_loaded_when_root_has_env_file(tmp_path):
    """When --root points at a directory with .env, env vars are loaded
    so that subcommands see them via os.environ.get."""
    root = tmp_path / "personal_db"
    # First init the root
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    # Write .env with a sentinel
    (root / ".env").write_text("PERSONAL_DB_TEST_SENTINEL=hello-from-env\n")
    # Run a subcommand that prints env vars (we use `python -c` + the dotenv side
    # effect of importing personal_db.cli.main)
    code = (
        "import sys; sys.argv=['p','--root',r'%s','--help']; "
        "from personal_db.cli import main; "
        "import os; print('SENTINEL=' + (os.environ.get('PERSONAL_DB_TEST_SENTINEL') or 'MISSING'))"
        % root
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    # --help prints to stdout then sys.exit(0); print runs before that.
    # Actually --help SystemExits before our print runs. Use a different approach:
    # invoke `tracker list` instead, then check the env via a small test helper.
    # Simpler: write a one-shot script that calls the callback directly.


def test_dotenv_load_function_reads_root_env(tmp_path, monkeypatch):
    """Direct unit test of the load helper."""
    from personal_db.cli.main import _load_root_env
    root = tmp_path / "personal_db"
    root.mkdir()
    (root / ".env").write_text("PERSONAL_DB_TEST_X=loaded\n")
    monkeypatch.delenv("PERSONAL_DB_TEST_X", raising=False)
    _load_root_env(root)
    assert os.environ.get("PERSONAL_DB_TEST_X") == "loaded"


def test_dotenv_does_not_override_real_env(tmp_path, monkeypatch):
    """override=False — shell env wins over .env (test/debug behavior)."""
    from personal_db.cli.main import _load_root_env
    root = tmp_path / "personal_db"
    root.mkdir()
    (root / ".env").write_text("PERSONAL_DB_TEST_Y=from-env-file\n")
    monkeypatch.setenv("PERSONAL_DB_TEST_Y", "from-shell")
    _load_root_env(root)
    assert os.environ.get("PERSONAL_DB_TEST_Y") == "from-shell"


def test_dotenv_load_silent_when_env_missing(tmp_path):
    from personal_db.cli.main import _load_root_env
    # Should not raise even though no .env exists
    _load_root_env(tmp_path / "personal_db")
```

The first test (`test_dotenv_loaded_when_root_has_env_file`) is messy because of `--help` short-circuiting; remove it and rely on the unit tests of `_load_root_env`. Update the test file to delete that first test before running.

- [ ] **Step 3: Run, confirm fail**

```bash
pytest tests/unit/test_dotenv_loading.py -v
```
Expected: import error (`_load_root_env` doesn't exist).

- [ ] **Step 4: Modify `src/personal_db/cli/main.py`**

Read the current main.py, then add the `_load_root_env` helper and call it from `_global`:

```python
from pathlib import Path

import typer
from dotenv import load_dotenv

from personal_db.cli import init_cmd, tracker_cmd
from personal_db.cli.state import _state, get_root  # noqa: F401 — re-exported for callers

app = typer.Typer(no_args_is_help=True, help="Personal data layer CLI")


def _load_root_env(root: Path) -> None:
    """Load <root>/.env if present. override=False so shell env wins."""
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


@app.callback()
def _global(root: str = typer.Option(None, "--root", help="Override data root")):
    if root:
        _state["root"] = Path(root).expanduser()
    _load_root_env(get_root())


# ... rest of main.py unchanged ...
```

- [ ] **Step 5: Run, confirm pass**

```bash
pytest tests/unit/test_dotenv_loading.py -v
pytest -q  # full suite still green
```

- [ ] **Step 6: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(cli): load <root>/.env on startup (override=False, shell env wins)"
```

---

## Task 5: EnvVar step handler

**Files:**
- Create: `src/personal_db/wizard/steps.py`
- Create: `tests/unit/test_wizard_steps.py`

- [ ] **Step 1: Write failing test**

`tests/unit/test_wizard_steps.py`:
```python
from dataclasses import dataclass
from pathlib import Path

import pytest

from personal_db.config import Config
from personal_db.manifest import EnvVarStep
from personal_db.wizard.env_file import read_env
from personal_db.wizard.steps import (
    Failed,
    Ok,
    StepResult,
    WizardContext,
    handle_env_var,
)


def _ctx(tmp_root) -> WizardContext:
    return WizardContext(cfg=Config(root=tmp_root), env_path=tmp_root / ".env")


def test_env_var_writes_value_when_missing(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(
        "personal_db.wizard.steps._prompt", lambda prompt, **kw: "abc123"
    )
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok", secret=True)
    r = handle_env_var(step, ctx)
    assert isinstance(r, Ok)
    assert read_env(ctx.env_path) == {"GITHUB_TOKEN": "abc123"}


def test_env_var_keeps_current_when_empty_input(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    ctx = _ctx(tmp_root)
    ctx.env_path.write_text("GITHUB_TOKEN=existing\n")
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "")
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    r = handle_env_var(step, ctx)
    assert isinstance(r, Ok)
    assert read_env(ctx.env_path) == {"GITHUB_TOKEN": "existing"}


def test_env_var_failed_when_no_value_and_no_input(tmp_root, monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    r = handle_env_var(step, ctx)
    assert isinstance(r, Failed)
    assert "no value" in r.reason.lower()


def test_env_var_updates_os_environ_after_write(tmp_root, monkeypatch):
    """After writing to .env, the new value should be visible to subsequent
    sync calls in the same process (the wizard runs them in-process)."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda prompt, **kw: "newval")
    ctx = _ctx(tmp_root)
    step = EnvVarStep(type="env_var", name="GITHUB_TOKEN", prompt="tok")
    handle_env_var(step, ctx)
    import os
    assert os.environ.get("GITHUB_TOKEN") == "newval"
```

- [ ] **Step 2: Run, confirm fail**

```bash
pytest tests/unit/test_wizard_steps.py -v
```

- [ ] **Step 3: Implement `src/personal_db/wizard/steps.py`**

```python
"""Step handlers for the tracker setup wizard.

Each handler takes a step and a WizardContext and returns a StepResult
(Ok / Failed / Skipped). Handlers MUTATE state (write .env, save oauth
tokens, etc.) and return a structured result the runner can record.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import questionary

from personal_db.config import Config
from personal_db.manifest import EnvVarStep
from personal_db.wizard.env_file import read_env, upsert_env


@dataclass
class WizardContext:
    cfg: Config
    env_path: Path


@dataclass
class Ok:
    detail: str = "ok"


@dataclass
class Failed:
    reason: str


@dataclass
class Skipped:
    reason: str


StepResult = Union[Ok, Failed, Skipped]


def _prompt(message: str, *, secret: bool = False, default: str = "") -> str:
    """Indirection so tests can monkeypatch this single seam."""
    if secret:
        return questionary.password(message, default=default).ask() or ""
    return questionary.text(message, default=default).ask() or ""


def handle_env_var(step: EnvVarStep, ctx: WizardContext) -> StepResult:
    current = read_env(ctx.env_path).get(step.name) or os.environ.get(step.name) or ""
    if current:
        if step.secret:
            display = "••••" + current[-4:] if len(current) >= 4 else "•" * len(current)
        else:
            display = current
        message = f"{step.prompt} (current: {display}, Enter to keep)"
    else:
        message = step.prompt
    new_value = _prompt(message, secret=step.secret)
    final = new_value or current
    if not final:
        return Failed(f"no value provided for {step.name}")
    upsert_env(ctx.env_path, step.name, final)
    os.environ[step.name] = final  # propagate so test sync sees it
    return Ok(f"{step.name} configured")
```

- [ ] **Step 4: Run, confirm pass**

```bash
pytest tests/unit/test_wizard_steps.py -v
```

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): EnvVar step handler with .env persistence"
```

---

## Task 6: FdaCheck step handler

**Files:**
- Modify: `src/personal_db/wizard/steps.py`
- Modify: `tests/unit/test_wizard_steps.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_wizard_steps.py`:
```python
import sqlite3

from personal_db.manifest import FdaCheckStep
from personal_db.wizard.steps import handle_fda_check


def test_fda_check_returns_ok_when_db_accessible(tmp_root, monkeypatch):
    db = tmp_root / "ok.sqlite"
    sqlite3.connect(db).executescript("CREATE TABLE x (a INT);")
    step = FdaCheckStep(type="fda_check", probe_path=str(db))
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_fda_check_failed_after_3_retries_when_denied(tmp_root, monkeypatch):
    """Simulate a denied probe; user presses Enter 3 times without granting; should Fail."""
    monkeypatch.setattr(
        "personal_db.wizard.steps.probe_sqlite_access",
        lambda p: type("R", (), {"granted": False, "reason": "FDA denied"})(),
    )
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    monkeypatch.setattr("personal_db.wizard.steps.open_fda_settings_pane", lambda: None)
    step = FdaCheckStep(type="fda_check", probe_path="/dev/null/doesnt-matter")
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "FDA" in r.reason or "denied" in r.reason


def test_fda_check_succeeds_on_retry(tmp_root, monkeypatch):
    """First probe denied, user presses Enter, second probe granted → Ok."""
    state = {"calls": 0}

    def probe(_p):
        state["calls"] += 1
        granted = state["calls"] >= 2
        return type("R", (), {"granted": granted, "reason": "ok" if granted else "denied"})()

    monkeypatch.setattr("personal_db.wizard.steps.probe_sqlite_access", probe)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    monkeypatch.setattr("personal_db.wizard.steps.open_fda_settings_pane", lambda: None)
    step = FdaCheckStep(type="fda_check", probe_path="/dev/null")
    r = handle_fda_check(step, _ctx(tmp_root))
    assert isinstance(r, Ok)
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add to `src/personal_db/wizard/steps.py`**

Append:
```python
from personal_db.manifest import FdaCheckStep
from personal_db.permissions import open_fda_settings_pane, probe_sqlite_access


def handle_fda_check(step: FdaCheckStep, ctx: WizardContext) -> StepResult:
    """Probe the gated SQLite file. Up to 3 retries with user prompts."""
    probe_path = Path(step.probe_path).expanduser()
    for attempt in range(3):
        r = probe_sqlite_access(probe_path)
        if r.granted:
            return Ok(f"FDA granted for {probe_path}")
        if attempt == 0:
            print(
                f"\n  ✗ Cannot access {probe_path}\n"
                f"    Reason: {r.reason}\n"
                f"\n  Grant Full Disk Access to your terminal binary "
                f"(Terminal.app, iTerm2, Cursor, etc.) in System Settings.\n"
                f"  Opening System Settings now…\n"
            )
            open_fda_settings_pane()
        _prompt(
            f"Press Enter once granted (attempt {attempt + 1}/3), "
            f"or just Enter to retry"
        )
    return Failed(
        f"FDA still denied after 3 attempts: {probe_path}. "
        f"Restart your terminal after granting and try again."
    )
```

- [ ] **Step 4: Run, confirm pass**

- [ ] **Step 5: Lint + commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): FdaCheck step handler with retry loop"
```

---

## Task 7: Instructions step handler

**Files:**
- Modify: `src/personal_db/wizard/steps.py`
- Modify: `tests/unit/test_wizard_steps.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_wizard_steps.py`:
```python
from personal_db.manifest import InstructionsStep
from personal_db.wizard.steps import handle_instructions


def test_instructions_always_returns_ok(tmp_root, monkeypatch, capsys):
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    step = InstructionsStep(
        type="instructions", text="Edit `<root>/entities/people.yaml` to add aliases."
    )
    r = handle_instructions(step, _ctx(tmp_root))
    assert isinstance(r, Ok)
    captured = capsys.readouterr()
    assert "Edit" in captured.out
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add to `src/personal_db/wizard/steps.py`**

```python
from personal_db.manifest import InstructionsStep


def handle_instructions(step: InstructionsStep, ctx: WizardContext) -> StepResult:
    print("\n" + step.text + "\n")
    _prompt("Press Enter when done")
    return Ok("acknowledged")
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): Instructions step handler"
```

---

## Task 8: CommandTest step handler

**Files:**
- Modify: `src/personal_db/wizard/steps.py`
- Modify: `tests/unit/test_wizard_steps.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_wizard_steps.py`:
```python
from personal_db.manifest import CommandTestStep
from personal_db.wizard.steps import handle_command_test


def test_command_test_ok_on_returncode_match(tmp_root):
    step = CommandTestStep(type="command_test", command=["true"])
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_command_test_failed_on_returncode_mismatch(tmp_root):
    step = CommandTestStep(type="command_test", command=["false"])
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Failed)


def test_command_test_pattern_match(tmp_root):
    step = CommandTestStep(
        type="command_test", command=["echo", "hello"], expect_pattern="ell"
    )
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Ok)


def test_command_test_pattern_mismatch_returns_failed(tmp_root):
    step = CommandTestStep(
        type="command_test", command=["echo", "hello"], expect_pattern="zzz"
    )
    r = handle_command_test(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "pattern" in r.reason.lower()
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add to `src/personal_db/wizard/steps.py`**

```python
import re
import subprocess

from personal_db.manifest import CommandTestStep


def handle_command_test(step: CommandTestStep, ctx: WizardContext) -> StepResult:
    try:
        r = subprocess.run(
            step.command, capture_output=True, text=True, timeout=30
        )
    except subprocess.TimeoutExpired:
        return Failed(f"command timed out: {' '.join(step.command)}")
    except FileNotFoundError as e:
        return Failed(f"command not found: {e}")
    if r.returncode != step.expect_returncode:
        return Failed(
            f"exit {r.returncode} (expected {step.expect_returncode}): {r.stderr.strip()}"
        )
    if step.expect_pattern and not re.search(step.expect_pattern, r.stdout):
        return Failed(f"pattern mismatch: {step.expect_pattern!r} not in output")
    return Ok("command verified")
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): CommandTest step handler with pattern matching"
```

---

## Task 9: oauth.exchange_code helper

**Files:**
- Modify: `src/personal_db/oauth.py`
- Modify: `tests/unit/test_oauth.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_oauth.py`:
```python
from unittest.mock import MagicMock, patch

from personal_db.oauth import exchange_code


def test_exchange_code_posts_to_token_url_and_returns_token():
    with patch("personal_db.oauth.requests.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 3600,
            },
        )
        mock_post.return_value.raise_for_status = MagicMock()
        token = exchange_code(
            token_url="https://example.com/token",
            client_id="CID",
            client_secret="CS",
            code="ABC",
            redirect_uri="http://127.0.0.1:8080/callback",
        )
    assert token["access_token"] == "AT"
    assert token["refresh_token"] == "RT"
    assert "expires_at" in token  # we add this for refresh_if_needed
    args, kwargs = mock_post.call_args
    assert args[0] == "https://example.com/token"
    data = kwargs["data"]
    assert data["grant_type"] == "authorization_code"
    assert data["code"] == "ABC"
    assert data["redirect_uri"] == "http://127.0.0.1:8080/callback"
    assert data["client_id"] == "CID"
    assert data["client_secret"] == "CS"
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add to `src/personal_db/oauth.py`**

Append at the end:
```python
def exchange_code(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for an access token.

    Counterpart to refresh_if_needed for the initial code-for-token step.
    """
    r = requests.post(
        token_url,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=10,
    )
    r.raise_for_status()
    token = r.json()
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
    return token
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(oauth): add exchange_code() for initial authorization-code flow"
```

---

## Task 10: OAuth step handler

**Files:**
- Modify: `src/personal_db/wizard/steps.py`
- Modify: `tests/unit/test_wizard_steps.py`

- [ ] **Step 1: Add failing test**

Append to `tests/unit/test_wizard_steps.py`:
```python
from unittest.mock import MagicMock, patch

from personal_db.manifest import OAuthStep
from personal_db.oauth import load_token
from personal_db.wizard.steps import handle_oauth


def test_oauth_handler_runs_full_flow(tmp_root, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "CID")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "CS")

    fake_flow = MagicMock()
    fake_flow.port = 12345
    fake_flow.wait_for_code.return_value = "AUTH_CODE"

    fake_token = {"access_token": "AT", "refresh_token": "RT", "expires_at": 9999999999}

    with (
        patch("personal_db.wizard.steps.OAuthFlow", return_value=fake_flow) as flow_cls,
        patch("personal_db.wizard.steps.exchange_code", return_value=fake_token) as ex,
        patch("personal_db.wizard.steps.webbrowser.open") as wb,
    ):
        ctx = _ctx(tmp_root)
        step = OAuthStep(
            type="oauth",
            provider="whoop",
            client_id_env="WHOOP_CLIENT_ID",
            client_secret_env="WHOOP_CLIENT_SECRET",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
            scopes=["read:profile"],
        )
        r = handle_oauth(step, ctx)

    assert isinstance(r, Ok)
    assert flow_cls.called
    assert ex.called
    assert wb.called  # we did open the browser
    saved = load_token(ctx.cfg, "whoop")
    assert saved["access_token"] == "AT"


def test_oauth_handler_failed_when_code_never_arrives(tmp_root, monkeypatch):
    monkeypatch.setenv("WHOOP_CLIENT_ID", "CID")
    monkeypatch.setenv("WHOOP_CLIENT_SECRET", "CS")
    fake_flow = MagicMock()
    fake_flow.port = 12345
    fake_flow.wait_for_code.return_value = None  # timeout

    with (
        patch("personal_db.wizard.steps.OAuthFlow", return_value=fake_flow),
        patch("personal_db.wizard.steps.webbrowser.open"),
    ):
        step = OAuthStep(
            type="oauth",
            provider="whoop",
            client_id_env="WHOOP_CLIENT_ID",
            client_secret_env="WHOOP_CLIENT_SECRET",
            auth_url="https://example.com/auth",
            token_url="https://example.com/token",
        )
        r = handle_oauth(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "timeout" in r.reason.lower() or "did you complete" in r.reason.lower()


def test_oauth_handler_failed_when_credentials_missing(tmp_root, monkeypatch):
    monkeypatch.delenv("WHOOP_CLIENT_ID", raising=False)
    monkeypatch.delenv("WHOOP_CLIENT_SECRET", raising=False)
    step = OAuthStep(
        type="oauth",
        provider="whoop",
        client_id_env="WHOOP_CLIENT_ID",
        client_secret_env="WHOOP_CLIENT_SECRET",
        auth_url="https://example.com/auth",
        token_url="https://example.com/token",
    )
    r = handle_oauth(step, _ctx(tmp_root))
    assert isinstance(r, Failed)
    assert "WHOOP_CLIENT_ID" in r.reason or "client" in r.reason.lower()
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add to `src/personal_db/wizard/steps.py`**

```python
import secrets
import urllib.parse
import webbrowser

from personal_db.manifest import OAuthStep
from personal_db.oauth import OAuthFlow, exchange_code, save_token


def handle_oauth(step: OAuthStep, ctx: WizardContext) -> StepResult:
    client_id = os.environ.get(step.client_id_env)
    client_secret = os.environ.get(step.client_secret_env)
    if not client_id or not client_secret:
        return Failed(
            f"missing OAuth credentials: ensure {step.client_id_env} and "
            f"{step.client_secret_env} are set (run env_var steps first)"
        )
    state = secrets.token_urlsafe(16)
    flow = OAuthFlow(state=state, port=0)
    flow.start()
    try:
        redirect_uri = f"http://127.0.0.1:{flow.port}{step.redirect_path}"
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        if step.scopes:
            params["scope"] = " ".join(step.scopes)
        auth_url = step.auth_url + "?" + urllib.parse.urlencode(params)
        print(f"\n  Opening browser to authorize {step.provider}…")
        print(f"  If it doesn't open, paste this URL manually:\n    {auth_url}\n")
        webbrowser.open(auth_url)
        code = flow.wait_for_code(timeout_s=120)
        if not code:
            return Failed(
                "OAuth timeout (120s): did you complete the redirect in your browser?"
            )
        token = exchange_code(
            token_url=step.token_url,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
        )
        save_token(ctx.cfg, step.provider, token)
        return Ok(f"OAuth completed for {step.provider}")
    finally:
        flow.shutdown()
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): OAuth step handler — full authorize-redirect-exchange flow"
```

---

## Task 11: wizard.runner — orchestrate steps + test sync + persist status

**Files:**
- Create: `src/personal_db/wizard/runner.py`
- Create: `tests/integration/test_wizard_runner.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_wizard_runner.py`:
```python
from unittest.mock import patch

import yaml

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.wizard.runner import run_tracker
from personal_db.wizard.status import read_status
from personal_db.wizard.steps import Failed, Ok


def _install_demo_tracker(tmp_root, setup_steps):
    d = tmp_root / "trackers" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "demo",
                "permission_type": "manual" if not setup_steps else "api_key",
                "setup_steps": setup_steps,
                "schedule": None,
                "time_column": "ts",
                "schema": {
                    "tables": {
                        "demo": {
                            "columns": {
                                "id": {"type": "TEXT", "semantic": "id"},
                                "ts": {"type": "TEXT", "semantic": "ts"},
                            }
                        }
                    }
                },
            }
        )
    )
    (d / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS demo (id TEXT PRIMARY KEY, ts TEXT);"
    )
    (d / "ingest.py").write_text(
        "def backfill(t,start,end): pass\n"
        "def sync(t):\n"
        "    t.upsert('demo', [{'id':'x','ts':'2026-04-26'}], key=['id'])\n"
    )


def test_run_tracker_with_no_setup_steps_runs_test_sync(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(tmp_root, [])
    result = run_tracker(cfg, "demo")
    assert result.success is True
    s = read_status(cfg)["demo"]
    assert s["success"] is True


def test_run_tracker_with_failing_step_records_failure_skips_sync(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(
        tmp_root,
        [{"type": "env_var", "name": "DEMO_VAR", "prompt": "demo"}],
    )
    monkeypatch.delenv("DEMO_VAR", raising=False)
    monkeypatch.setattr("personal_db.wizard.steps._prompt", lambda *a, **kw: "")
    result = run_tracker(cfg, "demo")
    assert result.success is False
    assert "no value" in result.detail.lower()
    s = read_status(cfg)["demo"]
    assert s["success"] is False


def test_run_tracker_records_test_sync_failure(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install_demo_tracker(tmp_root, [])
    # Replace the ingest with one that raises
    (tmp_root / "trackers" / "demo" / "ingest.py").write_text(
        "def backfill(t,start,end): pass\n"
        "def sync(t):\n"
        "    raise RuntimeError('boom')\n"
    )
    result = run_tracker(cfg, "demo")
    assert result.success is False
    assert "boom" in result.detail
    s = read_status(cfg)["demo"]
    assert s["success"] is False
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement `src/personal_db/wizard/runner.py`**

```python
"""Run a tracker's setup_steps in order, then a test sync, then persist status."""
from __future__ import annotations

from dataclasses import dataclass

from personal_db.config import Config
from personal_db.manifest import (
    CommandTestStep,
    EnvVarStep,
    FdaCheckStep,
    InstructionsStep,
    OAuthStep,
    load_manifest,
)
from personal_db.sync import sync_one
from personal_db.wizard.status import write_status
from personal_db.wizard.steps import (
    Failed,
    Ok,
    Skipped,
    StepResult,
    WizardContext,
    handle_command_test,
    handle_env_var,
    handle_fda_check,
    handle_instructions,
    handle_oauth,
)


@dataclass
class RunResult:
    success: bool
    detail: str


_DISPATCH = {
    EnvVarStep: handle_env_var,
    OAuthStep: handle_oauth,
    FdaCheckStep: handle_fda_check,
    InstructionsStep: handle_instructions,
    CommandTestStep: handle_command_test,
}


def run_tracker(cfg: Config, name: str) -> RunResult:
    manifest = load_manifest(cfg.trackers_dir / name / "manifest.yaml")
    ctx = WizardContext(cfg=cfg, env_path=cfg.root / ".env")

    # Run each setup_step in order. Stop on first Failed.
    for i, step in enumerate(manifest.setup_steps, 1):
        handler = _DISPATCH[type(step)]
        print(f"\n  [{i}/{len(manifest.setup_steps)}] {type(step).__name__}")
        result: StepResult = handler(step, ctx)
        if isinstance(result, Failed):
            detail = f"step {i} ({type(step).__name__}) failed: {result.reason}"
            write_status(cfg, name, success=False, detail=detail)
            return RunResult(success=False, detail=detail)
        if isinstance(result, Skipped):
            print(f"    skipped: {result.reason}")
        else:  # Ok
            print(f"    ✓ {result.detail}")

    # Test sync
    print(f"\n  Running test sync for {name}…")
    try:
        sync_one(cfg, name)
    except Exception as e:
        detail = f"test sync failed: {e}"
        write_status(cfg, name, success=False, detail=detail)
        print(f"    ✗ {detail}")
        return RunResult(success=False, detail=detail)
    detail = "test sync passed"
    write_status(cfg, name, success=True, detail=detail)
    print(f"    ✓ {detail}")
    return RunResult(success=True, detail=detail)
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
pytest tests/integration/test_wizard_runner.py -v
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): runner orchestrates steps + test sync + status persistence"
```

---

## Task 12: wizard.menu — questionary loop

**Files:**
- Create: `src/personal_db/wizard/menu.py`
- Create: `tests/unit/test_wizard_menu.py`

- [ ] **Step 1: Write failing test (component-level only — interactive loop is manual smoke)**

`tests/unit/test_wizard_menu.py`:
```python
from unittest.mock import patch

import yaml

from personal_db.config import Config
from personal_db.db import init_db
from personal_db.wizard.menu import _format_choice, _list_trackers, run_menu


def _install(tmp_root, name, setup_steps=None):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": f"{name} tracker",
                "permission_type": "none" if not setup_steps else "api_key",
                "setup_steps": setup_steps or [],
                "time_column": "ts",
                "schema": {
                    "tables": {
                        name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}
                    }
                },
            }
        )
    )


def test_list_trackers_returns_installed_only(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    _install(tmp_root, "github_commits",
             setup_steps=[{"type": "env_var", "name": "X", "prompt": "x"}])
    names = _list_trackers(cfg)
    assert set(names) == {"habits", "github_commits"}


def test_format_choice_includes_icon_and_status(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    label = _format_choice(cfg, "habits")
    assert "—" in label  # no setup needed icon
    assert "habits" in label


def test_run_menu_exits_on_done_selection(tmp_root):
    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    _install(tmp_root, "habits", setup_steps=[])
    # Select "Done" immediately
    with patch("personal_db.wizard.menu.questionary.select") as sel:
        sel.return_value.ask.return_value = "__DONE__"
        run_menu(cfg)
    assert sel.called
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement `src/personal_db/wizard/menu.py`**

```python
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
        d.name
        for d in cfg.trackers_dir.iterdir()
        if d.is_dir() and (d / "manifest.yaml").exists()
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
        choices = [
            questionary.Choice(title=_format_choice(cfg, n), value=n) for n in names
        ]
        choices.append(questionary.Choice(title="✓ Done — exit wizard", value=_DONE))
        selection = questionary.select("Tracker setup:", choices=choices).ask()
        if selection is None or selection == _DONE:
            return
        run_tracker(cfg, selection)
```

- [ ] **Step 4: Run, confirm pass; lint; commit**

```bash
ruff check . && ruff format .
git add -A
git commit -m "feat(wizard): menu loop with status icons + questionary select"
```

---

## Task 13: CLI subcommand wiring

**Files:**
- Modify: `src/personal_db/cli/tracker_cmd.py`
- Modify: `src/personal_db/cli/main.py`
- Create: `tests/integration/test_cli_setup.py`

- [ ] **Step 1: Write failing test**

`tests/integration/test_cli_setup.py`:
```python
import subprocess
import sys


def test_setup_with_name_arg_runs_runner_for_that_tracker(tmp_path):
    """`personal-db tracker setup habits` runs habits' (empty) setup + test sync."""
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "install",
            "habits",
        ],
        check=True,
        capture_output=True,
    )
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "personal_db.cli.main",
            "--root",
            str(root),
            "tracker",
            "setup",
            "habits",
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    # habits has no setup steps; should succeed via test sync alone
    import sqlite3
    con = sqlite3.connect(root / "db.sqlite")
    # The habits ingest is a no-op so no rows; the schema should exist though
    con.execute("SELECT * FROM habits")  # raises if table missing


def test_setup_help_lists_subcommand(tmp_path):
    r = subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "tracker", "--help"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0
    assert "setup" in r.stdout
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Add `setup` to `src/personal_db/cli/tracker_cmd.py`**

Append (alongside the existing `new`, `list_cmd`, `install` functions):
```python
from typing import Optional

from personal_db.config import Config
from personal_db.wizard.menu import run_menu
from personal_db.wizard.runner import run_tracker


def setup(name: Optional[str] = typer.Argument(None)) -> None:
    """Configure a tracker's required env vars / OAuth / FDA / instructions, then test sync.

    No argument → opens an interactive menu of all installed trackers.
    Argument     → runs setup for that one tracker and exits.
    """
    cfg = Config(root=get_root())
    if name is None:
        run_menu(cfg)
    else:
        result = run_tracker(cfg, name)
        if not result.success:
            raise typer.Exit(1)
```

You'll need to ensure `get_root` is imported at the top of `tracker_cmd.py` (it already should be from Task 11 of v0). Verify the existing imports include `from personal_db.cli.state import get_root`.

- [ ] **Step 4: Wire `setup` into `main.py`**

In `src/personal_db/cli/main.py`, find the existing tracker_app block (added in v0 Task 11) and add the new subcommand:
```python
tracker_app.command("setup")(tracker_cmd.setup)
```

It should sit alongside the existing `new`/`list`/`install` registrations.

- [ ] **Step 5: Run, confirm pass; lint; commit**

```bash
pytest tests/integration/test_cli_setup.py -v
pytest -q  # full suite
ruff check . && ruff format .
git add -A
git commit -m "feat(cli): personal-db tracker setup [name] — wizard entry point"
```

---

## Task 14: README updates + dependency declaration

**Files:**
- Modify: `README.md`
- Modify: `pyproject.toml` (already done in Task 4 for dotenv; add questionary here)

- [ ] **Step 1: Add `questionary>=2.0` to `pyproject.toml` `[project]` dependencies**

Insert alphabetically into the dependencies list (between `pydantic` and `python-dotenv`).

Run:
```bash
uv pip install -e .
```

- [ ] **Step 2: Update `README.md` Quick Start section**

In the Quick Start section, replace the "For each, run setup" block:

OLD:
```bash
# For each, run setup
export GITHUB_TOKEN=…  GITHUB_USER=…
export WHOOP_CLIENT_ID=…  WHOOP_CLIENT_SECRET=…
personal-db permission check screen_time   # opens System Settings if FDA missing
personal-db permission check imessage      # same
```

NEW:
```bash
# Configure each connector via the interactive wizard
personal-db tracker setup
# (Or set up one specific tracker: personal-db tracker setup whoop)
#
# The wizard:
#   - prompts for env vars (GITHUB_TOKEN, WHOOP_CLIENT_ID, etc.) and writes them
#     to <root>/.env (mode 0600, gitignored)
#   - launches OAuth flows in your browser for OAuth-based connectors (whoop)
#   - probes Full Disk Access for chat.db / knowledgeC.db and opens System
#     Settings if needed
#   - runs a test sync after each connector to confirm it's working
```

Also add a new section after "CLI argument order note":

```markdown
## Credentials

Credentials live in `<root>/.env` (default `~/personal_db/.env`, mode 0600).
The file is loaded automatically on every `personal-db` invocation; shell
environment variables override `.env` values (useful for debugging and tests).

To rotate a credential or fix a misconfiguration, re-run
`personal-db tracker setup <name>` — current values are shown as defaults
(secrets are masked) so you can press Enter to keep them or type a new value
to overwrite.
```

- [ ] **Step 3: Verify README renders cleanly**

Visual inspection — no broken markdown.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "docs: README updates for tracker setup wizard + .env credentials"
```

---

## Self-Review (against spec)

Run this checklist before declaring v0.1 done.

**Spec coverage:**

| Spec § | Tasks |
|---|---|
| 2 Wedge / scope | 1, 13 (wizard implementation overall) |
| 3 UX (menu, icons, re-select) | 3, 12, 13 |
| 4 Architecture | 11, 12 (runner + menu compose existing modules) |
| 5 Manifest schema extension | 1 |
| 6 Step handlers | 5, 6, 7, 8, 9, 10 (one per type + oauth helper) |
| 7 .env semantics | 2, 4 (env_file + dotenv loading) |
| 8 Connector manifest migrations | 1 (atomic with schema change) |
| 9 CLI surface | 13 |
| 10 File layout | enforced by Tasks 2-13 file paths |
| 11 Component boundaries | enforced by file structure |
| 12 Error handling | distributed: 5 (env_var fail), 6 (FDA retry), 10 (oauth timeout), 11 (sync fail) |
| 13 Testing | every task ships tests; menu loop is component-tested only (manual smoke for the interactive loop, per spec §13) |
| 14 Dependencies | 4 (dotenv promote), 14 (questionary add) |
| 15 Success criteria | manual smoke per criteria 4 (run wizard end-to-end on author's machine) |

**Manual smoke checklist (after Task 14):**

1. `personal-db init`
2. `personal-db tracker install github_commits whoop screen_time imessage habits` (one at a time)
3. `personal-db tracker setup` — menu opens; navigate; configure each in turn.
4. After: `cat ~/personal_db/.env` shows GITHUB_TOKEN=…, WHOOP_CLIENT_ID=…, WHOOP_CLIENT_SECRET=…
5. `cat ~/personal_db/state/oauth/whoop.json` exists
6. `cat ~/personal_db/state/wizard_status.json` shows all five trackers with `success: true`
7. `personal-db sync github_commits whoop screen_time imessage` all return without error and produce row counts > 0
8. Re-running `personal-db tracker setup` shows all `✓` icons in the menu
9. Selecting a `✓` tracker shows current values as defaults (Enter to keep)

**Run the full test suite:**
```bash
pytest -v
```
Expected: all green. Approximate count after v0.1: 46 (v0) + ~25 new = ~71 passing.

---

*End of plan.*
