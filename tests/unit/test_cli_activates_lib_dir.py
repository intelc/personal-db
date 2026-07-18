"""Every long-lived entrypoint (cli/main.py root callback, services/daemon/
server.py, services/mcp_server/server.py) calls core.runtime_env.activate_lib_dir
at startup so <root>/lib is importable -- see core/runtime_env.py for why."""

from __future__ import annotations

import sys

from typer.testing import CliRunner

from personal_db.cli.main import app

runner = CliRunner()


def test_cli_root_callback_activates_lib_dir(tmp_path):
    root = tmp_path / "personal_db"
    lib_dir = root / "lib"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pdb_cli_activation_probe.py").write_text("VALUE = 1\n")

    try:
        result = runner.invoke(app, ["--root", str(root), "status"])
        assert result.exit_code == 0, result.output
        assert str(lib_dir) in sys.path
    finally:
        sys.modules.pop("pdb_cli_activation_probe", None)
        if str(lib_dir) in sys.path:
            sys.path.remove(str(lib_dir))
