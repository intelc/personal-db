from __future__ import annotations

import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import GetPromptResult, Prompt, PromptMessage, TextContent, Tool

from personal_db.config import Config
from personal_db.mcp_server import prompts as P
from personal_db.mcp_server import tools as T


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
        elif name == "read_tracker_file":
            result = T.read_tracker_file(cfg, arguments["path"])
        elif name == "write_tracker_file":
            result = T.write_tracker_file(cfg, arguments["path"], arguments["content"])
        elif name == "validate_tracker":
            result = T.validate_tracker(cfg, arguments["name"])
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
