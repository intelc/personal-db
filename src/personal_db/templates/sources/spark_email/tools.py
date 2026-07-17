"""Declared MCP tool implementations for the spark_email source.

Registered via source.yaml `mcp_tools` and dispatched by the MCP server's
extension-tool registry. Each function's signature is (cfg, arguments) ->
JSON-serializable, per the declared-tool entrypoint contract (see
core/manifest.py's McpToolSpec docstring).

These wrap raw Spark Desktop CLI operations (accounts/folders/list/search/
thread) directly — unlike the email_search_receipts/email_read_thread core
MCP tools, which go through the EmailContextProvider protocol, these
operations have no protocol-level abstraction and are Spark-specific by
design, so they live with the concrete spark_email source rather than in the
core MCP surface.
"""

from __future__ import annotations

from typing import Any

from personal_db.config import Config
from personal_db.remote_sources.spark import (
    SparkCommandError,
    SparkEmailSource,
    SparkSourceConfigError,
)


def _spark_call(fn) -> dict[str, Any]:
    try:
        return {"ok": True, **fn().as_dict()}
    except SparkCommandError as e:
        return {
            "ok": False,
            "source": "spark_email",
            "error": str(e),
            "returncode": e.returncode,
        }
    except SparkSourceConfigError as e:
        return {"ok": False, "source": "spark_email", "error": str(e)}
    except Exception as e:
        return {"ok": False, "source": "spark_email", "error": str(e)}


def spark_email_accounts(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    return _spark_call(lambda: SparkEmailSource.from_config(cfg, require_installed=True).accounts())


def spark_email_folders(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    scope = arguments.get("scope")
    return _spark_call(lambda: SparkEmailSource.from_config(cfg, require_installed=True).folders(scope))


def spark_email_list(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    return _spark_call(
        lambda: SparkEmailSource.from_config(cfg, require_installed=True).emails(
            folders=arguments.get("folders"),
            filter_=arguments.get("filter"),
            page=arguments.get("page", 1),
            page_size=arguments.get("page_size", 50),
            order=arguments.get("order"),
            new_senders=arguments.get("new_senders", False),
        )
    )


def spark_email_search(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    return _spark_call(
        lambda: SparkEmailSource.from_config(cfg, require_installed=True).search(
            arguments["about"],
            filter_=arguments.get("filter"),
            in_=arguments.get("in"),
        )
    )


def spark_email_thread(cfg: Config, arguments: dict[str, Any]) -> dict[str, Any]:
    return _spark_call(
        lambda: SparkEmailSource.from_config(cfg, require_installed=True).thread(
            arguments["message_id"],
            download_attachments=arguments.get("download_attachments", False),
        )
    )
