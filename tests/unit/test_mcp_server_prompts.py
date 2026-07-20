"""Registration tests for the MCP server's prompts/list and prompts/get
handlers -- specifically that create_connector is wired up next to
create_tracker (services/mcp_server/server.py)."""

from __future__ import annotations

import pytest
from mcp.types import GetPromptRequest, ListPromptsRequest

from personal_db.core.config import Config
from personal_db.services.mcp_server import prompts as P
from personal_db.services.mcp_server.server import build_server


@pytest.mark.asyncio
async def test_list_prompts_includes_create_connector(tmp_root):
    cfg = Config(root=tmp_root)
    server = build_server(cfg)
    handler = server.request_handlers[ListPromptsRequest]
    result = await handler(ListPromptsRequest(method="prompts/list"))
    names = [p.name for p in result.root.prompts]
    assert P.CREATE_TRACKER in names
    assert P.CREATE_CONNECTOR in names


@pytest.mark.asyncio
async def test_get_prompt_create_connector_renders_with_slug(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    server = build_server(cfg)
    handler = server.request_handlers[GetPromptRequest]
    result = await handler(
        GetPromptRequest(
            method="prompts/get",
            params={"name": P.CREATE_CONNECTOR, "arguments": {"slug": "my_source"}},
        )
    )
    text = result.root.messages[0].content.text
    assert "my_source" in text
    assert "{{slug}}" not in text


@pytest.mark.asyncio
async def test_get_prompt_create_connector_renders_without_arguments(tmp_root):
    cfg = Config(root=tmp_root)
    cfg.trackers_dir.mkdir(parents=True, exist_ok=True)
    server = build_server(cfg)
    handler = server.request_handlers[GetPromptRequest]
    result = await handler(
        GetPromptRequest(method="prompts/get", params={"name": P.CREATE_CONNECTOR})
    )
    text = result.root.messages[0].content.text
    assert "not yet chosen" in text
