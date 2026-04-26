from __future__ import annotations

import json

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from personal_db.config import Config
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
        else:
            raise ValueError(f"unknown tool {name}")
        return [TextContent(type="text", text=json.dumps(result, default=str))]

    return server


async def run(cfg: Config) -> None:
    server = build_server(cfg)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
