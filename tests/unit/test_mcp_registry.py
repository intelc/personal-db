import yaml

from personal_db.core.apps import install_app_template
from personal_db.core.config import Config
from personal_db.core.mcp_registry import discover_mcp_tools
from personal_db.core.sources import install_source_template


def _install_tracker_with_tool(tmp_root, name="fixture_tracker"):
    d = tmp_root / "trackers" / name
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": name,
                "description": "x",
                "permission_type": "none",
                "setup_steps": [],
                "time_column": "ts",
                "granularity": "event",
                "schema": {"tables": {name: {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}},
                "mcp_tools": [
                    {
                        "name": "fixture_tool",
                        "description": "does a thing",
                        "entrypoint": "tools:fixture_tool",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            }
        )
    )
    (d / "tools.py").write_text("def fixture_tool(cfg, arguments):\n    return {'ok': True}\n")
    return d


def test_discover_mcp_tools_finds_tracker_declared_tool(tmp_root):
    cfg = Config(root=tmp_root)
    tracker_dir = _install_tracker_with_tool(tmp_root)

    tools = discover_mcp_tools(cfg)

    assert len(tools) == 1
    entry = tools[0]
    assert entry.extension_kind == "tracker"
    assert entry.extension_name == "fixture_tracker"
    assert entry.base_dir == tracker_dir
    assert entry.spec.name == "fixture_tool"
    assert entry.spec.entrypoint == "tools:fixture_tool"


def test_discover_mcp_tools_finds_app_declared_tools(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_app_template(cfg, "finance")

    tools = discover_mcp_tools(cfg)

    names = {(t.extension_kind, t.extension_name, t.spec.name) for t in tools}
    assert ("app", "finance", "finance_enrich_receipt_stub") in names
    assert ("app", "finance", "finance_receipt_latest") in names
    assert all(t.base_dir == dest for t in tools if t.extension_name == "finance")


def test_discover_mcp_tools_finds_source_declared_tools(tmp_root):
    cfg = Config(root=tmp_root)
    dest = install_source_template(cfg, "spark_email")

    tools = discover_mcp_tools(cfg)

    names = {(t.extension_kind, t.extension_name, t.spec.name) for t in tools}
    assert ("source", "spark_email", "spark_email_accounts") in names
    assert ("source", "spark_email", "spark_email_thread") in names
    assert all(t.base_dir == dest for t in tools if t.extension_name == "spark_email")


def test_discover_mcp_tools_empty_when_nothing_installed(tmp_root):
    cfg = Config(root=tmp_root)
    assert discover_mcp_tools(cfg) == []
