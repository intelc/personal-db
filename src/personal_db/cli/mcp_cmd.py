import asyncio

from personal_db.cli.state import get_root
from personal_db.config import Config
from personal_db.mcp_server.server import run as run_server


def mcp() -> None:
    """Run the MCP stdio server (called by Claude Code)."""
    cfg = Config(root=get_root())
    asyncio.run(run_server(cfg))
