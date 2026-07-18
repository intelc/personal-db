"""services/mcp_server/server.py::run -- must call activate_lib_dir at
startup (see core/runtime_env.py) so <root>/lib (pack python_deps) is
importable before any declared MCP tool entrypoint gets dispatched."""

from __future__ import annotations

import contextlib

import pytest

from personal_db.core.config import Config
from personal_db.services.mcp_server import server as mcp_server


class _FakeServer:
    async def run(self, read_stream, write_stream, init_options):
        return None

    def create_initialization_options(self):
        return {}


@contextlib.asynccontextmanager
async def _fake_stdio_server():
    yield (None, None)


@pytest.mark.asyncio
async def test_run_activates_lib_dir(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    activate_calls = []

    monkeypatch.setattr(mcp_server, "build_server", lambda c: _FakeServer())
    monkeypatch.setattr(mcp_server, "stdio_server", _fake_stdio_server)
    monkeypatch.setattr(mcp_server, "activate_lib_dir", lambda c: activate_calls.append(c))

    await mcp_server.run(cfg)

    assert activate_calls == [cfg]
