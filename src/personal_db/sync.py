from __future__ import annotations

import importlib.util
import json
import re
import sys
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path

from personal_db.config import Config
from personal_db.data_horizon import compute_and_store as _store_horizon
from personal_db.db import apply_tracker_schema, init_db
from personal_db.manifest import load_manifest
from personal_db.tracker import Tracker
from personal_db.transforms import TransformError, make_context, topo_sort, validate

_EVERY_RE = re.compile(r"^(\d+)\s*([smhd])$")


def _parse_every(s: str) -> timedelta:
    m = _EVERY_RE.match(s.strip())
    if not m:
        raise ValueError(f"bad schedule.every: {s!r}")
    n, unit = int(m.group(1)), m.group(2)
    return {
        "s": timedelta(seconds=n),
        "m": timedelta(minutes=n),
        "h": timedelta(hours=n),
        "d": timedelta(days=n),
    }[unit]


def _load_ingest_module(tracker_dir: Path, name: str):
    """Load ingest.py fresh on every call. Drop any prior cached version so
    tests that re-create trackers under tmp_path don't see stale code."""
    spec_name = f"personal_db_trackers_{name}"
    sys.modules.pop(spec_name, None)
    spec = importlib.util.spec_from_file_location(spec_name, tracker_dir / "ingest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _last_run_path(cfg: Config) -> Path:
    return cfg.state_dir / "last_run.json"


def _read_last_run(cfg: Config) -> dict[str, str]:
    p = _last_run_path(cfg)
    return json.loads(p.read_text()) if p.exists() else {}


def _write_last_run(cfg: Config, name: str, ts: str) -> None:
    data = _read_last_run(cfg)
    data[name] = ts
    _last_run_path(cfg).write_text(json.dumps(data, indent=2))


def _is_due(cfg: Config, name: str) -> bool:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    if not manifest.schedule or not manifest.schedule.every:
        return False  # cron schedules: launchd handles cadence; we always run when called
    last = _read_last_run(cfg).get(name)
    if not last:
        return True
    delta = _parse_every(manifest.schedule.every)
    return datetime.fromisoformat(last) + delta <= datetime.now(UTC)


def _ensure_schema(cfg: Config, tracker_dir: Path) -> None:
    init_db(cfg.db_path)
    schema_sql = (tracker_dir / "schema.sql").read_text()
    apply_tracker_schema(cfg.db_path, schema_sql)


def _extract_schema_tables(schema_sql: str) -> set[str]:
    """Pull table names out of CREATE TABLE statements in schema.sql.

    Used to validate that every @transform's `writes` and `depends_on` refer
    to tables actually declared in the tracker's schema.
    """
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?(\w+)[\"`]?",
        re.IGNORECASE,
    )
    return set(pattern.findall(schema_sql))


def _record_transform_error(cfg: Config, tracker: str, transform_name: str, err: Exception) -> None:
    err_path = cfg.state_dir / "sync_errors.jsonl"
    with err_path.open("a") as f:
        f.write(
            json.dumps(
                {
                    "ts": datetime.now(UTC).isoformat(),
                    "tracker": tracker,
                    "transform": transform_name,
                    "error": str(err),
                    "tb": traceback.format_exc(),
                }
            )
            + "\n"
        )


def _run_transforms(cfg: Config, name: str, mod, tracker_dir: Path) -> None:
    """Discover @transform functions in `mod`, validate the DAG, and run in topo order.

    Errors per-transform are caught and logged to sync_errors.jsonl; downstream
    transforms whose deps failed are skipped, but independent branches still run.
    """
    specs = [
        v._transform_spec
        for v in vars(mod).values()
        if hasattr(v, "_transform_spec")
    ]
    if not specs:
        return

    schema_sql = (tracker_dir / "schema.sql").read_text()
    schema_tables = _extract_schema_tables(schema_sql)

    try:
        validate(specs, schema_tables=schema_tables)
    except TransformError as e:
        _record_transform_error(cfg, name, "<validation>", e)
        return

    manifest = load_manifest(tracker_dir / "manifest.yaml")
    t = Tracker(name=name, cfg=cfg, manifest=manifest)

    failed_writes: set[str] = set()
    for spec in topo_sort(specs):
        if any(d in failed_writes for d in spec.depends_on):
            # Upstream transform failed this tick; skip downstream.
            continue
        ctx = make_context(t, spec)
        try:
            spec.fn(t, ctx)
        except Exception as e:
            failed_writes.add(spec.writes)
            _record_transform_error(cfg, name, spec.name, e)
        finally:
            ctx.con.close()


def sync_one(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.sync(t)
    _run_transforms(cfg, name, mod, tracker_dir)
    _write_last_run(cfg, name, datetime.now(UTC).isoformat())
    _store_horizon(cfg, name, manifest)


def backfill_one(cfg: Config, name: str, start: str | None, end: str | None) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.backfill(t, start, end)
    _run_transforms(cfg, name, mod, tracker_dir)
    _store_horizon(cfg, name, manifest)


def sync_due(cfg: Config, sync_one_fn=None) -> dict[str, str]:
    """Run every due tracker. Returns {name: 'ok'|'<error>'}.

    ``sync_one_fn`` defaults to :func:`sync_one`. Pass a wrapper (e.g. a
    lock-acquiring variant) to intercept each per-tracker call without
    changing the scheduling or error-recording logic.
    """
    if sync_one_fn is None:
        sync_one_fn = sync_one
    results: dict[str, str] = {}
    for tracker_dir in sorted(cfg.trackers_dir.iterdir()):
        if not tracker_dir.is_dir():
            continue
        name = tracker_dir.name
        try:
            if _is_due(cfg, name):
                sync_one_fn(cfg, name)
                results[name] = "ok"
            else:
                results[name] = "skip"
        except Exception as e:
            results[name] = f"error: {e}"
            err_path = cfg.state_dir / "sync_errors.jsonl"
            with err_path.open("a") as f:
                f.write(
                    json.dumps(
                        {
                            "ts": datetime.now(UTC).isoformat(),
                            "tracker": name,
                            "error": str(e),
                            "tb": traceback.format_exc(),
                        }
                    )
                    + "\n"
                )
    return results
