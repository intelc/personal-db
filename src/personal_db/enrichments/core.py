from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

from personal_db.config import Config
from personal_db.context_providers.base import EvidenceRef
from personal_db.db import connect

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS enrichment_runs (
  run_id          TEXT PRIMARY KEY,
  enrichment_name TEXT NOT NULL,
  input_table     TEXT NOT NULL,
  input_id        TEXT NOT NULL,
  status          TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  completed_at    TEXT,
  model           TEXT,
  prompt_version  TEXT,
  result_json     TEXT,
  result_summary  TEXT,
  confidence      REAL,
  error           TEXT
);

CREATE INDEX IF NOT EXISTS idx_enrichment_runs_input
  ON enrichment_runs(enrichment_name, input_table, input_id);

CREATE TABLE IF NOT EXISTS enrichment_latest (
  enrichment_name TEXT NOT NULL,
  input_table     TEXT NOT NULL,
  input_id        TEXT NOT NULL,
  run_id          TEXT NOT NULL,
  status          TEXT NOT NULL,
  result_json     TEXT,
  result_summary  TEXT,
  confidence      REAL,
  error           TEXT,
  updated_at      TEXT NOT NULL,
  PRIMARY KEY(enrichment_name, input_table, input_id)
);

CREATE TABLE IF NOT EXISTS enrichment_evidence (
  evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id      TEXT NOT NULL,
  source      TEXT NOT NULL,
  ref         TEXT NOT NULL,
  kind        TEXT NOT NULL,
  title       TEXT,
  excerpt     TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_enrichment_evidence_run
  ON enrichment_evidence(run_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_evidence_ref
  ON enrichment_evidence(source, ref);

CREATE TABLE IF NOT EXISTS enrichment_jobs (
  job_id          TEXT PRIMARY KEY,
  enrichment_name TEXT NOT NULL,
  input_table     TEXT NOT NULL,
  input_id        TEXT NOT NULL,
  status          TEXT NOT NULL DEFAULT 'pending',
  priority        INTEGER NOT NULL DEFAULT 100,
  run_after       TEXT NOT NULL,
  payload_json    TEXT,
  attempts        INTEGER NOT NULL DEFAULT 0,
  max_attempts    INTEGER NOT NULL DEFAULT 3,
  last_run_id     TEXT,
  last_error      TEXT,
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL,
  locked_at       TEXT,
  lease_until     TEXT,
  failed_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_due
  ON enrichment_jobs(status, run_after, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_enrichment_jobs_input
  ON enrichment_jobs(enrichment_name, input_table, input_id);
"""


@dataclass(frozen=True)
class EnrichmentRunRecord:
    enrichment_name: str
    input_table: str
    input_id: str
    status: str
    result: dict[str, Any] | None = None
    evidence: list[EvidenceRef] = field(default_factory=list)
    result_summary: str | None = None
    confidence: float | None = None
    error: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass(frozen=True)
class EnrichmentJob:
    job_id: str
    enrichment_name: str
    input_table: str
    input_id: str
    status: str
    priority: int
    run_after: str
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    created_at: str
    updated_at: str
    last_run_id: str | None = None
    last_error: str | None = None
    locked_at: str | None = None
    lease_until: str | None = None
    failed_at: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "enrichment_name": self.enrichment_name,
            "input_table": self.input_table,
            "input_id": self.input_id,
            "status": self.status,
            "priority": self.priority,
            "run_after": self.run_after,
            "payload": self.payload,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_run_id": self.last_run_id,
            "last_error": self.last_error,
            "locked_at": self.locked_at,
            "lease_until": self.lease_until,
            "failed_at": self.failed_at,
        }


def apply_enrichment_schema(cfg: Config) -> None:
    cfg.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(cfg.db_path)
    try:
        con.executescript(_SCHEMA_SQL)
        _ensure_job_columns(con)
        con.commit()
    finally:
        con.close()


def _ensure_job_columns(con: sqlite3.Connection) -> None:
    cols = {row[1] for row in con.execute("PRAGMA table_info(enrichment_jobs)").fetchall()}
    if "lease_until" not in cols:
        con.execute("ALTER TABLE enrichment_jobs ADD COLUMN lease_until TEXT")
    if "failed_at" not in cols:
        con.execute("ALTER TABLE enrichment_jobs ADD COLUMN failed_at TEXT")


def enrichment_job_id(enrichment_name: str, input_table: str, input_id: str) -> str:
    raw = f"{enrichment_name}\0{input_table}\0{input_id}".encode()
    return sha256(raw).hexdigest()


def enqueue_enrichment_job(
    cfg: Config,
    *,
    enrichment_name: str,
    input_table: str,
    input_id: str,
    priority: int = 100,
    run_after: str | None = None,
    payload: dict[str, Any] | None = None,
    max_attempts: int = 3,
    force: bool = False,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    run_after = run_after or now
    job_id = enrichment_job_id(enrichment_name, input_table, input_id)
    payload_json = json.dumps(payload or {}, sort_keys=True)
    con = connect(cfg.db_path)
    try:
        existing = con.execute(
            """
            SELECT job_id, status
            FROM enrichment_jobs
            WHERE job_id=?
            """,
            (job_id,),
        ).fetchone()
        if existing and existing[1] in {"pending", "running"} and not force:
            row = _job_by_id(con, job_id)
            con.commit()
            return {"created": False, "job": row.as_dict()}
        created = existing is None
        con.execute(
            """
            INSERT INTO enrichment_jobs(
              job_id, enrichment_name, input_table, input_id, status,
              priority, run_after, payload_json, attempts, max_attempts,
              created_at, updated_at, locked_at, lease_until, failed_at, last_error
            )
            VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, 0, ?, ?, ?, NULL, NULL, NULL, NULL)
            ON CONFLICT(job_id) DO UPDATE SET
              status='pending',
              priority=excluded.priority,
              run_after=excluded.run_after,
              payload_json=excluded.payload_json,
              attempts=0,
              max_attempts=excluded.max_attempts,
              updated_at=excluded.updated_at,
              locked_at=NULL,
              lease_until=NULL,
              failed_at=NULL,
              last_error=NULL
            """,
            (
                job_id,
                enrichment_name,
                input_table,
                input_id,
                int(priority),
                run_after,
                payload_json,
                int(max_attempts),
                now,
                now,
            ),
        )
        con.commit()
        return {"created": created, "job": _job_by_id(con, job_id).as_dict()}
    finally:
        con.close()


def list_due_enrichment_jobs(cfg: Config, *, limit: int = 10) -> list[dict[str, Any]]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    con = connect(cfg.db_path, read_only=True)
    try:
        rows = con.execute(
            """
            SELECT job_id, enrichment_name, input_table, input_id, status,
                   priority, run_after, payload_json, attempts, max_attempts,
                   created_at, updated_at, last_run_id, last_error, locked_at,
                   lease_until, failed_at
            FROM enrichment_jobs
            WHERE status='pending' AND run_after <= ?
            ORDER BY priority ASC, created_at ASC
            LIMIT ?
            """,
            (now, int(limit)),
        ).fetchall()
    finally:
        con.close()
    return [_job_from_row(row).as_dict() for row in rows]


def list_enrichment_jobs(
    cfg: Config,
    *,
    status: str | None = None,
    enrichment_name: str | None = None,
    input_table: str | None = None,
    input_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    apply_enrichment_schema(cfg)
    clauses = []
    params: list[Any] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    if enrichment_name:
        clauses.append("enrichment_name=?")
        params.append(enrichment_name)
    if input_table:
        clauses.append("input_table=?")
        params.append(input_table)
    if input_id:
        clauses.append("input_id=?")
        params.append(input_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(int(limit))
    con = connect(cfg.db_path, read_only=True)
    try:
        rows = con.execute(
            f"""
            SELECT job_id, enrichment_name, input_table, input_id, status,
                   priority, run_after, payload_json, attempts, max_attempts,
                   created_at, updated_at, last_run_id, last_error, locked_at,
                   lease_until, failed_at
            FROM enrichment_jobs
            {where}
            ORDER BY
              CASE status
                WHEN 'running' THEN 0
                WHEN 'pending' THEN 1
                WHEN 'failed' THEN 2
                WHEN 'canceled' THEN 3
                ELSE 4
              END,
              priority ASC,
              updated_at DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
    finally:
        con.close()
    return [_job_from_row(row).as_dict() for row in rows]


def enrichment_queue_summary(cfg: Config) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC)
    con = connect(cfg.db_path, read_only=True)
    try:
        status_rows = con.execute(
            """
            SELECT enrichment_name, status, count(*)
            FROM enrichment_jobs
            GROUP BY enrichment_name, status
            ORDER BY enrichment_name, status
            """
        ).fetchall()
        oldest_pending_rows = con.execute(
            """
            SELECT enrichment_name, min(run_after)
            FROM enrichment_jobs
            WHERE status='pending'
            GROUP BY enrichment_name
            """
        ).fetchall()
        latest_run_rows = con.execute(
            """
            SELECT enrichment_name, max(completed_at)
            FROM enrichment_runs
            GROUP BY enrichment_name
            """
        ).fetchall()
        failed_rows = con.execute(
            """
            SELECT job_id, enrichment_name, input_table, input_id, attempts, last_error, updated_at
            FROM enrichment_jobs
            WHERE status='failed'
            ORDER BY updated_at DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        con.close()

    by_enrichment: dict[str, dict[str, Any]] = {}
    for enrichment_name, status, count in status_rows:
        entry = by_enrichment.setdefault(
            enrichment_name,
            {"statuses": {}, "oldest_pending_run_after": None, "oldest_pending_age_seconds": None},
        )
        entry["statuses"][status] = count
    for enrichment_name, run_after in oldest_pending_rows:
        entry = by_enrichment.setdefault(
            enrichment_name,
            {"statuses": {}, "oldest_pending_run_after": None, "oldest_pending_age_seconds": None},
        )
        entry["oldest_pending_run_after"] = run_after
        entry["oldest_pending_age_seconds"] = _age_seconds(now, run_after)
    for enrichment_name, completed_at in latest_run_rows:
        entry = by_enrichment.setdefault(
            enrichment_name,
            {"statuses": {}, "oldest_pending_run_after": None, "oldest_pending_age_seconds": None},
        )
        entry["latest_run_completed_at"] = completed_at
    return {
        "by_enrichment": by_enrichment,
        "failed_jobs": [
            {
                "job_id": row[0],
                "enrichment_name": row[1],
                "input_table": row[2],
                "input_id": row[3],
                "attempts": row[4],
                "last_error": row[5],
                "updated_at": row[6],
            }
            for row in failed_rows
        ],
    }


def get_enrichment_job(cfg: Config, job_id: str) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    con = connect(cfg.db_path, read_only=True)
    try:
        return _job_by_id(con, job_id).as_dict()
    finally:
        con.close()


def get_enrichment_job_detail(cfg: Config, job_id: str) -> dict[str, Any]:
    job = get_enrichment_job(cfg, job_id)
    run = get_enrichment_run(cfg, job["last_run_id"]) if job.get("last_run_id") else None
    latest = get_latest_enrichment(
        cfg,
        job["enrichment_name"],
        job["input_table"],
        job["input_id"],
    )
    return {"job": job, "last_run": run, "latest": latest}


def retry_enrichment_job(
    cfg: Config,
    job_id: str,
    *,
    reset_attempts: bool = True,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    attempts_sql = "attempts=0," if reset_attempts else ""
    con = connect(cfg.db_path)
    try:
        con.execute(
            f"""
            UPDATE enrichment_jobs
            SET status='pending',
                run_after=?,
                {attempts_sql}
                locked_at=NULL,
                lease_until=NULL,
                failed_at=NULL,
                last_error=NULL,
                updated_at=?
            WHERE job_id=?
            """,
            (now, now, job_id),
        )
        if con.total_changes == 0:
            raise ValueError(f"no enrichment job found: {job_id}")
        con.commit()
        return _job_by_id(con, job_id).as_dict()
    finally:
        con.close()


def cancel_enrichment_job(
    cfg: Config,
    job_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    error = f"canceled: {reason}" if reason else "canceled"
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            UPDATE enrichment_jobs
            SET status='canceled',
                last_error=?,
                locked_at=NULL,
                lease_until=NULL,
                failed_at=NULL,
                updated_at=?
            WHERE job_id=?
            """,
            (error, now, job_id),
        )
        if con.total_changes == 0:
            raise ValueError(f"no enrichment job found: {job_id}")
        con.commit()
        return _job_by_id(con, job_id).as_dict()
    finally:
        con.close()


def claim_due_enrichment_jobs(
    cfg: Config,
    *,
    enrichment_name: str | None = None,
    limit: int = 1,
    lease_seconds: int = 300,
) -> list[EnrichmentJob]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    lease_until = (datetime.now(UTC) + timedelta(seconds=int(lease_seconds))).isoformat()
    con = connect(cfg.db_path)
    try:
        where_name = "AND enrichment_name=?" if enrichment_name else ""
        params: list[Any] = [now]
        if enrichment_name:
            params.append(enrichment_name)
        params.append(int(limit))
        ids = [
            row[0]
            for row in con.execute(
                f"""
                SELECT job_id
                FROM enrichment_jobs
                WHERE status='pending'
                  AND run_after <= ?
                  {where_name}
                  AND attempts < max_attempts
                ORDER BY priority ASC, created_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
        ]
        claimed: list[EnrichmentJob] = []
        for job_id in ids:
            cur = con.execute(
                """
                UPDATE enrichment_jobs
                SET status='running',
                    attempts=attempts + 1,
                    locked_at=?,
                    lease_until=?,
                    updated_at=?
                WHERE job_id=?
                  AND status='pending'
                  AND run_after <= ?
                  AND attempts < max_attempts
                """,
                (now, lease_until, now, job_id, now),
            )
            if cur.rowcount:
                claimed.append(_job_by_id(con, job_id))
        con.commit()
        return claimed
    finally:
        con.close()


def mark_enrichment_job_running(
    cfg: Config,
    job_id: str,
    *,
    lease_seconds: int = 300,
) -> EnrichmentJob:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    lease_until = (datetime.now(UTC) + timedelta(seconds=int(lease_seconds))).isoformat()
    con = connect(cfg.db_path)
    try:
        row = con.execute(
            "SELECT status, attempts, max_attempts FROM enrichment_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no enrichment job found: {job_id}")
        if row[0] != "pending":
            raise ValueError(f"enrichment job is not pending: {job_id}")
        if int(row[1]) >= int(row[2]):
            raise ValueError(f"enrichment job has no attempts remaining: {job_id}")
        con.execute(
            """
            UPDATE enrichment_jobs
            SET status='running',
                attempts=attempts + 1,
                locked_at=?,
                lease_until=?,
                updated_at=?
            WHERE job_id=?
            """,
            (now, lease_until, now, job_id),
        )
        con.commit()
        return _job_by_id(con, job_id)
    finally:
        con.close()


def mark_enrichment_job_complete(
    cfg: Config,
    job_id: str,
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    return _mark_job_terminal(cfg, job_id, status="succeeded", run_id=run_id, error=None)


def mark_enrichment_job_failed(
    cfg: Config,
    job_id: str,
    *,
    error: str,
    run_id: str | None = None,
    retry_delay_seconds: int = 60,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    con = connect(cfg.db_path)
    try:
        row = con.execute(
            "SELECT attempts, max_attempts FROM enrichment_jobs WHERE job_id=?",
            (job_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"no enrichment job found: {job_id}")
        status = "failed" if int(row[0]) >= int(row[1]) else "pending"
        failed_at = now if status == "failed" else None
        run_after = (
            (datetime.now(UTC) + timedelta(seconds=int(retry_delay_seconds))).isoformat()
            if status == "pending"
            else now
        )
        con.execute(
            """
            UPDATE enrichment_jobs
            SET status=?,
                run_after=?,
                last_run_id=COALESCE(?, last_run_id),
                last_error=?,
                failed_at=?,
                locked_at=NULL,
                lease_until=NULL,
                updated_at=?
            WHERE job_id=?
            """,
            (status, run_after, run_id, error, failed_at, now, job_id),
        )
        con.commit()
        return _job_by_id(con, job_id).as_dict()
    finally:
        con.close()


def _mark_job_terminal(
    cfg: Config,
    job_id: str,
    *,
    status: str,
    run_id: str | None,
    error: str | None,
) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            UPDATE enrichment_jobs
            SET status=?,
                last_run_id=COALESCE(?, last_run_id),
                last_error=?,
                locked_at=NULL,
                lease_until=NULL,
                failed_at=NULL,
                updated_at=?
            WHERE job_id=?
            """,
            (status, run_id, error, now, job_id),
        )
        if con.total_changes == 0:
            raise ValueError(f"no enrichment job found: {job_id}")
        con.commit()
        return _job_by_id(con, job_id).as_dict()
    finally:
        con.close()


def record_enrichment_run(cfg: Config, record: EnrichmentRunRecord) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    result_json = json.dumps(record.result, sort_keys=True) if record.result is not None else None
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            INSERT INTO enrichment_runs(
              run_id, enrichment_name, input_table, input_id, status,
              started_at, completed_at, model, prompt_version, result_json,
              result_summary, confidence, error
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.enrichment_name,
                record.input_table,
                record.input_id,
                record.status,
                record.started_at,
                record.completed_at,
                record.model,
                record.prompt_version,
                result_json,
                record.result_summary,
                record.confidence,
                record.error,
            ),
        )
        con.execute(
            """
            INSERT INTO enrichment_latest(
              enrichment_name, input_table, input_id, run_id, status,
              result_json, result_summary, confidence, error, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(enrichment_name, input_table, input_id) DO UPDATE SET
              run_id=excluded.run_id,
              status=excluded.status,
              result_json=excluded.result_json,
              result_summary=excluded.result_summary,
              confidence=excluded.confidence,
              error=excluded.error,
              updated_at=excluded.updated_at
            """,
            (
                record.enrichment_name,
                record.input_table,
                record.input_id,
                record.run_id,
                record.status,
                result_json,
                record.result_summary,
                record.confidence,
                record.error,
                record.completed_at,
            ),
        )
        con.executemany(
            """
            INSERT INTO enrichment_evidence(run_id, source, ref, kind, title, excerpt)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    record.run_id,
                    ref.source,
                    ref.ref,
                    ref.kind,
                    ref.title,
                    ref.excerpt,
                )
                for ref in record.evidence
            ],
        )
        con.commit()
    finally:
        con.close()
    return {
        "run_id": record.run_id,
        "enrichment_name": record.enrichment_name,
        "input_table": record.input_table,
        "input_id": record.input_id,
        "status": record.status,
        "result": record.result,
        "evidence": [ref.as_dict() for ref in record.evidence],
        "result_summary": record.result_summary,
        "confidence": record.confidence,
        "error": record.error,
    }


def _job_by_id(con: sqlite3.Connection, job_id: str) -> EnrichmentJob:
    row = con.execute(
        """
        SELECT job_id, enrichment_name, input_table, input_id, status,
               priority, run_after, payload_json, attempts, max_attempts,
               created_at, updated_at, last_run_id, last_error, locked_at,
               lease_until, failed_at
        FROM enrichment_jobs
        WHERE job_id=?
        """,
        (job_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no enrichment job found: {job_id}")
    return _job_from_row(row)


def _job_from_row(row) -> EnrichmentJob:
    payload_json = row[7]
    return EnrichmentJob(
        job_id=row[0],
        enrichment_name=row[1],
        input_table=row[2],
        input_id=row[3],
        status=row[4],
        priority=row[5],
        run_after=row[6],
        payload=json.loads(payload_json) if payload_json else {},
        attempts=row[8],
        max_attempts=row[9],
        created_at=row[10],
        updated_at=row[11],
        last_run_id=row[12],
        last_error=row[13],
        locked_at=row[14],
        lease_until=row[15] if len(row) > 15 else None,
        failed_at=row[16] if len(row) > 16 else None,
    )


def _age_seconds(now: datetime, iso_value: str | None) -> float | None:
    if not iso_value:
        return None
    try:
        value = datetime.fromisoformat(iso_value)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return max(0.0, (now - value).total_seconds())


def reap_expired_enrichment_jobs(cfg: Config) -> dict[str, Any]:
    apply_enrichment_schema(cfg)
    now = datetime.now(UTC).isoformat()
    con = connect(cfg.db_path)
    try:
        rows = con.execute(
            """
            SELECT job_id
            FROM enrichment_jobs
            WHERE status='running'
              AND lease_until IS NOT NULL
              AND lease_until < ?
            """,
            (now,),
        ).fetchall()
        for (job_id,) in rows:
            con.execute(
                """
                UPDATE enrichment_jobs
                SET status='pending',
                    locked_at=NULL,
                    lease_until=NULL,
                    last_error='lease expired',
                    updated_at=?
                WHERE job_id=?
                """,
                (now, job_id),
            )
        con.commit()
        return {"reaped": len(rows), "job_ids": [row[0] for row in rows]}
    finally:
        con.close()


def get_latest_enrichment(
    cfg: Config,
    enrichment_name: str,
    input_table: str,
    input_id: str,
) -> dict[str, Any] | None:
    apply_enrichment_schema(cfg)
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            """
            SELECT enrichment_name, input_table, input_id, run_id, status,
                   result_json, result_summary, confidence, error, updated_at
            FROM enrichment_latest
            WHERE enrichment_name=? AND input_table=? AND input_id=?
            """,
            (enrichment_name, input_table, input_id),
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return None
    result_json = row[5]
    return {
        "enrichment_name": row[0],
        "input_table": row[1],
        "input_id": row[2],
        "run_id": row[3],
        "status": row[4],
        "result": json.loads(result_json) if result_json else None,
        "result_summary": row[6],
        "confidence": row[7],
        "error": row[8],
        "updated_at": row[9],
    }


def get_enrichment_run(cfg: Config, run_id: str) -> dict[str, Any] | None:
    apply_enrichment_schema(cfg)
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            """
            SELECT run_id, enrichment_name, input_table, input_id, status,
                   started_at, completed_at, model, prompt_version, result_json,
                   result_summary, confidence, error
            FROM enrichment_runs
            WHERE run_id=?
            """,
            (run_id,),
        ).fetchone()
        evidence_rows = con.execute(
            """
            SELECT evidence_id, source, ref, kind, title, excerpt, created_at
            FROM enrichment_evidence
            WHERE run_id=?
            ORDER BY evidence_id ASC
            """,
            (run_id,),
        ).fetchall()
    finally:
        con.close()
    if row is None:
        return None
    result_json = row[9]
    return {
        "run_id": row[0],
        "enrichment_name": row[1],
        "input_table": row[2],
        "input_id": row[3],
        "status": row[4],
        "started_at": row[5],
        "completed_at": row[6],
        "model": row[7],
        "prompt_version": row[8],
        "result": json.loads(result_json) if result_json else None,
        "result_summary": row[10],
        "confidence": row[11],
        "error": row[12],
        "evidence": [
            {
                "evidence_id": evidence[0],
                "source": evidence[1],
                "ref": evidence[2],
                "kind": evidence[3],
                "title": evidence[4],
                "excerpt": evidence[5],
                "created_at": evidence[6],
            }
            for evidence in evidence_rows
        ],
    }
