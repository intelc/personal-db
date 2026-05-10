# Withings Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bundled `withings` tracker that ingests body-composition measurements from a Withings smart scale, plus a small framework extension (`TokenAdapter` registry + manifest field) so Withings' non-standard OAuth token endpoint stays contained inside the tracker dir.

**Architecture:** New tracker at `src/personal_db/templates/trackers/withings/` (six files including a tracker-local `oauth_adapter.py`). Framework changes add a per-provider OAuth adapter mechanism in `oauth.py` and a new optional `adapter` field on `OAuthStep` in `manifest.py`. Three wiring sites — the CLI wizard step, the daemon's web OAuth route, and `sync_one`/`backfill_one` — are taught to register a tracker's adapter before any token operation.

**Tech Stack:** Python 3.12+, pydantic, requests, SQLite. Existing tracker plumbing (`Tracker`, `Cursor`, `personal_db.oauth`, manifest loader) — same patterns as `whoop` and `granola`.

**Reference spec:** `docs/superpowers/specs/2026-05-09-withings-tracker-design.md`

---

## File Structure

**Created:**
- `src/personal_db/templates/trackers/withings/__init__.py` — empty package marker
- `src/personal_db/templates/trackers/withings/manifest.yaml` — tracker metadata + setup steps + schema
- `src/personal_db/templates/trackers/withings/schema.sql` — `withings_measurements` table + indexes
- `src/personal_db/templates/trackers/withings/oauth_adapter.py` — `WithingsAdapter` (extra `action=requesttoken` + envelope unwrap)
- `src/personal_db/templates/trackers/withings/ingest.py` — `sync()` / `backfill()` + `_flatten` + helpers
- `src/personal_db/templates/trackers/withings/visualizations.py` — `weight_trend_180d`, `body_composition_30d`
- `tests/unit/test_withings_tracker.py` — adapter unit tests + flatten/sync unit tests

**Modified:**
- `src/personal_db/oauth.py` — add `TokenAdapter` Protocol, `StandardAdapter`, `_adapters` registry, `register_adapter`, `_adapter_for`, `ensure_adapter_from_manifest`. Route `exchange_code` and `refresh_if_needed` through the registry. Thread `provider` through `start_web_oauth`'s internal `exchange_code` call.
- `src/personal_db/manifest.py:50-63` — add `adapter: str | None = None` to `OAuthStep`.
- `src/personal_db/wizard/steps.py:141-179` — call `ensure_adapter_from_manifest` before `exchange_code`; pass `provider=step.provider` to `exchange_code`.
- `src/personal_db/daemon/http.py:230-311` — call `ensure_adapter_from_manifest` before `start_web_oauth`.
- `src/personal_db/sync.py:150-170` — call `ensure_adapter_from_manifest` for every `OAuthStep` in the manifest before `mod.sync(t)` / `mod.backfill(...)`.
- `tests/unit/test_oauth.py` — add adapter-dispatch tests.
- `tests/unit/test_manifest.py` — confirm `adapter` field parses (and is optional).

---

## Task 1: Add TokenAdapter mechanism (registry + StandardAdapter) to oauth.py

**Files:**
- Modify: `src/personal_db/oauth.py`
- Test: `tests/unit/test_oauth.py`

This task only adds the registry and `StandardAdapter` class. It does NOT yet route `exchange_code` / `refresh_if_needed` through them — those are Tasks 2 and 3. Existing tests must still pass at the end of this task.

- [ ] **Step 1: Write the failing test for adapter registration**

Add to the bottom of `tests/unit/test_oauth.py`:

```python
def test_register_and_lookup_adapter():
    from personal_db.oauth import register_adapter, _adapter_for, StandardAdapter

    class _Fake:
        def exchange_code(self, **kw): return {}
        def refresh_token(self, **kw): return {}

    fake = _Fake()
    register_adapter("test_provider_xyz", fake)
    assert _adapter_for("test_provider_xyz") is fake
    # Unknown providers fall back to StandardAdapter
    assert isinstance(_adapter_for("never_registered"), StandardAdapter)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_register_and_lookup_adapter -v`
Expected: FAIL with `ImportError: cannot import name 'register_adapter'` (or similar).

- [ ] **Step 3: Add adapter mechanism to `personal_db/oauth.py`**

At the top of `src/personal_db/oauth.py`, add to imports:

```python
import importlib.util
from typing import Any, Protocol
```

Right above the `_token_path` function (around line 63), insert:

```python
class TokenAdapter(Protocol):
    """Provider-specific override for OAuth token exchange/refresh.

    Implementations return a token dict containing at least:
      access_token, refresh_token, expires_in
    The dispatcher (refresh_if_needed / exchange_code) adds expires_at.
    """

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]: ...

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]: ...


class StandardAdapter:
    """Default RFC 6749 token flow used when no per-provider adapter is registered."""

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
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
        return r.json()

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        r = requests.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout=10,
        )
        r.raise_for_status()
        return r.json()


_adapters: dict[str, TokenAdapter] = {}


def register_adapter(provider: str, adapter: TokenAdapter) -> None:
    """Register a TokenAdapter for `provider`. Idempotent: re-registering replaces."""
    _adapters[provider] = adapter


def _adapter_for(provider: str) -> TokenAdapter:
    return _adapters.get(provider) or StandardAdapter()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_register_and_lookup_adapter -v`
Expected: PASS.

- [ ] **Step 5: Confirm existing tests still pass**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py -v`
Expected: every existing test still passes.

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/oauth.py tests/unit/test_oauth.py
git commit -m "feat(oauth): add TokenAdapter protocol and provider registry"
```

---

## Task 2: Route `exchange_code` through the adapter registry

**Files:**
- Modify: `src/personal_db/oauth.py:112-138` (the `exchange_code` function) and `src/personal_db/oauth.py` (the `start_web_oauth` call site at ~line 237)
- Test: `tests/unit/test_oauth.py`

`exchange_code` currently takes no `provider` argument. Add it (keyword-only, default `"_standard"`) so existing call sites keep working, and route through `_adapter_for(provider)`.

- [ ] **Step 1: Write the failing test for adapter dispatch**

Add to `tests/unit/test_oauth.py`:

```python
def test_exchange_code_dispatches_to_registered_adapter():
    from personal_db.oauth import exchange_code, register_adapter

    seen = {}

    class _RecordingAdapter:
        def exchange_code(self, **kw):
            seen.update(kw)
            return {
                "access_token": "from-adapter",
                "refresh_token": "RT",
                "expires_in": 3600,
            }

        def refresh_token(self, **kw):  # unused here but required by protocol
            return {}

    register_adapter("dispatch_test", _RecordingAdapter())
    token = exchange_code(
        token_url="https://example.com/token",
        client_id="CID",
        client_secret="CS",
        code="ABC",
        redirect_uri="http://127.0.0.1:1/callback",
        provider="dispatch_test",
    )
    assert token["access_token"] == "from-adapter"
    assert "expires_at" in token
    assert seen["code"] == "ABC"
    assert seen["client_id"] == "CID"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_exchange_code_dispatches_to_registered_adapter -v`
Expected: FAIL — `exchange_code` doesn't accept `provider`.

- [ ] **Step 3: Refactor `exchange_code` to use the registry**

Replace the body of `exchange_code` in `src/personal_db/oauth.py` (around lines 112-138) with:

```python
def exchange_code(
    *,
    token_url: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    provider: str = "_standard",
) -> dict[str, Any]:
    """Exchange an OAuth authorization code for an access token.

    Dispatches through `_adapter_for(provider)` so providers with non-standard
    token endpoints (e.g. Withings) can override the wire format. The default
    `_standard` provider routes to StandardAdapter, preserving prior behavior.
    """
    adapter = _adapter_for(provider)
    token = adapter.exchange_code(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        code=code,
        redirect_uri=redirect_uri,
    )
    token["expires_at"] = int(time.time()) + int(token.get("expires_in", 3600))
    return token
```

- [ ] **Step 4: Thread `provider` through `start_web_oauth`'s internal call**

In `src/personal_db/oauth.py`, find the `exchange_code(...)` call inside the `_Handler.do_GET` method of `start_web_oauth` (around line 237) and add `provider=provider` to it:

```python
                token = exchange_code(
                    token_url=token_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    code=code,
                    redirect_uri=redirect_uri,
                    provider=provider,
                )
```

- [ ] **Step 5: Run all oauth tests**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py -v`
Expected: every test passes (including the new dispatch test and the pre-existing `test_exchange_code_posts_to_token_url_and_returns_token` which doesn't pass `provider` and so routes through `StandardAdapter`).

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/oauth.py tests/unit/test_oauth.py
git commit -m "feat(oauth): route exchange_code through adapter registry"
```

---

## Task 3: Route `refresh_if_needed` through the adapter registry

**Files:**
- Modify: `src/personal_db/oauth.py:80-109` (the `refresh_if_needed` function)
- Test: `tests/unit/test_oauth.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_oauth.py`:

```python
def test_refresh_if_needed_dispatches_to_registered_adapter(tmp_root):
    from personal_db.oauth import (
        load_token,
        refresh_if_needed,
        register_adapter,
        save_token,
    )

    cfg = Config(root=tmp_root)
    # Save an expired token so refresh is forced.
    save_token(cfg, "refresh_dispatch_test", {
        "access_token": "old",
        "refresh_token": "RT",
        "expires_at": 0,
    })

    seen = {}

    class _RecordingAdapter:
        def exchange_code(self, **kw): return {}
        def refresh_token(self, **kw):
            seen.update(kw)
            return {
                "access_token": "from-adapter",
                "refresh_token": "RT2",
                "expires_in": 3600,
            }

    register_adapter("refresh_dispatch_test", _RecordingAdapter())

    token = refresh_if_needed(
        cfg,
        "refresh_dispatch_test",
        token_url="https://example.com/token",
        client_id="CID",
        client_secret="CS",
    )
    assert token["access_token"] == "from-adapter"
    assert "expires_at" in token
    assert seen["refresh_token"] == "RT"
    # Token was persisted
    saved = load_token(cfg, "refresh_dispatch_test")
    assert saved["access_token"] == "from-adapter"
    assert saved["refresh_token"] == "RT2"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_refresh_if_needed_dispatches_to_registered_adapter -v`
Expected: FAIL — `refresh_if_needed` still posts directly via `requests.post`, doesn't consult the registry.

- [ ] **Step 3: Refactor `refresh_if_needed` to use the registry**

Replace the body of `refresh_if_needed` in `src/personal_db/oauth.py` (around lines 80-109) with:

```python
def refresh_if_needed(
    cfg: Config,
    provider: str,
    token_url: str,
    client_id: str,
    client_secret: str,
) -> dict[str, Any]:
    """Refresh the token if expired. Returns the (possibly refreshed) token.

    Dispatches the actual refresh wire call through `_adapter_for(provider)`
    so providers with non-standard token endpoints (e.g. Withings) can
    override the request shape and response parsing.
    """
    token = load_token(cfg, provider) or {}
    if token.get("expires_at", 0) > time.time() + 60:
        return token
    if "refresh_token" not in token:
        raise RuntimeError(f"{provider}: no refresh_token; re-run setup")
    adapter = _adapter_for(provider)
    new_token = adapter.refresh_token(
        token_url=token_url,
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=token["refresh_token"],
    )
    new_token["expires_at"] = int(time.time()) + int(new_token.get("expires_in", 3600))
    if "refresh_token" not in new_token:
        new_token["refresh_token"] = token["refresh_token"]
    save_token(cfg, provider, new_token)
    return new_token
```

- [ ] **Step 4: Run all oauth tests**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py -v`
Expected: every test passes.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/oauth.py tests/unit/test_oauth.py
git commit -m "feat(oauth): route refresh_if_needed through adapter registry"
```

---

## Task 4: Add `adapter` field to `OAuthStep` and `ensure_adapter_from_manifest` helper

**Files:**
- Modify: `src/personal_db/manifest.py:50-63`
- Modify: `src/personal_db/oauth.py`
- Test: `tests/unit/test_oauth.py`, `tests/unit/test_manifest.py`

- [ ] **Step 1: Write the failing manifest test**

Add to `tests/unit/test_manifest.py`:

```python
def test_oauth_step_accepts_optional_adapter_field(tmp_path):
    from personal_db.manifest import load_manifest, OAuthStep

    p = tmp_path / "manifest.yaml"
    p.write_text(
        """\
name: t1
permission_type: oauth
setup_steps:
  - type: oauth
    provider: withings_test
    adapter: oauth_adapter:WithingsAdapter
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schema:
  tables: {}
""",
    )
    m = load_manifest(p)
    step = m.setup_steps[0]
    assert isinstance(step, OAuthStep)
    assert step.adapter == "oauth_adapter:WithingsAdapter"


def test_oauth_step_adapter_field_is_optional(tmp_path):
    from personal_db.manifest import load_manifest, OAuthStep

    p = tmp_path / "manifest.yaml"
    p.write_text(
        """\
name: t2
permission_type: oauth
setup_steps:
  - type: oauth
    provider: whoop
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schema:
  tables: {}
""",
    )
    m = load_manifest(p)
    step = m.setup_steps[0]
    assert isinstance(step, OAuthStep)
    assert step.adapter is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_manifest.py::test_oauth_step_accepts_optional_adapter_field tests/unit/test_manifest.py::test_oauth_step_adapter_field_is_optional -v`
Expected: FAIL — pydantic rejects the extra `adapter` key.

- [ ] **Step 3: Add `adapter` field to `OAuthStep`**

In `src/personal_db/manifest.py` find the `OAuthStep` class (around line 50-63) and add the `adapter` field:

```python
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
    redirect_port: int | None = None
    redirect_host: str = "127.0.0.1"
```

- [ ] **Step 4: Run manifest tests**

Run: `.venv/bin/python -m pytest tests/unit/test_manifest.py -v`
Expected: PASS (both new tests + all existing tests).

- [ ] **Step 5: Write the failing helper test**

Add to `tests/unit/test_oauth.py`:

```python
def test_ensure_adapter_from_manifest_loads_and_registers(tmp_path):
    from personal_db.manifest import OAuthStep
    from personal_db.oauth import (
        _adapter_for,
        StandardAdapter,
        ensure_adapter_from_manifest,
    )

    # Drop a tiny adapter module into a fake tracker dir.
    tracker_dir = tmp_path / "fake_tracker"
    tracker_dir.mkdir()
    (tracker_dir / "my_adapter.py").write_text(
        """\
class FakeAdapter:
    def exchange_code(self, **kw):
        return {"access_token": "fa", "refresh_token": "r", "expires_in": 3600}
    def refresh_token(self, **kw):
        return {"access_token": "fa2", "refresh_token": "r", "expires_in": 3600}
"""
    )
    step = OAuthStep(
        type="oauth",
        provider="ensure_test_provider",
        adapter="my_adapter:FakeAdapter",
        client_id_env="X",
        client_secret_env="Y",
        auth_url="https://example.com/a",
        token_url="https://example.com/t",
    )

    # Before: unknown provider falls back to StandardAdapter
    assert isinstance(_adapter_for("ensure_test_provider"), StandardAdapter)

    ensure_adapter_from_manifest(tracker_dir, step)

    adapter = _adapter_for("ensure_test_provider")
    assert adapter.__class__.__name__ == "FakeAdapter"

    # Idempotent: calling again does not raise
    ensure_adapter_from_manifest(tracker_dir, step)
    assert _adapter_for("ensure_test_provider").__class__.__name__ == "FakeAdapter"


def test_ensure_adapter_from_manifest_noop_when_adapter_unset(tmp_path):
    from personal_db.manifest import OAuthStep
    from personal_db.oauth import (
        _adapter_for,
        StandardAdapter,
        ensure_adapter_from_manifest,
    )

    step = OAuthStep(
        type="oauth",
        provider="never_register_me",
        client_id_env="X",
        client_secret_env="Y",
        auth_url="https://example.com/a",
        token_url="https://example.com/t",
    )
    ensure_adapter_from_manifest(tmp_path, step)
    assert isinstance(_adapter_for("never_register_me"), StandardAdapter)
```

- [ ] **Step 6: Run helper tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_ensure_adapter_from_manifest_loads_and_registers tests/unit/test_oauth.py::test_ensure_adapter_from_manifest_noop_when_adapter_unset -v`
Expected: FAIL — `ensure_adapter_from_manifest` is not defined.

- [ ] **Step 7: Implement `ensure_adapter_from_manifest`**

Add to the bottom of `src/personal_db/oauth.py`:

```python
def ensure_adapter_from_manifest(tracker_dir: Path, step: Any) -> None:
    """Load `<tracker_dir>/<module>.py` and register `<class>()` for `step.provider`.

    No-op if `step.adapter` is None or the provider is already registered with
    the same class. Idempotent: safe to call repeatedly.

    `step` is typed as Any to avoid a circular import on `OAuthStep`; only
    `step.adapter` and `step.provider` attributes are accessed.
    """
    spec_str = getattr(step, "adapter", None)
    if not spec_str:
        return
    provider = step.provider
    existing = _adapters.get(provider)
    if existing is not None and existing.__class__.__name__ == spec_str.split(":")[1]:
        return
    module_name, _, class_name = spec_str.partition(":")
    module_path = tracker_dir / f"{module_name}.py"
    if not module_path.exists():
        raise RuntimeError(
            f"OAuth adapter module not found: {module_path} "
            f"(declared as {spec_str} in manifest)"
        )
    spec = importlib.util.spec_from_file_location(
        f"personal_db_oauth_adapter_{provider}_{module_name}",
        module_path,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, class_name, None)
    if cls is None:
        raise RuntimeError(
            f"OAuth adapter class {class_name} not found in {module_path}"
        )
    register_adapter(provider, cls())
```

- [ ] **Step 8: Run helper tests**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py::test_ensure_adapter_from_manifest_loads_and_registers tests/unit/test_oauth.py::test_ensure_adapter_from_manifest_noop_when_adapter_unset -v`
Expected: PASS.

- [ ] **Step 9: Run full oauth + manifest test suites**

Run: `.venv/bin/python -m pytest tests/unit/test_oauth.py tests/unit/test_manifest.py -v`
Expected: everything passes.

- [ ] **Step 10: Commit**

```bash
git add src/personal_db/manifest.py src/personal_db/oauth.py tests/unit/test_oauth.py tests/unit/test_manifest.py
git commit -m "feat(oauth): add adapter manifest field and ensure_adapter_from_manifest helper"
```

---

## Task 5: Wire CLI wizard's `handle_oauth` to register the adapter and pass provider

**Files:**
- Modify: `src/personal_db/wizard/steps.py:141-179`

This is a small wiring change with no new test surface (existing wizard tests still cover happy path; adapter usage tested at the helper level in Task 4 and end-to-end in Task 13).

- [ ] **Step 1: Modify `handle_oauth` in `src/personal_db/wizard/steps.py`**

Currently the `handle_oauth` function builds the auth URL inline and calls `exchange_code(...)` directly without `provider`. Change it to register the adapter (if any) before exchanging, and pass `provider=step.provider`.

In `src/personal_db/wizard/steps.py`, modify imports near the top (around line 29):

```python
from personal_db.oauth import (
    OAuthFlow,
    ensure_adapter_from_manifest,
    exchange_code,
    save_token,
)
```

Then replace the body of `handle_oauth` to register the adapter and pass `provider`:

```python
def handle_oauth(step: OAuthStep, ctx: WizardContext) -> StepResult:
    client_id = os.environ.get(step.client_id_env)
    client_secret = os.environ.get(step.client_secret_env)
    if not client_id or not client_secret:
        return Failed(
            f"missing OAuth credentials: ensure {step.client_id_env} and "
            f"{step.client_secret_env} are set (run env_var steps first)"
        )
    # Register the tracker's TokenAdapter (if any) before any token op.
    ensure_adapter_from_manifest(ctx.tracker_dir, step)
    state = secrets.token_urlsafe(16)
    flow = OAuthFlow(state=state, port=step.redirect_port or 0)
    flow.start()
    try:
        redirect_uri = f"http://{step.redirect_host}:{flow.port}{step.redirect_path}"
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
            return Failed("OAuth timeout (120s): did you complete the redirect in your browser?")
        token = exchange_code(
            token_url=step.token_url,
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=redirect_uri,
            provider=step.provider,
        )
        save_token(ctx.cfg, step.provider, token)
        return Ok(f"OAuth completed for {step.provider}")
    finally:
        flow.shutdown()
```

- [ ] **Step 2: Confirm `WizardContext` has `tracker_dir`**

Run: `.venv/bin/grep -n "tracker_dir\|class WizardContext" src/personal_db/wizard/*.py`
Expected: `WizardContext` has a `tracker_dir: Path` field (it does — used by other handlers).

If it doesn't, add `tracker_dir: Path` to the dataclass and update the one constructor call site. (Inspect output first.)

- [ ] **Step 3: Run wizard tests**

Run: `.venv/bin/python -m pytest tests/unit/test_wizard_steps.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add src/personal_db/wizard/steps.py
git commit -m "feat(wizard): register OAuth adapter before token exchange"
```

---

## Task 6: Wire daemon's `setup_tracker_oauth_start` to register the adapter

**Files:**
- Modify: `src/personal_db/daemon/http.py:230-311`

- [ ] **Step 1: Modify the daemon route**

In `src/personal_db/daemon/http.py`, update imports near line 40:

```python
from personal_db.oauth import ensure_adapter_from_manifest, start_web_oauth
```

In the `setup_tracker_oauth_start` function (around line 230-311), insert the adapter registration call right after `step = oauth_steps[idx]` (line 253):

```python
        step = oauth_steps[idx]
        # Register the tracker's TokenAdapter (if any) before any token op.
        ensure_adapter_from_manifest(cfg.trackers_dir / name, step)
```

No other code in this function changes — `start_web_oauth` already takes `provider=step.provider` and now threads it through to `exchange_code`.

- [ ] **Step 2: Run daemon route tests**

Run: `.venv/bin/python -m pytest tests/unit/test_daemon_routes.py -v`
Expected: all pass (existing routes don't declare an adapter, so `ensure_adapter_from_manifest` is a no-op for them).

- [ ] **Step 3: Commit**

```bash
git add src/personal_db/daemon/http.py
git commit -m "feat(daemon): register OAuth adapter before web OAuth flow"
```

---

## Task 7: Wire `sync_one` / `backfill_one` to register the adapter before sync

**Files:**
- Modify: `src/personal_db/sync.py:150-170`
- Test: `tests/unit/test_sync.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_sync.py`:

```python
def test_sync_one_registers_oauth_adapter_from_manifest(tmp_root, monkeypatch):
    """Ingest.py's sync() does not need to register the adapter itself —
    sync_one wires it up based on the manifest's OAuthStep.adapter field."""
    from personal_db.config import Config
    from personal_db.oauth import _adapter_for, StandardAdapter
    from personal_db.sync import sync_one

    cfg = Config(root=tmp_root)
    tracker_dir = cfg.trackers_dir / "fake_oauth_tracker"
    tracker_dir.mkdir(parents=True)

    (tracker_dir / "manifest.yaml").write_text(
        """\
name: fake_oauth_tracker
permission_type: oauth
setup_steps:
  - type: oauth
    provider: fake_oauth_provider
    adapter: my_adapter:MyAdapter
    client_id_env: A
    client_secret_env: B
    auth_url: https://example.com/a
    token_url: https://example.com/t
schema:
  tables:
    fake_table:
      columns:
        id: {type: TEXT, semantic: pk}
""",
    )
    (tracker_dir / "schema.sql").write_text(
        "CREATE TABLE IF NOT EXISTS fake_table (id TEXT PRIMARY KEY);\n"
    )
    (tracker_dir / "my_adapter.py").write_text(
        """\
class MyAdapter:
    def exchange_code(self, **kw): return {}
    def refresh_token(self, **kw): return {}
"""
    )
    (tracker_dir / "ingest.py").write_text(
        """\
def sync(t):
    return None
def backfill(t, start, end):
    return None
"""
    )

    # Sanity: not yet registered.
    assert isinstance(_adapter_for("fake_oauth_provider"), StandardAdapter)

    sync_one(cfg, "fake_oauth_tracker")

    assert _adapter_for("fake_oauth_provider").__class__.__name__ == "MyAdapter"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/unit/test_sync.py::test_sync_one_registers_oauth_adapter_from_manifest -v`
Expected: FAIL — adapter not registered.

- [ ] **Step 3: Wire `sync_one` and `backfill_one`**

In `src/personal_db/sync.py`, add to imports at the top:

```python
from personal_db.manifest import OAuthStep, load_manifest
from personal_db.oauth import ensure_adapter_from_manifest
```

(Note: `load_manifest` is already imported. Add `OAuthStep` and `ensure_adapter_from_manifest`.)

Add a small helper above `sync_one` (around line 145):

```python
def _register_oauth_adapters(tracker_dir: Path, manifest) -> None:
    """Register every OAuthStep.adapter declared in the manifest. Idempotent."""
    for step in manifest.setup_steps:
        if isinstance(step, OAuthStep):
            ensure_adapter_from_manifest(tracker_dir, step)
```

Modify `sync_one` (around line 150):

```python
def sync_one(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _register_oauth_adapters(tracker_dir, manifest)
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.sync(t)
    _run_transforms(cfg, name, mod, tracker_dir)
    _write_last_run(cfg, name, datetime.now(UTC).isoformat())
    _store_horizon(cfg, name, manifest)
```

Modify `backfill_one` (around line 162):

```python
def backfill_one(cfg: Config, name: str, start: str | None, end: str | None) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _register_oauth_adapters(tracker_dir, manifest)
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.backfill(t, start, end)
    _run_transforms(cfg, name, mod, tracker_dir)
    _store_horizon(cfg, name, manifest)
```

- [ ] **Step 4: Run sync tests**

Run: `.venv/bin/python -m pytest tests/unit/test_sync.py -v`
Expected: all pass (new test + existing).

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/sync.py tests/unit/test_sync.py
git commit -m "feat(sync): register OAuth adapters from manifest before sync"
```

---

## Task 8: Withings tracker scaffolding — manifest, schema, package marker

**Files:**
- Create: `src/personal_db/templates/trackers/withings/__init__.py`
- Create: `src/personal_db/templates/trackers/withings/manifest.yaml`
- Create: `src/personal_db/templates/trackers/withings/schema.sql`

- [ ] **Step 1: Create empty package marker**

Write `src/personal_db/templates/trackers/withings/__init__.py` with a single newline.

- [ ] **Step 2: Create the manifest**

Write `src/personal_db/templates/trackers/withings/manifest.yaml`:

```yaml
name: withings
description: Withings smart-scale body composition (weight, fat %, muscle, bone, etc.)
permission_type: oauth
setup_steps:
  - type: env_var
    name: WITHINGS_CLIENT_ID
    prompt: "Withings OAuth client ID (from developer.withings.com)"
  - type: env_var
    name: WITHINGS_CLIENT_SECRET
    prompt: "Withings OAuth client secret"
    secret: true
  - type: oauth
    provider: withings
    adapter: oauth_adapter:WithingsAdapter
    client_id_env: WITHINGS_CLIENT_ID
    client_secret_env: WITHINGS_CLIENT_SECRET
    auth_url: "https://account.withings.com/oauth2_user/authorize2"
    token_url: "https://wbsapi.withings.net/v2/oauth2"
    scopes: ["user.metrics"]
    redirect_host: localhost
    redirect_port: 9877
schedule:
  every: 6h
time_column: date
granularity: event
schema:
  tables:
    withings_measurements:
      columns:
        grpid:            {type: TEXT,    semantic: "Withings measure-group id, primary key"}
        date:             {type: TEXT,    semantic: "ISO-8601 measurement time (UTC)"}
        timezone:         {type: TEXT,    semantic: "IANA tz reported by Withings (e.g. America/Los_Angeles); per-row because user travels"}
        attrib:           {type: INTEGER, semantic: "0=device user known, 1=device ambiguous, 2=manual entry, 4=manual ambiguous, 5=user-create, 7=auto, 8=device hash unknown"}
        category:         {type: INTEGER, semantic: "1=real measurement, 2=user objective"}
        device_id:        {type: TEXT,    semantic: "Withings deviceid (raw, may be empty for manual entries)"}
        weight_kg:        {type: REAL,    semantic: "weight in kilograms (Withings type 1)"}
        fat_ratio_pct:    {type: REAL,    semantic: "body fat percentage (type 6)"}
        fat_mass_kg:      {type: REAL,    semantic: "fat mass in kg (type 8)"}
        lean_mass_kg:     {type: REAL,    semantic: "fat-free mass in kg (type 5)"}
        muscle_mass_kg:   {type: REAL,    semantic: "muscle mass in kg (type 76); often equals lean mass on basic scales"}
        bone_mass_kg:     {type: REAL,    semantic: "bone mass in kg (type 88)"}
        hydration_kg:     {type: REAL,    semantic: "body water in kg (type 77)"}
        heart_pulse_bpm:  {type: INTEGER, semantic: "pulse at weigh-in (type 11); only on Body Cardio / Body Comp / Body Smart"}
        created_at:       {type: TEXT,    semantic: "ISO-8601 record creation time (UTC)"}
        modified_at:      {type: TEXT,    semantic: "ISO-8601 last modification time (UTC); used for cursor"}
related_entities: []
```

- [ ] **Step 3: Create the schema**

Write `src/personal_db/templates/trackers/withings/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS withings_measurements (
  grpid            TEXT PRIMARY KEY,
  date             TEXT NOT NULL,
  timezone         TEXT,
  attrib           INTEGER,
  category         INTEGER,
  device_id        TEXT,
  weight_kg        REAL,
  fat_ratio_pct    REAL,
  fat_mass_kg      REAL,
  lean_mass_kg     REAL,
  muscle_mass_kg   REAL,
  bone_mass_kg     REAL,
  hydration_kg     REAL,
  heart_pulse_bpm  INTEGER,
  created_at       TEXT,
  modified_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_date ON withings_measurements(date);
CREATE INDEX IF NOT EXISTS idx_withings_measurements_modified_at ON withings_measurements(modified_at);
```

- [ ] **Step 4: Verify the manifest parses**

Run:
```bash
.venv/bin/python -c "from pathlib import Path; from personal_db.manifest import load_manifest; m = load_manifest(Path('src/personal_db/templates/trackers/withings/manifest.yaml')); print(m.name, [s.type for s in m.setup_steps])"
```
Expected: `withings ['env_var', 'env_var', 'oauth']`

- [ ] **Step 5: Verify the tracker is auto-discovered**

Run:
```bash
.venv/bin/python -c "from personal_db.installer import list_bundled; print('withings' in list_bundled())"
```
Expected: `True`

- [ ] **Step 6: Commit**

```bash
git add src/personal_db/templates/trackers/withings/__init__.py \
        src/personal_db/templates/trackers/withings/manifest.yaml \
        src/personal_db/templates/trackers/withings/schema.sql
git commit -m "feat(withings): add tracker manifest and schema"
```

---

## Task 9: WithingsAdapter (`oauth_adapter.py`)

**Files:**
- Create: `src/personal_db/templates/trackers/withings/oauth_adapter.py`
- Create: `tests/unit/test_withings_tracker.py`

- [ ] **Step 1: Write failing tests for the adapter**

Create `tests/unit/test_withings_tracker.py`:

```python
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

WITHINGS_DIR = Path(__file__).parent.parent.parent / "src" / "personal_db" / "templates" / "trackers" / "withings"


def _load_adapter_class():
    """Load WithingsAdapter the same way ensure_adapter_from_manifest does."""
    spec = importlib.util.spec_from_file_location(
        "withings_oauth_adapter_test", WITHINGS_DIR / "oauth_adapter.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.WithingsAdapter


@patch("requests.post")
def test_withings_adapter_exchange_code_unwraps_envelope(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": 0,
            "body": {
                "access_token": "AT",
                "refresh_token": "RT",
                "expires_in": 10800,
                "userid": 1234,
                "scope": "user.metrics",
                "token_type": "Bearer",
            },
        },
    )
    mock_post.return_value.raise_for_status = MagicMock()

    token = cls().exchange_code(
        token_url="ignored",
        client_id="CID",
        client_secret="CS",
        code="ABC",
        redirect_uri="http://localhost:9877/callback",
    )
    assert token["access_token"] == "AT"
    assert token["refresh_token"] == "RT"
    assert token["expires_in"] == 10800
    args, kwargs = mock_post.call_args
    assert args[0] == "https://wbsapi.withings.net/v2/oauth2"
    body = kwargs["data"]
    assert body["action"] == "requesttoken"
    assert body["grant_type"] == "authorization_code"
    assert body["code"] == "ABC"


@patch("requests.post")
def test_withings_adapter_refresh_token_unwraps_envelope(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "status": 0,
            "body": {
                "access_token": "AT2",
                "refresh_token": "RT2",
                "expires_in": 10800,
            },
        },
    )
    mock_post.return_value.raise_for_status = MagicMock()

    token = cls().refresh_token(
        token_url="ignored",
        client_id="CID",
        client_secret="CS",
        refresh_token="OLD_RT",
    )
    assert token["access_token"] == "AT2"
    body = mock_post.call_args.kwargs["data"]
    assert body["action"] == "requesttoken"
    assert body["grant_type"] == "refresh_token"
    assert body["refresh_token"] == "OLD_RT"


@patch("requests.post")
def test_withings_adapter_raises_on_nonzero_status(mock_post):
    cls = _load_adapter_class()
    mock_post.return_value = MagicMock(
        status_code=200,
        json=lambda: {"status": 401, "error": "invalid_token"},
    )
    mock_post.return_value.raise_for_status = MagicMock()

    with pytest.raises(RuntimeError, match="Withings token error"):
        cls().refresh_token(
            token_url="ignored",
            client_id="CID",
            client_secret="CS",
            refresh_token="X",
        )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v`
Expected: FAIL — `oauth_adapter.py` doesn't exist yet.

- [ ] **Step 3: Implement `WithingsAdapter`**

Create `src/personal_db/templates/trackers/withings/oauth_adapter.py`:

```python
"""Withings-specific OAuth token adapter.

Withings deviates from RFC 6749 in two ways: (1) every token request needs
an extra `action=requesttoken` form param; (2) responses are wrapped in
`{"status": 0, "body": {...}}` and a non-zero status means error.

This adapter handles both, returning a flat token dict in the shape that
personal_db.oauth expects (access_token / refresh_token / expires_in).
"""

from __future__ import annotations

from typing import Any

import requests


class WithingsAdapter:
    TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

    def exchange_code(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        code: str,
        redirect_uri: str,
    ) -> dict[str, Any]:
        return self._post(
            {
                "action": "requesttoken",
                "grant_type": "authorization_code",
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            }
        )

    def refresh_token(
        self,
        *,
        token_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, Any]:
        return self._post(
            {
                "action": "requesttoken",
                "grant_type": "refresh_token",
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
            }
        )

    def _post(self, data: dict) -> dict[str, Any]:
        r = requests.post(self.TOKEN_URL, data=data, timeout=10)
        r.raise_for_status()
        envelope = r.json()
        if envelope.get("status") != 0:
            raise RuntimeError(f"Withings token error: {envelope}")
        body = envelope.get("body") or {}
        return {
            "access_token": body["access_token"],
            "refresh_token": body["refresh_token"],
            "expires_in": int(body.get("expires_in", 10800)),
            "userid": body.get("userid"),
            "scope": body.get("scope"),
            "token_type": body.get("token_type", "Bearer"),
        }
```

- [ ] **Step 4: Run adapter tests**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v`
Expected: PASS — all three adapter tests.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/withings/oauth_adapter.py tests/unit/test_withings_tracker.py
git commit -m "feat(withings): add WithingsAdapter for non-standard token flow"
```

---

## Task 10: Withings ingest — `_flatten` and helpers

**Files:**
- Create: `src/personal_db/templates/trackers/withings/ingest.py` (initial form, with helpers; sync/backfill come in Task 11)
- Modify: `tests/unit/test_withings_tracker.py`

- [ ] **Step 1: Write failing tests for `_flatten`**

Append to `tests/unit/test_withings_tracker.py`:

```python
def _load_ingest_module():
    spec = importlib.util.spec_from_file_location(
        "withings_ingest_test", WITHINGS_DIR / "ingest.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_flatten_full_measuregrp():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 12345,
        "attrib": 0,
        "date": 1746752400,        # 2025-05-09T00:20:00Z (well-defined epoch)
        "created": 1746752400,
        "modified": 1746752400,
        "category": 1,
        "deviceid": "abc",
        "timezone": "America/Los_Angeles",
        "measures": [
            {"value": 80123, "type": 1, "unit": -3},   # weight 80.123 kg
            {"value": 18234, "type": 6, "unit": -3},   # fat ratio 18.234 %
            {"value": 14567, "type": 8, "unit": -3},   # fat mass 14.567 kg
            {"value": 65556, "type": 5, "unit": -3},   # lean mass 65.556 kg
            {"value": 60123, "type": 76, "unit": -3},  # muscle 60.123 kg
            {"value": 3210,  "type": 88, "unit": -3},  # bone 3.210 kg
            {"value": 45678, "type": 77, "unit": -3},  # hydration 45.678 kg
            {"value": 72,    "type": 11, "unit": 0},   # heart pulse 72 bpm
        ],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["grpid"] == "12345"
    assert row["timezone"] == "America/Los_Angeles"
    assert row["attrib"] == 0
    assert row["category"] == 1
    assert row["device_id"] == "abc"
    assert row["weight_kg"] == 80.123
    assert row["fat_ratio_pct"] == 18.234
    assert row["fat_mass_kg"] == 14.567
    assert row["lean_mass_kg"] == 65.556
    assert row["muscle_mass_kg"] == 60.123
    assert row["bone_mass_kg"] == 3.210
    assert row["hydration_kg"] == 45.678
    assert row["heart_pulse_bpm"] == 72
    assert row["date"].endswith("+00:00")
    assert row["_modified_unix"] == 1746752400


def test_flatten_partial_only_weight():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 99,
        "attrib": 0,
        "date": 1746752400,
        "created": 1746752400,
        "modified": 1746752400,
        "category": 1,
        "deviceid": "abc",
        "timezone": "UTC",
        "measures": [{"value": 75000, "type": 1, "unit": -3}],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["weight_kg"] == 75.0
    assert row["fat_ratio_pct"] is None
    assert row["fat_mass_kg"] is None
    assert row["lean_mass_kg"] is None
    assert row["heart_pulse_bpm"] is None


def test_flatten_unknown_measure_type_dropped():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 1, "attrib": 0, "date": 1746752400, "created": 1746752400,
        "modified": 1746752400, "category": 1, "deviceid": "x", "timezone": "UTC",
        "measures": [
            {"value": 80000, "type": 1, "unit": -3},
            {"value": 999,   "type": 4242, "unit": 0},  # unknown type
        ],
    }
    row = ingest._flatten(grp, default_tz="UTC")
    assert row["weight_kg"] == 80.0
    assert "4242" not in row  # not stored as a column


def test_flatten_timezone_fallback_to_default():
    ingest = _load_ingest_module()
    grp = {
        "grpid": 1, "attrib": 0, "date": 1746752400, "created": 1746752400,
        "modified": 1746752400, "category": 1, "deviceid": "x",
        # no per-row timezone field
        "measures": [{"value": 80000, "type": 1, "unit": -3}],
    }
    row = ingest._flatten(grp, default_tz="America/New_York")
    assert row["timezone"] == "America/New_York"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v`
Expected: FAIL — `ingest.py` doesn't exist.

- [ ] **Step 3: Implement initial `ingest.py` with helpers**

Create `src/personal_db/templates/trackers/withings/ingest.py`:

```python
"""Withings smart-scale ingest.

Pulls body-composition measurements (weight, fat %, fat mass, lean/muscle/
bone mass, hydration, heart pulse) via the Measure v2 API. Uses the
Withings `lastupdate` parameter as a cursor so corrections to historical
weigh-ins are picked up on the next sync.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

import requests

from personal_db.oauth import refresh_if_needed
from personal_db.tracker import Cursor, Tracker

MEASURE_URL = "https://wbsapi.withings.net/measure"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"

# Withings measure types we store. Anything else in the response is ignored.
TYPE_MAP: dict[int, str] = {
    1:  "weight_kg",
    5:  "lean_mass_kg",
    6:  "fat_ratio_pct",
    8:  "fat_mass_kg",
    11: "heart_pulse_bpm",
    76: "muscle_mass_kg",
    77: "hydration_kg",
    88: "bone_mass_kg",
}

# Comma-separated CSV passed to the API so we only fetch what we store.
MEAS_TYPES_CSV = ",".join(str(k) for k in TYPE_MAP.keys())


def _client_credentials() -> tuple[str, str]:
    cid = os.environ.get("WITHINGS_CLIENT_ID")
    cs = os.environ.get("WITHINGS_CLIENT_SECRET")
    if not cid or not cs:
        raise RuntimeError("Set WITHINGS_CLIENT_ID and WITHINGS_CLIENT_SECRET")
    return cid, cs


def _iso_utc(unix_seconds: int | None) -> str | None:
    if unix_seconds is None:
        return None
    return datetime.fromtimestamp(int(unix_seconds), tz=UTC).isoformat()


def _flatten(grp: dict, default_tz: str) -> dict[str, Any]:
    """Convert one Withings measuregrp into a withings_measurements row.

    The `_modified_unix` field is internal — it's used to compute the
    cursor max in sync(); it must be popped from each row before upsert.
    """
    row: dict[str, Any] = {
        "grpid": str(grp["grpid"]),
        "date": _iso_utc(grp["date"]),
        "timezone": grp.get("timezone") or default_tz,
        "attrib": grp.get("attrib"),
        "category": grp.get("category"),
        "device_id": grp.get("deviceid"),
        "created_at": _iso_utc(grp.get("created")),
        "modified_at": _iso_utc(grp.get("modified")),
        "_modified_unix": int(grp.get("modified") or grp["date"]),
        "weight_kg": None,
        "fat_ratio_pct": None,
        "fat_mass_kg": None,
        "lean_mass_kg": None,
        "muscle_mass_kg": None,
        "bone_mass_kg": None,
        "hydration_kg": None,
        "heart_pulse_bpm": None,
    }
    for m in grp.get("measures") or []:
        col = TYPE_MAP.get(m["type"])
        if not col:
            continue
        scaled = m["value"] * (10 ** m["unit"])
        row[col] = int(scaled) if col == "heart_pulse_bpm" else float(scaled)
    return row


def _fetch_measures(
    access_token: str,
    *,
    lastupdate: str | None,
    offset: int,
) -> dict:
    """One getmeas call. Returns the response `body` dict (envelope-unwrapped).
    Raises RuntimeError on non-zero Withings status."""
    params: dict[str, Any] = {
        "action": "getmeas",
        "meastypes": MEAS_TYPES_CSV,
        "category": 1,
        "offset": offset,
    }
    if lastupdate:
        params["lastupdate"] = lastupdate
    r = requests.post(
        MEASURE_URL,
        data=params,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    r.raise_for_status()
    envelope = r.json()
    if envelope.get("status") != 0:
        raise RuntimeError(f"Withings measure error: status={envelope.get('status')} body={envelope}")
    return envelope.get("body") or {}


def sync(t: Tracker) -> None:
    raise NotImplementedError("filled in Task 11")


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    raise NotImplementedError("filled in Task 11")
```

- [ ] **Step 4: Run flatten tests**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v -k "flatten"`
Expected: PASS — all four `_flatten` tests.

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/withings/ingest.py tests/unit/test_withings_tracker.py
git commit -m "feat(withings): add ingest helpers and _flatten"
```

---

## Task 11: Withings ingest — `sync` and `backfill`

**Files:**
- Modify: `src/personal_db/templates/trackers/withings/ingest.py`
- Modify: `tests/unit/test_withings_tracker.py`

- [ ] **Step 1: Write failing tests for sync behavior**

Append to `tests/unit/test_withings_tracker.py`:

```python
@pytest.fixture
def withings_tracker(tmp_root, monkeypatch):
    """A Tracker pointed at tmp_root with the schema applied and credentials set."""
    from personal_db.config import Config
    from personal_db.db import apply_tracker_schema, init_db
    from personal_db.tracker import Tracker

    cfg = Config(root=tmp_root)
    init_db(cfg.db_path)
    apply_tracker_schema(cfg.db_path, (WITHINGS_DIR / "schema.sql").read_text())
    monkeypatch.setenv("WITHINGS_CLIENT_ID", "CID")
    monkeypatch.setenv("WITHINGS_CLIENT_SECRET", "CS")
    # Persist a non-expiring token so refresh_if_needed returns immediately.
    from personal_db.oauth import save_token
    save_token(cfg, "withings", {
        "access_token": "AT",
        "refresh_token": "RT",
        "expires_at": 9999999999,
    })
    return Tracker(name="withings", cfg=cfg, manifest=None)


def _grp(grpid, modified, weight_kg=80.0):
    return {
        "grpid": grpid, "attrib": 0, "date": modified, "created": modified,
        "modified": modified, "category": 1, "deviceid": "x", "timezone": "UTC",
        "measures": [{"value": int(weight_kg * 1000), "type": 1, "unit": -3}],
    }


def test_sync_first_run_no_cursor(withings_tracker, monkeypatch):
    ingest = _load_ingest_module()
    seen_calls = []

    def fake_fetch(token, *, lastupdate, offset):
        seen_calls.append({"lastupdate": lastupdate, "offset": offset})
        return {
            "timezone": "UTC",
            "more": 0, "offset": 0,
            "measuregrps": [_grp(1, 1746752400, 80.0)],
        }

    monkeypatch.setattr(ingest, "_fetch_measures", fake_fetch)
    ingest.sync(withings_tracker)
    # First run: no lastupdate sent
    assert seen_calls == [{"lastupdate": None, "offset": 0}]
    # Cursor advanced to the modified value
    assert withings_tracker.cursor.get(default=None) is None  # cursor is "withings:measurements", not the tracker default
    from personal_db.tracker import Cursor
    cur = Cursor("withings:measurements", withings_tracker.cfg.state_dir)
    assert cur.get() == "1746752400"


def test_sync_paginates_until_more_is_zero(withings_tracker, monkeypatch):
    ingest = _load_ingest_module()
    pages = [
        {"timezone": "UTC", "more": 1, "offset": 100,
         "measuregrps": [_grp(1, 1746000000, 80.0), _grp(2, 1746100000, 81.0)]},
        {"timezone": "UTC", "more": 0, "offset": 0,
         "measuregrps": [_grp(3, 1746200000, 82.0)]},
    ]
    calls = []

    def fake_fetch(token, *, lastupdate, offset):
        calls.append({"offset": offset})
        return pages.pop(0)

    monkeypatch.setattr(ingest, "_fetch_measures", fake_fetch)
    ingest.sync(withings_tracker)
    assert [c["offset"] for c in calls] == [0, 100]
    # All three rows persisted
    import sqlite3
    rows = sqlite3.connect(withings_tracker.cfg.db_path).execute(
        "SELECT grpid, weight_kg FROM withings_measurements ORDER BY grpid"
    ).fetchall()
    assert rows == [("1", 80.0), ("2", 81.0), ("3", 82.0)]
    # Cursor is the max modified value across all pages
    from personal_db.tracker import Cursor
    cur = Cursor("withings:measurements", withings_tracker.cfg.state_dir)
    assert cur.get() == "1746200000"


def test_sync_incremental_passes_lastupdate(withings_tracker, monkeypatch):
    ingest = _load_ingest_module()
    # Pre-set the cursor as if a prior sync ran.
    from personal_db.tracker import Cursor
    Cursor("withings:measurements", withings_tracker.cfg.state_dir).set("1700000000")

    seen = {}

    def fake_fetch(token, *, lastupdate, offset):
        seen["lastupdate"] = lastupdate
        return {"timezone": "UTC", "more": 0, "offset": 0, "measuregrps": []}

    monkeypatch.setattr(ingest, "_fetch_measures", fake_fetch)
    ingest.sync(withings_tracker)
    assert seen["lastupdate"] == "1700000000"


def test_sync_drops_internal_modified_unix_field(withings_tracker, monkeypatch):
    """_modified_unix must not leak into the SQL insert (no such column)."""
    ingest = _load_ingest_module()

    def fake_fetch(token, *, lastupdate, offset):
        return {"timezone": "UTC", "more": 0, "offset": 0,
                "measuregrps": [_grp(99, 1746752400, 80.0)]}

    monkeypatch.setattr(ingest, "_fetch_measures", fake_fetch)
    # Should not raise (would raise sqlite3.OperationalError if _modified_unix leaked).
    ingest.sync(withings_tracker)


def test_backfill_is_an_alias_for_sync(withings_tracker, monkeypatch):
    ingest = _load_ingest_module()
    called = []

    def fake_sync(t):
        called.append(t)

    monkeypatch.setattr(ingest, "sync", fake_sync)
    ingest.backfill(withings_tracker, start=None, end=None)
    assert called == [withings_tracker]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v -k "sync or backfill"`
Expected: FAIL — `sync` raises `NotImplementedError`.

- [ ] **Step 3: Implement `sync` and `backfill`**

In `src/personal_db/templates/trackers/withings/ingest.py`, replace the placeholder `sync` and `backfill` with:

```python
def sync(t: Tracker) -> None:
    cid, cs = _client_credentials()
    token = refresh_if_needed(
        t.cfg,
        "withings",
        token_url=TOKEN_URL,
        client_id=cid,
        client_secret=cs,
    )
    access_token = token["access_token"]
    cursor = Cursor("withings:measurements", t.cfg.state_dir)

    rows: list[dict] = []
    offset = 0
    while True:
        body = _fetch_measures(
            access_token,
            lastupdate=cursor.get(),
            offset=offset,
        )
        default_tz = body.get("timezone") or "UTC"
        grps = body.get("measuregrps") or []
        for grp in grps:
            rows.append(_flatten(grp, default_tz))
        if not body.get("more"):
            break
        if not grps:
            # `more=1` lying about a non-empty next page; bail safely.
            break
        offset = body.get("offset") or (offset + len(grps))

    if rows:
        max_mod = max(r["_modified_unix"] for r in rows)
        # Strip the internal field before upsert.
        for r in rows:
            r.pop("_modified_unix", None)
        t.upsert("withings_measurements", rows, key=["grpid"])
        cursor.set(str(max_mod))
    t.log.info("withings measurements: %d", len(rows))


def backfill(t: Tracker, start: str | None, end: str | None) -> None:
    """Withings has no separate backfill endpoint — `sync` with no cursor is
    already a full backfill. The (start, end) args are accepted for interface
    compatibility but ignored."""
    sync(t)
```

- [ ] **Step 4: Run all withings tests**

Run: `.venv/bin/python -m pytest tests/unit/test_withings_tracker.py -v`
Expected: PASS — all tests (adapter + flatten + sync + backfill).

- [ ] **Step 5: Commit**

```bash
git add src/personal_db/templates/trackers/withings/ingest.py tests/unit/test_withings_tracker.py
git commit -m "feat(withings): implement sync and backfill"
```

---

## Task 12: Withings visualizations

**Files:**
- Create: `src/personal_db/templates/trackers/withings/visualizations.py`

Visualizations are best validated by eye in the dashboard, so this task has no unit tests beyond a smoke test that `list_visualizations()` returns the right shape.

- [ ] **Step 1: Write the visualizations module**

Create `src/personal_db/templates/trackers/withings/visualizations.py`:

```python
"""Visualizations for the withings tracker."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from personal_db.config import Config
from personal_db.ui.charts import vertical_bars


def _connect(cfg: Config) -> sqlite3.Connection | None:
    try:
        return sqlite3.connect(cfg.db_path)
    except sqlite3.OperationalError:
        return None


def render_weight_trend_180d(cfg: Config) -> str:
    """Daily weight (kg) over the last 180 days. Manual entries excluded.

    If there are multiple weigh-ins in a day, the latest one wins."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=179)).isoformat()
    try:
        rows = dict(con.execute(
            "SELECT date(date) AS d, weight_kg "
            "FROM withings_measurements "
            "WHERE date >= ? AND weight_kg IS NOT NULL "
            "  AND attrib NOT IN (2, 4) "
            "GROUP BY d "
            "HAVING date = MAX(date)",
            (cutoff,),
        ).fetchall())
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()

    items = []
    for i in range(179, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        items.append((d[5:], rows.get(d, 0)))

    return (
        '<p class="meta">withings weight (kg) · last 180 days · device measurements only</p>'
        + vertical_bars(items, color="#3a6ea8", show_every_nth_label=30)
    )


def render_body_composition_30d(cfg: Config) -> str:
    """Last 30 days. Bars show fat_mass_kg and lean_mass_kg side by side per day.

    The two together account for total body weight on most Withings scales,
    so the visual answers 'is recent weight change fat or lean?'."""
    con = _connect(cfg)
    if not con:
        return '<p class="meta">no data</p>'
    today = datetime.now().date()
    cutoff = (today - timedelta(days=29)).isoformat()
    try:
        rows = con.execute(
            "SELECT date(date) AS d, "
            "       MAX(fat_mass_kg)  AS fat, "
            "       MAX(lean_mass_kg) AS lean "
            "FROM withings_measurements "
            "WHERE date >= ? AND attrib NOT IN (2, 4) "
            "GROUP BY d",
            (cutoff,),
        ).fetchall()
    except sqlite3.OperationalError:
        return '<p class="meta">withings_measurements not synced yet</p>'
    finally:
        con.close()
    by_day = {row[0]: (row[1], row[2]) for row in rows}

    fat_items = []
    lean_items = []
    for i in range(29, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        fat, lean = by_day.get(d, (0, 0))
        fat_items.append((d[5:], fat or 0))
        lean_items.append((d[5:], lean or 0))

    return (
        '<p class="meta">withings body composition · last 30 days · '
        '<span style="color:#cc6644">fat mass kg</span> &amp; '
        '<span style="color:#3a8a4a">lean mass kg</span></p>'
        + '<div style="margin-bottom:0.5em">'
        + vertical_bars(fat_items, color="#cc6644", show_every_nth_label=5)
        + '</div>'
        + vertical_bars(lean_items, color="#3a8a4a", show_every_nth_label=5)
    )


def list_visualizations() -> list[dict]:
    return [
        {
            "slug": "weight_trend_180d",
            "name": "Weight Trend (180d)",
            "description": "Daily weight in kilograms over the last 180 days, device measurements only.",
            "render": render_weight_trend_180d,
        },
        {
            "slug": "body_composition_30d",
            "name": "Body Composition (30d)",
            "description": "Fat mass vs lean mass, day by day, over the last 30 days.",
            "render": render_body_composition_30d,
        },
    ]
```

- [ ] **Step 2: Smoke test the module loads and lists viz**

Run:
```bash
.venv/bin/python -c "
import importlib.util
from pathlib import Path
p = Path('src/personal_db/templates/trackers/withings/visualizations.py')
spec = importlib.util.spec_from_file_location('w_viz', p)
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
viz = m.list_visualizations()
assert len(viz) == 2
assert {v['slug'] for v in viz} == {'weight_trend_180d', 'body_composition_30d'}
print('ok')
"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/personal_db/templates/trackers/withings/visualizations.py
git commit -m "feat(withings): add weight-trend and body-composition visualizations"
```

---

## Task 13: End-to-end install + smoke test

**Files:**
- (no source changes — runs the existing smoke tests against the new tracker)

- [ ] **Step 1: Run the broader smoke + installer test suite**

Run: `.venv/bin/python -m pytest tests/unit/test_smoke.py tests/unit/test_installer.py tests/unit/test_manifest.py -v`
Expected: every test passes. The smoke test enumerates bundled trackers and validates each manifest — `withings` is now in the list.

- [ ] **Step 2: Install the tracker into the user's real `~/personal_db` root and verify schema applies**

Run:
```bash
.venv/bin/personal-db --root ~/personal_db tracker install withings
```
Expected output: includes `installed: withings` (or equivalent — the wizard normally runs setup steps but the tracker can be installed without completing OAuth).

If the user has already manually started this install before reaching this step, the equivalent `tracker reinstall withings` is the right command per `CLAUDE.md`.

- [ ] **Step 3: Verify table was created**

Run:
```bash
sqlite3 ~/personal_db/db.sqlite ".schema withings_measurements"
```
Expected: outputs the `CREATE TABLE withings_measurements (...)` and the two indexes.

- [ ] **Step 4: Run the full unit-test suite to catch regressions**

Run: `.venv/bin/python -m pytest tests/unit/ -q`
Expected: all green.

- [ ] **Step 5: Commit any final adjustments**

If steps 2-3 surfaced issues that needed code tweaks, commit them as `fix(withings): <reason>`. If everything was clean, no commit required for this task.

---

## Self-Review

**1. Spec coverage:**
- §Goal (scale data from Withings) → Tasks 8-12.
- §Framework changes #1 (TokenAdapter + registry) → Task 1.
- §Framework changes #1 (route exchange_code/refresh_if_needed) → Tasks 2, 3.
- §Framework changes #2 (`adapter` field on OAuthStep) → Task 4.
- §Framework changes #3 (setup wizard) → Task 5 (CLI), Task 6 (daemon HTTP route).
- §WithingsAdapter → Task 9.
- §Manifest → Task 8.
- §Schema → Task 8.
- §Sync algorithm + cursor + backfill → Tasks 10, 11.
- §Flatten → Task 10.
- §Error handling (status != 0, missing creds, unknown measure types) → Tasks 9, 10, 11 (covered in tests).
- §Visualizations (`weight_trend_180d`, `body_composition_30d`) → Task 12.
- §Testing (13 enumerated unit tests in spec) → covered: tests 1-3 in Task 9, tests 4-6 in Tasks 1+4 (registry + ensure-adapter), tests 7-10 in Task 10 (`_flatten`), tests 11-13 in Task 11 (sync pagination/cursor). Also Task 7 adds a sync_one integration test that exercises the manifest-driven adapter registration.
- §End-to-end install verification → Task 13.
- Coverage gaps: none. Task 7's adapter-registration-in-sync test plus Task 4's helper test both cover the wiring; Task 11's "drops _modified_unix" test catches the internal-field leak.

**2. Placeholder scan:** No "TBD", "TODO", or "fill in details" remain. Tasks 5 and 6 are wiring-only and have no new test code, but each has explicit code/diff and verification steps. Task 13 has no commit step in the success path because it's pure verification.

**3. Type consistency:**
- `TokenAdapter` Protocol methods (`exchange_code`, `refresh_token`) match `StandardAdapter` and `WithingsAdapter` signatures.
- `register_adapter(provider, adapter)` / `_adapter_for(provider)` consistent across Tasks 1-4.
- `ensure_adapter_from_manifest(tracker_dir, step)` signature consistent across Tasks 4, 5, 6, 7.
- `_flatten(grp, default_tz)` and `_fetch_measures(access_token, *, lastupdate, offset)` consistent across Tasks 10, 11.
- `Cursor("withings:measurements", t.cfg.state_dir)` keyed identically in `sync` and the test.
- Manifest `adapter: oauth_adapter:WithingsAdapter` matches the file `oauth_adapter.py` and class `WithingsAdapter` (Task 8 ↔ Task 9).
- Schema columns in `manifest.yaml` (Task 8) ↔ `schema.sql` (Task 8) ↔ `_flatten` row keys (Task 10) — all match (`grpid`, `date`, `timezone`, `attrib`, `category`, `device_id`, eight measure columns, `created_at`, `modified_at`).

No inconsistencies found.
