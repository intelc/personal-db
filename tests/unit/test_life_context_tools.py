"""Exercises the life_context tracker's declared log_life_context MCP tool
(templates/trackers/life_context/tools.py) via the same loader the MCP
server uses."""

from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint
from personal_db.core.installer import install_template


def test_log_life_context_tool_delegates_to_core_helper(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_template(cfg, "life_context")
    calls = []

    def fake_log_life_context(cfg, *, start_date, end_date=None, state=None, note=None):
        calls.append((start_date, end_date, state, note))
        return {"inserted": 1, "dates": [start_date]}

    # tools.py imports the flat SDK shim (`personal_db.log_event`), which
    # already re-exported the real function at its own import time — patch
    # the shim's own attribute so the re-executed `from ... import` in
    # load_entrypoint picks it up.
    monkeypatch.setattr(
        "personal_db.log_event.log_life_context",
        fake_log_life_context,
    )

    func = load_entrypoint(dest, "tools:log_life_context", modname_prefix="test_life_context_tools")
    result = func(cfg, {"start_date": "2026-04-13", "state": "sick", "note": "flu"})

    assert result == {"inserted": 1, "dates": ["2026-04-13"]}
    assert calls == [("2026-04-13", None, "sick", "flu")]
