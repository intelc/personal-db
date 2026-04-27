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


def sync_one(cfg: Config, name: str) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.sync(t)
    _write_last_run(cfg, name, datetime.now(UTC).isoformat())
    _store_horizon(cfg, name, manifest)


def backfill_one(cfg: Config, name: str, start: str | None, end: str | None) -> None:
    tracker_dir = cfg.trackers_dir / name
    manifest = load_manifest(tracker_dir / "manifest.yaml")
    _ensure_schema(cfg, tracker_dir)
    mod = _load_ingest_module(tracker_dir, name)
    t = Tracker(name=name, cfg=cfg, manifest=manifest)
    mod.backfill(t, start, end)
    _store_horizon(cfg, name, manifest)


def sync_due(cfg: Config) -> dict[str, str]:
    """Run every due tracker. Returns {name: 'ok'|'<error>'}."""
    results: dict[str, str] = {}
    for tracker_dir in sorted(cfg.trackers_dir.iterdir()):
        if not tracker_dir.is_dir():
            continue
        name = tracker_dir.name
        try:
            if _is_due(cfg, name):
                sync_one(cfg, name)
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
