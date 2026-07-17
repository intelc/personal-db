from __future__ import annotations

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptMessage, TextContent, Tool

from personal_db.core.config import Config
from personal_db.services.mcp_server import prompts as P
from personal_db.services.mcp_server import tools as T


def build_server(cfg: Config) -> Server:
    server = Server("personal_db")

    @server.list_tools()
    async def _list() -> list[Tool]:
        return [
            Tool(
                name="list_trackers",
                description="List installed trackers + descriptions",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="describe_tracker",
                description="Get full manifest for a tracker",
                inputSchema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            Tool(
                name="query",
                description="Run read-only SQL (SELECT/WITH only) against db.sqlite",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "sql": {"type": "string"},
                        "params": {
                            "type": "array",
                            "items": {"type": ["string", "number", "null"]},
                        },
                    },
                    "required": ["sql"],
                },
            ),
            Tool(
                name="get_series",
                description="Bucketed time-series for a tracker",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tracker": {"type": "string"},
                        "range": {
                            "type": "string",
                            "description": "YYYY-MM-DD/YYYY-MM-DD",
                        },
                        "granularity": {
                            "type": "string",
                            "enum": ["hour", "day", "week", "month"],
                        },
                        "agg": {
                            "type": "string",
                            "enum": ["sum", "avg", "count", "min", "max"],
                        },
                        "value_column": {"type": "string"},
                    },
                    "required": ["tracker", "range"],
                },
            ),
            Tool(
                name="list_entities",
                description="List people or topics with aliases",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": ["people", "topics"]},
                        "query": {"type": "string"},
                    },
                    "required": ["kind"],
                },
            ),
            Tool(
                name="log_event",
                description="Insert a row into a tracker (manual capture)",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "tracker": {"type": "string"},
                        "fields": {"type": "object"},
                    },
                    "required": ["tracker", "fields"],
                },
            ),
            Tool(
                name="list_notes",
                description="List previously written analysis notes",
                inputSchema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            ),
            Tool(
                name="read_note",
                description="Read a note by its relative path",
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            Tool(
                name="list_remote_sources",
                description=(
                    "List live/remote sources personal_db can call without materializing "
                    "their data into db.sqlite."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="spark_email_accounts",
                description="List Spark email accounts visible to Spark Desktop.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="spark_email_folders",
                description="List Spark email folders and message counts.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "scope": {
                            "type": "string",
                            "description": "Optional account/team/folder scope",
                        }
                    },
                },
            ),
            Tool(
                name="spark_email_list",
                description=(
                    "List Spark emails with optional folders and Gmail-style filters. "
                    "Returns parsed page metadata plus raw Spark output."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "folders": {"type": "array", "items": {"type": "string"}},
                        "filter": {"type": "string"},
                        "page": {"type": "integer"},
                        "page_size": {"type": "integer"},
                        "order": {"type": "string"},
                        "new_senders": {"type": "boolean"},
                    },
                },
            ),
            Tool(
                name="spark_email_search",
                description=(
                    "Search Spark emails by topic. Returns Spark's raw result text plus "
                    "any parseable message IDs/page metadata."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "about": {"type": "string"},
                        "filter": {"type": "string"},
                        "in": {
                            "type": "string",
                            "description": "Optional account/team/folder scope",
                        },
                    },
                    "required": ["about"],
                },
            ),
            Tool(
                name="spark_email_thread",
                description=(
                    "Read a full Spark email thread by Spark message ID. Returns raw "
                    "thread text for context-provider/enrichment use."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "download_attachments": {"type": "boolean"},
                    },
                    "required": ["message_id"],
                },
            ),
            Tool(
                name="email_search_receipts",
                description=(
                    "Context-provider operation: find receipt-like email candidates "
                    "for a transaction using installed email sources. Returns evidence "
                    "refs such as spark_email:message:<id> plus raw source output."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "merchant": {"type": "string"},
                        "amount": {"type": "string"},
                        "date": {
                            "type": "string",
                            "description": "Transaction date as YYYY-MM-DD",
                        },
                        "window_days": {
                            "type": "integer",
                            "description": "Days before/after date to search",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Optional email source/account/folder scope",
                        },
                    },
                },
            ),
            Tool(
                name="email_read_thread",
                description=(
                    "Context-provider operation: read an email thread by evidence "
                    "message ID, returning raw thread text and evidence refs."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {"type": "string"},
                        "download_attachments": {"type": "boolean"},
                    },
                    "required": ["message_id"],
                },
            ),
            Tool(
                name="finance_enrich_receipt_stub",
                description=(
                    "Run the stub finance receipt enrichment for one transaction. "
                    "It gathers receipt email context and records enrichment run/latest/"
                    "evidence rows, but does not call an LLM yet."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "finance_transaction_id": {"type": "string"},
                        "window_days": {
                            "type": "integer",
                            "description": "Days before/after transaction date to search",
                        },
                        "scope": {
                            "type": "string",
                            "description": "Optional email source/account/folder scope",
                        },
                    },
                    "required": ["finance_transaction_id"],
                },
            ),
            Tool(
                name="finance_enrich_receipt_v1",
                description=(
                    "Run the agent-shaped finance receipt enrichment for one transaction. "
                    "This reads bounded receipt email threads and records structured "
                    "agent output/evidence. It uses the configured harness; by default "
                    "that harness is a deterministic stub."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "finance_transaction_id": {"type": "string"},
                        "window_days": {"type": "integer"},
                        "scope": {"type": "string"},
                        "max_threads": {"type": "integer"},
                        "max_candidate_threads": {"type": "integer"},
                    },
                    "required": ["finance_transaction_id"],
                },
            ),
            Tool(
                name="finance_enqueue_receipt_jobs",
                description=(
                    "Queue receipt enrichment jobs for finance transactions missing "
                    "latest receipt-enrichment results."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                        "window_days": {"type": "integer"},
                        "scope": {"type": "string"},
                        "force": {"type": "boolean"},
                    },
                },
            ),
            Tool(
                name="finance_enqueue_receipt_v1_jobs",
                description=(
                    "Queue v1 agent-shaped receipt enrichment jobs for finance "
                    "transactions missing latest v1 results."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                        "window_days": {"type": "integer"},
                        "scope": {"type": "string"},
                        "max_threads": {"type": "integer"},
                        "max_candidate_threads": {"type": "integer"},
                        "snippet_window_chars": {"type": "integer"},
                        "only_ready": {"type": "boolean"},
                        "force": {"type": "boolean"},
                    },
                },
            ),
            Tool(
                name="finance_run_due_receipt_jobs",
                description=(
                    "Run due queued finance receipt enrichment jobs synchronously. "
                    "This gathers context and records runs/evidence, but still does "
                    "not call an LLM."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="finance_run_due_receipt_v1_jobs",
                description=(
                    "Run due queued v1 finance receipt enrichment jobs synchronously. "
                    "By default this uses the deterministic stub harness, not a real LLM."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="enrichment_jobs_list",
                description="List enrichment queue jobs with optional status/input filters.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "status": {"type": "string"},
                        "enrichment_name": {"type": "string"},
                        "input_table": {"type": "string"},
                        "input_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="enrichment_job_show",
                description=(
                    "Show one enrichment job, including last run/evidence and latest "
                    "result when available."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"job_id": {"type": "string"}},
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="enrichment_job_retry",
                description="Return an enrichment job to pending so a worker can retry it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "reset_attempts": {"type": "boolean"},
                    },
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="enrichment_job_cancel",
                description="Mark an enrichment job canceled so workers skip it.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "job_id": {"type": "string"},
                        "reason": {"type": "string"},
                    },
                    "required": ["job_id"],
                },
            ),
            Tool(
                name="finance_receipt_latest",
                description="Get the latest receipt enrichment result for one finance transaction.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "finance_transaction_id": {"type": "string"},
                        "v1": {"type": "boolean"},
                    },
                    "required": ["finance_transaction_id"],
                },
            ),
            Tool(
                name="enrichment_queue_summary",
                description=(
                    "Summarize enrichment queue health: counts by enrichment/status, "
                    "oldest pending age, latest run time, and recent failed jobs."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="worker_status",
                description="Return structured status for the enrichment worker LaunchAgent.",
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="worker_log_tail",
                description="Return the tail of the enrichment worker log.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "lines": {"type": "integer"},
                    },
                },
            ),
            Tool(
                name="read_tracker_file",
                description=(
                    "Read a text file under <root>/trackers/. Path is relative to the "
                    "trackers directory (e.g. 'project_time/manifest.yaml'). Use this to "
                    "inspect existing tracker files before editing them."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
            Tool(
                name="write_tracker_file",
                description=(
                    "Create or overwrite a text file under <root>/trackers/. Path is "
                    "relative to the trackers dir. Use this to author manifest.yaml, "
                    "ingest.py, schema.sql, and config yamls when scaffolding or "
                    "editing a tracker. Always call validate_tracker after writing."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            Tool(
                name="log_life_context",
                description=(
                    "Log a life_context diary entry — a structured 'state' tag "
                    "(sick, traveling, well, etc.) and/or free-text note for one "
                    "day or a range. Range entries fan out to one row per day. "
                    "At least one of state or note is required. Use this to "
                    "annotate days that need explanation when other trackers "
                    "look weird (sick stretch, vacation, system reinstall)."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                        "end_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD; omit for single-day entry",
                        },
                        "state": {
                            "type": "string",
                            "description": "categorical tag (sick/traveling/well/etc.)",
                        },
                        "note": {"type": "string", "description": "free-text annotation"},
                    },
                    "required": ["start_date"],
                },
            ),
            Tool(
                name="sync",
                description=(
                    "Run an incremental sync for one tracker (equivalent to "
                    "`personal-db sync <name>`). Pulls new rows from the source "
                    "since the stored cursor and runs registered transforms. "
                    "May take seconds to minutes depending on the tracker."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
            Tool(
                name="sync_due",
                description=(
                    "Run sync for every tracker whose schedule.every interval "
                    "has elapsed since its last run (equivalent to "
                    "`personal-db sync --due`). Returns a per-tracker status "
                    "map: 'ok', 'skip', or 'error: <msg>'."
                ),
                inputSchema={"type": "object", "properties": {}},
            ),
            Tool(
                name="backfill",
                description=(
                    "Run a historical backfill for one tracker over an optional "
                    "date range (equivalent to `personal-db backfill <name> "
                    "[--from] [--to]`). Use this after fixing an ingest parser "
                    "or when first installing a tracker. Dates are YYYY-MM-DD."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "from": {"type": "string", "description": "YYYY-MM-DD"},
                        "to": {"type": "string", "description": "YYYY-MM-DD"},
                    },
                    "required": ["name"],
                },
            ),
            Tool(
                name="validate_tracker",
                description=(
                    "Run lint checks on a tracker dir: YAML parse, Pydantic manifest "
                    "schema, py_compile on ingest.py, schema.sql executes against an "
                    "in-memory sqlite. Returns per-check pass/fail. Call after every "
                    "round of write_tracker_file before declaring done."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
            ),
        ]

    @server.call_tool()
    async def _call(name: str, arguments: dict) -> list[TextContent]:
        if name == "list_trackers":
            result = T.list_trackers(cfg)
        elif name == "describe_tracker":
            result = T.describe_tracker(cfg, arguments["name"])
        elif name == "query":
            result = T.query(cfg, arguments["sql"], arguments.get("params"))
        elif name == "get_series":
            result = T.get_series(
                cfg,
                tracker=arguments["tracker"],
                range_=arguments["range"],
                granularity=arguments.get("granularity", "day"),
                agg=arguments.get("agg", "sum"),
                value_column=arguments.get("value_column"),
            )
        elif name == "list_entities":
            result = T.list_entities(cfg, arguments["kind"], arguments.get("query"))
        elif name == "log_event":
            result = {"rowid": T.log_event_tool(cfg, arguments["tracker"], arguments["fields"])}
        elif name == "list_notes":
            result = T.list_notes_tool(cfg, arguments.get("query"))
        elif name == "read_note":
            result = T.read_note_tool(cfg, arguments["path"])
        elif name == "list_remote_sources":
            result = T.list_remote_sources(cfg)
        elif name == "spark_email_accounts":
            result = await asyncio.to_thread(T.spark_email_accounts, cfg)
        elif name == "spark_email_folders":
            result = await asyncio.to_thread(T.spark_email_folders, cfg, arguments.get("scope"))
        elif name == "spark_email_list":
            result = await asyncio.to_thread(
                T.spark_email_list,
                cfg,
                arguments.get("folders"),
                arguments.get("filter"),
                arguments.get("page", 1),
                arguments.get("page_size", 50),
                arguments.get("order"),
                arguments.get("new_senders", False),
            )
        elif name == "spark_email_search":
            result = await asyncio.to_thread(
                T.spark_email_search,
                cfg,
                arguments["about"],
                arguments.get("filter"),
                arguments.get("in"),
            )
        elif name == "spark_email_thread":
            result = await asyncio.to_thread(
                T.spark_email_thread,
                cfg,
                arguments["message_id"],
                arguments.get("download_attachments", False),
            )
        elif name == "email_search_receipts":
            result = await asyncio.to_thread(
                T.email_search_receipts,
                cfg,
                arguments.get("merchant"),
                arguments.get("amount"),
                arguments.get("date"),
                arguments.get("window_days", 7),
                arguments.get("scope"),
            )
        elif name == "email_read_thread":
            result = await asyncio.to_thread(
                T.email_read_thread,
                cfg,
                arguments["message_id"],
                arguments.get("download_attachments", False),
            )
        elif name == "finance_enrich_receipt_stub":
            result = await asyncio.to_thread(
                T.finance_enrich_receipt_stub,
                cfg,
                arguments["finance_transaction_id"],
                arguments.get("window_days", 7),
                arguments.get("scope"),
            )
        elif name == "finance_enrich_receipt_v1":
            result = await asyncio.to_thread(
                T.finance_enrich_receipt_v1,
                cfg,
                arguments["finance_transaction_id"],
                arguments.get("window_days", 7),
                arguments.get("scope"),
                arguments.get("max_threads", 3),
                arguments.get("max_candidate_threads", 20),
            )
        elif name == "finance_enqueue_receipt_jobs":
            result = await asyncio.to_thread(
                T.finance_enqueue_receipt_jobs,
                cfg,
                arguments.get("limit", 50),
                arguments.get("window_days", 7),
                arguments.get("scope"),
                arguments.get("force", False),
            )
        elif name == "finance_enqueue_receipt_v1_jobs":
            result = await asyncio.to_thread(
                T.finance_enqueue_receipt_v1_jobs,
                cfg,
                arguments.get("limit", 50),
                arguments.get("window_days", 7),
                arguments.get("scope"),
                arguments.get("max_threads", 3),
                arguments.get("max_candidate_threads", 20),
                arguments.get("force", False),
                arguments.get("start_date"),
                arguments.get("end_date"),
                arguments.get("snippet_window_chars", 300),
                arguments.get("only_ready", False),
            )
        elif name == "finance_run_due_receipt_jobs":
            result = await asyncio.to_thread(
                T.finance_run_due_receipt_jobs,
                cfg,
                arguments.get("limit", 5),
            )
        elif name == "finance_run_due_receipt_v1_jobs":
            result = await asyncio.to_thread(
                T.finance_run_due_receipt_v1_jobs,
                cfg,
                arguments.get("limit", 5),
            )
        elif name == "enrichment_jobs_list":
            result = await asyncio.to_thread(
                T.enrichment_jobs_list,
                cfg,
                arguments.get("status"),
                arguments.get("enrichment_name"),
                arguments.get("input_table"),
                arguments.get("input_id"),
                arguments.get("limit", 50),
            )
        elif name == "enrichment_job_show":
            result = await asyncio.to_thread(
                T.enrichment_job_show,
                cfg,
                arguments["job_id"],
            )
        elif name == "enrichment_job_retry":
            result = await asyncio.to_thread(
                T.enrichment_job_retry,
                cfg,
                arguments["job_id"],
                arguments.get("reset_attempts", True),
            )
        elif name == "enrichment_job_cancel":
            result = await asyncio.to_thread(
                T.enrichment_job_cancel,
                cfg,
                arguments["job_id"],
                arguments.get("reason"),
            )
        elif name == "finance_receipt_latest":
            result = await asyncio.to_thread(
                T.finance_receipt_latest,
                cfg,
                arguments["finance_transaction_id"],
                arguments.get("v1", False),
            )
        elif name == "enrichment_queue_summary":
            result = await asyncio.to_thread(T.enrichment_queue_summary, cfg)
        elif name == "worker_status":
            result = await asyncio.to_thread(T.worker_status, cfg)
        elif name == "worker_log_tail":
            result = await asyncio.to_thread(
                T.worker_log_tail,
                cfg,
                arguments.get("lines", 50),
            )
        elif name == "read_tracker_file":
            result = T.read_tracker_file(cfg, arguments["path"])
        elif name == "write_tracker_file":
            result = T.write_tracker_file(cfg, arguments["path"], arguments["content"])
        elif name == "validate_tracker":
            result = T.validate_tracker(cfg, arguments["name"])
        elif name == "sync":
            result = await asyncio.to_thread(T.sync_tool, cfg, arguments["name"])
        elif name == "sync_due":
            result = await asyncio.to_thread(T.sync_due_tool, cfg)
        elif name == "backfill":
            result = await asyncio.to_thread(
                T.backfill_tool,
                cfg,
                arguments["name"],
                arguments.get("from"),
                arguments.get("to"),
            )
        elif name == "log_life_context":
            result = T.log_life_context(
                cfg,
                start_date=arguments["start_date"],
                end_date=arguments.get("end_date"),
                state=arguments.get("state"),
                note=arguments.get("note"),
            )
        else:
            raise ValueError(f"unknown tool {name}")
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    @server.list_prompts()
    async def _list_prompts() -> list[Prompt]:
        return [
            Prompt(
                name=P.CREATE_TRACKER,
                description=(
                    "Walk through designing a new derived tracker — Q&A flow that "
                    "verifies SQL on real data and writes manifest+ingest+schema files."
                ),
                arguments=[],
            ),
        ]

    @server.get_prompt()
    async def _get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if name == P.CREATE_TRACKER:
            text = P.build_create_tracker_prompt(cfg)
            return GetPromptResult(
                description="Design a derived tracker for personal_db",
                messages=[
                    PromptMessage(
                        role="user",
                        content=TextContent(type="text", text=text),
                    )
                ],
            )
        raise ValueError(f"unknown prompt: {name}")

    return server


async def run(cfg: Config) -> None:
    server = build_server(cfg)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
