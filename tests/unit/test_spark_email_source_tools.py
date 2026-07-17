"""Exercises the spark_email source's declared mcp_tools entrypoints
(templates/sources/spark_email/tools.py) via the same loader the MCP server
uses, proving the moved spark_email_* tools still work end to end."""

from personal_db.core.config import Config
from personal_db.core.entrypoints import load_entrypoint
from personal_db.core.sources import install_source_template


def test_spark_email_accounts_wraps_result(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_source_template(cfg, "spark_email")

    class FakeSpark:
        @classmethod
        def from_config(cls, cfg, require_installed=False):
            assert require_installed is True
            return cls()

        def accounts(self):
            class Result:
                def as_dict(self):
                    return {"source": "spark_email", "operation": "accounts", "data": {}, "raw_text": ""}

            return Result()

    monkeypatch.setattr(
        "personal_db.remote_sources.spark.SparkEmailSource",
        FakeSpark,
    )

    func = load_entrypoint(dest, "tools:spark_email_accounts", modname_prefix="test_spark_tools")
    out = func(cfg, {})

    assert out["ok"] is True
    assert out["operation"] == "accounts"


def test_spark_email_thread_passes_arguments(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    dest = install_source_template(cfg, "spark_email")
    calls = []

    class FakeSpark:
        @classmethod
        def from_config(cls, cfg, require_installed=False):
            return cls()

        def thread(self, message_id, *, download_attachments=False):
            calls.append((message_id, download_attachments))

            class Result:
                def as_dict(self):
                    return {"source": "spark_email", "operation": "thread", "data": {}, "raw_text": ""}

            return Result()

    monkeypatch.setattr(
        "personal_db.remote_sources.spark.SparkEmailSource",
        FakeSpark,
    )

    func = load_entrypoint(dest, "tools:spark_email_thread", modname_prefix="test_spark_tools")
    out = func(cfg, {"message_id": "123", "download_attachments": True})

    assert out["ok"] is True
    assert calls == [("123", True)]
