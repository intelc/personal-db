"""Run a tracker's setup_steps in order, then a test sync, then persist status."""

from __future__ import annotations

from dataclasses import dataclass

from personal_db import backfill as backfill_mod
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
            print(f"    ok: {result.detail}")

    # Test sync
    print(f"\n  Running test sync for {name}...")
    try:
        sync_one(cfg, name)
    except Exception as e:
        detail = f"test sync failed: {e}"
        write_status(cfg, name, success=False, detail=detail)
        print(f"    failed: {detail}")
        return RunResult(success=False, detail=detail)
    detail = "test sync passed"
    write_status(cfg, name, success=True, detail=detail)
    print(f"    ok: {detail}")

    # Kick off historical backfill in a detached subprocess so the user
    # doesn't wait. Manual-only trackers (habits, life_context) have no-op
    # backfills that exit immediately; the small subprocess overhead is
    # acceptable for the simpler code path.
    log_path = backfill_mod.start_async(cfg, name)
    print(f"    ✓ backfill running in background → {log_path}")

    return RunResult(success=True, detail=detail)
