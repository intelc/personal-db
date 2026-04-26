import json
import subprocess
import sys

import yaml


def test_mcp_server_handles_list_trackers(tmp_path):
    root = tmp_path / "personal_db"
    subprocess.run(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "init"],
        check=True,
        capture_output=True,
    )
    # Make a demo tracker so list_trackers has something to return
    d = root / "trackers" / "demo"
    d.mkdir(parents=True)
    (d / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "description": "x",
                "permission_type": "none",
                "setup_steps": [],
                "schedule": {"every": "1h"},
                "time_column": "ts",
                "granularity": "event",
                "schema": {
                    "tables": {"demo": {"columns": {"ts": {"type": "TEXT", "semantic": "ts"}}}}
                },
            }
        )
    )
    proc = subprocess.Popen(
        [sys.executable, "-m", "personal_db.cli.main", "--root", str(root), "mcp"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # MCP initialize handshake
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
        proc.stdin.write((json.dumps(init_req) + "\n").encode())
        proc.stdin.flush()
        # Read the init response (one JSON-RPC line)
        line = proc.stdout.readline()
        assert line, "no init response"
        resp = json.loads(line)
        assert resp["id"] == 1
        # Notification: initialized
        notif = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
        proc.stdin.write((json.dumps(notif) + "\n").encode())
        proc.stdin.flush()
        # tools/call list_trackers
        call_req = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "list_trackers", "arguments": {}},
        }
        proc.stdin.write((json.dumps(call_req) + "\n").encode())
        proc.stdin.flush()
        line = proc.stdout.readline()
        resp = json.loads(line)
        # Tool result content is a list of TextContent items per MCP spec
        text = resp["result"]["content"][0]["text"]
        data = json.loads(text)
        assert any(t["name"] == "demo" for t in data)
    finally:
        proc.terminate()
        proc.wait(timeout=3)
