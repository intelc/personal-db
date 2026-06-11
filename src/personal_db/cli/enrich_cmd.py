from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated

import typer

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.enrichments.core import (
    cancel_enrichment_job,
    enrichment_queue_summary,
    get_enrichment_job_detail,
    get_latest_enrichment,
    list_enrichment_jobs,
    retry_enrichment_job,
)
from personal_db.enrichments.finance import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    RECEIPT_ENRICHMENT,
    RECEIPT_V1_ENRICHMENT,
    debug_receipt_batch_v1,
    debug_transaction_receipt_v1,
    enqueue_missing_receipt_enrichments,
    enqueue_missing_receipt_v1_enrichments,
    enrich_transaction_receipt_stub,
    enrich_transaction_receipt_v1,
    run_due_finance_receipt_jobs,
    run_due_finance_receipt_v1_jobs,
)

app = typer.Typer(no_args_is_help=True, help="Run enrichment jobs")
finance_app = typer.Typer(no_args_is_help=True, help="Finance enrichments")
jobs_app = typer.Typer(no_args_is_help=True, help="Inspect and control enrichment jobs")


def _emit(result: dict) -> None:
    json.dump(result, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


@contextmanager
def _temporary_receipt_harness(backend: str | None) -> Iterator[None]:
    if not backend:
        yield
        return
    old = os.environ.get("PERSONAL_DB_RECEIPT_HARNESS")
    os.environ["PERSONAL_DB_RECEIPT_HARNESS"] = backend
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("PERSONAL_DB_RECEIPT_HARNESS", None)
        else:
            os.environ["PERSONAL_DB_RECEIPT_HARNESS"] = old


@finance_app.command("receipt")
def finance_receipt(
    finance_transaction_id: str = typer.Argument(..., help="finance_transactions id"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
) -> None:
    """Find receipt context for one finance transaction and persist a stub result."""
    cfg = Config(root=get_root())
    try:
        result = enrich_transaction_receipt_stub(
            cfg,
            finance_transaction_id,
            window_days=window_days,
            scope=scope,
        )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("receipt-v1")
def finance_receipt_v1(
    finance_transaction_id: str = typer.Argument(..., help="finance_transactions id"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
    max_threads: int = typer.Option(3, "--max-threads", help="Maximum email threads to send as fuller context"),
    max_candidate_threads: int = typer.Option(
        DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
        "--max-candidate-threads",
        help="Maximum candidate email threads to read for deterministic evidence snippets",
    ),
    harness: str | None = typer.Option(
        None,
        "--harness",
        help="Override harness backend for this run: stub or openai",
    ),
) -> None:
    """Run the agent-shaped receipt enrichment for one finance transaction."""
    cfg = Config(root=get_root())
    try:
        with _temporary_receipt_harness(harness):
            result = enrich_transaction_receipt_v1(
                cfg,
                finance_transaction_id,
                window_days=window_days,
                scope=scope,
                max_threads=max_threads,
                max_candidate_threads=max_candidate_threads,
            )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("receipt-debug")
def finance_receipt_debug(
    finance_transaction_id: str = typer.Argument(..., help="finance_transactions id"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
    max_threads: int = typer.Option(3, "--max-threads", help="Maximum email threads to send as fuller context"),
    max_candidate_threads: int = typer.Option(
        DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
        "--max-candidate-threads",
        help="Maximum candidate email threads to read for deterministic evidence snippets",
    ),
    snippet_window_chars: int = typer.Option(
        300,
        "--snippet-window-chars",
        help="Characters around deterministic matches to include in snippets",
    ),
    run_agent: bool = typer.Option(False, "--run-agent", help="Also run the configured receipt agent"),
    harness: str | None = typer.Option(
        None,
        "--harness",
        help="Override harness backend for this debug run: stub or openai",
    ),
) -> None:
    """Inspect receipt search candidates, snippets, and optional agent output without persisting."""
    cfg = Config(root=get_root())
    try:
        with _temporary_receipt_harness(harness):
            result = debug_transaction_receipt_v1(
                cfg,
                finance_transaction_id,
                window_days=window_days,
                scope=scope,
                max_threads=max_threads,
                max_candidate_threads=max_candidate_threads,
                snippet_window_chars=snippet_window_chars,
                run_agent=run_agent,
            )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("receipt-debug-batch")
def finance_receipt_debug_batch(
    limit: int = typer.Option(20, "--limit", help="Maximum recent positive transactions to inspect"),
    start_date: str | None = typer.Option(None, "--start-date", help="Only include transactions on/after YYYY-MM-DD"),
    end_date: str | None = typer.Option(None, "--end-date", help="Only include transactions before YYYY-MM-DD"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
    max_threads: int = typer.Option(2, "--max-threads", help="Maximum email threads to send as fuller context"),
    max_candidate_threads: int = typer.Option(
        DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
        "--max-candidate-threads",
        help="Maximum candidate email threads to read for deterministic evidence snippets",
    ),
    snippet_window_chars: int = typer.Option(
        300,
        "--snippet-window-chars",
        help="Characters around deterministic matches to include in snippets",
    ),
    run_agent: bool = typer.Option(False, "--run-agent", help="Also run the configured receipt agent"),
    include_details: bool = typer.Option(False, "--include-details", help="Include full per-transaction debug payloads"),
    harness: str | None = typer.Option(
        None,
        "--harness",
        help="Override harness backend for this debug run: stub or openai",
    ),
) -> None:
    """Run a non-persisting receipt calibration batch over recent transactions."""
    cfg = Config(root=get_root())
    try:
        with _temporary_receipt_harness(harness):
            result = debug_receipt_batch_v1(
                cfg,
                limit=limit,
                start_date=start_date,
                end_date=end_date,
                window_days=window_days,
                scope=scope,
                max_threads=max_threads,
                max_candidate_threads=max_candidate_threads,
                snippet_window_chars=snippet_window_chars,
                run_agent=run_agent,
                include_details=include_details,
            )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("enqueue-receipts")
def finance_enqueue_receipts(
    limit: int = typer.Option(50, "--limit", help="Maximum transactions to enqueue"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
    force: bool = typer.Option(False, "--force", help="Requeue even if latest enrichment exists"),
) -> None:
    """Queue receipt enrichment jobs for finance transactions missing results."""
    cfg = Config(root=get_root())
    try:
        result = enqueue_missing_receipt_enrichments(
            cfg,
            limit=limit,
            window_days=window_days,
            scope=scope,
            force=force,
        )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("enqueue-receipts-v1")
def finance_enqueue_receipts_v1(
    limit: int = typer.Option(50, "--limit", help="Maximum transactions to enqueue"),
    start_date: str | None = typer.Option(None, "--start-date", help="Only include transactions on/after YYYY-MM-DD"),
    end_date: str | None = typer.Option(None, "--end-date", help="Only include transactions before YYYY-MM-DD"),
    window_days: int = typer.Option(7, "--window-days", help="Days around transaction date"),
    scope: str | None = typer.Option(None, "--in", help="Optional email source/account/folder scope"),
    max_threads: int = typer.Option(3, "--max-threads", help="Maximum email threads to send as fuller context"),
    max_candidate_threads: int = typer.Option(
        DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
        "--max-candidate-threads",
        help="Maximum candidate email threads to read for deterministic evidence snippets",
    ),
    snippet_window_chars: int = typer.Option(
        300,
        "--snippet-window-chars",
        help="Characters around deterministic matches to include in readiness snippets",
    ),
    only_ready: bool = typer.Option(
        False,
        "--only-ready",
        help="Screen candidates first and queue only transactions with strong receipt evidence",
    ),
    rerun_status: Annotated[
        list[str] | None,
        typer.Option(
            "--rerun-status",
            help="Also queue transactions whose latest v1 receipt result has this status.",
        ),
    ] = None,
    force: bool = typer.Option(False, "--force", help="Requeue even if latest enrichment exists"),
) -> None:
    """Queue v1 receipt enrichment jobs for finance transactions missing results."""
    cfg = Config(root=get_root())
    try:
        result = enqueue_missing_receipt_v1_enrichments(
            cfg,
            limit=limit,
            start_date=start_date,
            end_date=end_date,
            window_days=window_days,
            scope=scope,
            max_threads=max_threads,
            max_candidate_threads=max_candidate_threads,
            snippet_window_chars=snippet_window_chars,
            only_ready=only_ready,
            rerun_statuses=rerun_status,
            force=force,
        )
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("latest")
def finance_latest(
    finance_transaction_id: str = typer.Argument(..., help="finance_transactions id"),
    v1: bool = typer.Option(False, "--v1", help="Show v1 receipt enrichment latest result"),
) -> None:
    """Show the latest receipt enrichment result for one finance transaction."""
    cfg = Config(root=get_root())
    result = get_latest_enrichment(
        cfg,
        RECEIPT_V1_ENRICHMENT if v1 else RECEIPT_ENRICHMENT,
        "finance_transactions",
        finance_transaction_id,
    )
    _emit({"latest": result})


@finance_app.command("run-due")
def finance_run_due(
    limit: int = typer.Option(5, "--limit", help="Maximum due jobs to run"),
) -> None:
    """Run due finance receipt enrichment jobs synchronously."""
    cfg = Config(root=get_root())
    try:
        result = run_due_finance_receipt_jobs(cfg, limit=limit)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@finance_app.command("run-due-v1")
def finance_run_due_v1(
    limit: int = typer.Option(5, "--limit", help="Maximum due jobs to run"),
    harness: str | None = typer.Option(
        None,
        "--harness",
        help="Override harness backend for this run: stub or openai",
    ),
) -> None:
    """Run due v1 finance receipt enrichment jobs synchronously."""
    cfg = Config(root=get_root())
    try:
        with _temporary_receipt_harness(harness):
            result = run_due_finance_receipt_v1_jobs(cfg, limit=limit)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@jobs_app.command("list")
def jobs_list(
    status: str | None = typer.Option(None, "--status", help="Filter by job status"),
    enrichment_name: str | None = typer.Option(None, "--enrichment", help="Filter by enrichment name"),
    input_table: str | None = typer.Option(None, "--input-table", help="Filter by input table"),
    input_id: str | None = typer.Option(None, "--input-id", help="Filter by input id"),
    limit: int = typer.Option(50, "--limit", help="Maximum jobs to return"),
) -> None:
    """List enrichment queue jobs."""
    cfg = Config(root=get_root())
    _emit(
        {
            "jobs": list_enrichment_jobs(
                cfg,
                status=status,
                enrichment_name=enrichment_name,
                input_table=input_table,
                input_id=input_id,
                limit=limit,
            )
        }
    )


@jobs_app.command("show")
def jobs_show(job_id: str = typer.Argument(..., help="enrichment_jobs.job_id")) -> None:
    """Show one enrichment job with last run/latest context."""
    cfg = Config(root=get_root())
    try:
        result = get_enrichment_job_detail(cfg, job_id)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit(result)


@jobs_app.command("retry")
def jobs_retry(
    job_id: str = typer.Argument(..., help="enrichment_jobs.job_id"),
    keep_attempts: bool = typer.Option(
        False,
        "--keep-attempts",
        help="Do not reset attempts when requeueing",
    ),
) -> None:
    """Return a job to pending so a worker can try it again."""
    cfg = Config(root=get_root())
    try:
        result = retry_enrichment_job(cfg, job_id, reset_attempts=not keep_attempts)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit({"job": result})


@jobs_app.command("cancel")
def jobs_cancel(
    job_id: str = typer.Argument(..., help="enrichment_jobs.job_id"),
    reason: str | None = typer.Option(None, "--reason", help="Optional cancellation reason"),
) -> None:
    """Mark a job canceled so workers skip it."""
    cfg = Config(root=get_root())
    try:
        result = cancel_enrichment_job(cfg, job_id, reason=reason)
    except Exception as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2) from e
    _emit({"job": result})


@app.command("queue-summary")
def queue_summary() -> None:
    """Show enrichment queue health counts and recent failures."""
    cfg = Config(root=get_root())
    _emit(enrichment_queue_summary(cfg))


app.add_typer(jobs_app, name="jobs")
app.add_typer(finance_app, name="finance")
