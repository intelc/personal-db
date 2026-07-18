from __future__ import annotations

import asyncio
import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptMessage, TextContent, Tool

from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint
from personal_db.core.mcp_registry import DeclaredMcpTool, discover_mcp_tools
from personal_db.core.runtime_env import activate_lib_dir
from personal_db.services.mcp_server import prompts as P
from personal_db.services.mcp_server import tools as T

_CORE_TOOL_NAMES = frozenset(
    {
        "list_trackers",
        "describe_tracker",
        "query",
        "get_series",
        "list_entities",
        "log_event",
        "list_notes",
        "read_note",
        "list_remote_sources",
        "email_search_receipts",
        "email_read_thread",
        "enrichment_jobs_list",
        "enrichment_job_show",
        "enrichment_job_retry",
        "enrichment_job_cancel",
        "enrichment_queue_summary",
        "read_tracker_file",
        "write_tracker_file",
        "sync",
        "sync_due",
        "backfill",
        "validate_tracker",
    }
)


def _declared_tool(spec_map: dict[str, DeclaredMcpTool], name: str) -> DeclaredMcpTool | None:
    return spec_map.get(name)


async def _dispatch_declared_tool(cfg: Config, entry: DeclaredMcpTool, arguments: dict) -> object:
    func = load_entrypoint(
        entry.base_dir,
        entry.spec.entrypoint,
        modname_prefix=f"pdb_mcp_{entry.extension_kind}_{entry.extension_name}",
    )
    return await asyncio.to_thread(func, cfg, arguments or {})


def build_server(cfg: Config) -> Server:
    server = Server("personal_db")

    def _declared_tools() -> dict[str, DeclaredMcpTool]:
        # Re-discovered per call so newly-declared tools (a freshly installed
        # tracker/app/source) show up without restarting the MCP server.
        return {entry.spec.name: entry for entry in discover_mcp_tools(cfg)}

    @server.list_tools()
    async def _list() -> list[Tool]:
        static_tools = [
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
                name="email_search_receipts",
                description=(
                    "Context-provider operation: find receipt-like email candidates "
                    "for a transaction using the configured email context provider. "
                    "Returns evidence refs such as spark_email:message:<id> plus raw "
                    "source output. Fails with a clear error if no email context "
                    "provider is configured."
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
                    "message ID, returning raw thread text and evidence refs. Fails "
                    "with a clear error if no email context provider is configured."
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
                name="enrichment_queue_summary",
                description=(
                    "Summarize enrichment queue health: counts by enrichment/status, "
                    "oldest pending age, latest run time, and recent failed jobs."
                ),
                inputSchema={"type": "object", "properties": {}},
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
        declared_tools = [
            Tool(
                name=entry.spec.name,
                description=entry.spec.description,
                inputSchema=entry.spec.input_schema or {"type": "object", "properties": {}},
            )
            for entry in discover_mcp_tools(cfg)
            if entry.spec.name not in _CORE_TOOL_NAMES
        ]
        return static_tools + declared_tools

    @server.call_tool()
    async def _call(name: str, arguments: dict) -> list[TextContent]:
        arguments = arguments or {}
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
        elif name == "enrichment_queue_summary":
            result = await asyncio.to_thread(T.enrichment_queue_summary, cfg)
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
        else:
            entry = _declared_tool(_declared_tools(), name)
            if entry is None:
                raise ValueError(f"unknown tool {name}")
            result = await _dispatch_declared_tool(cfg, entry, arguments)
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
    # Declared MCP tools (core/mcp_registry.py) are entrypoints resolved from
    # trackers/apps/sources, same as sync — they need <root>/lib on sys.path
    # for the same sealed-bundle reason (see core/runtime_env.py).
    activate_lib_dir(cfg)
    server = build_server(cfg)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
