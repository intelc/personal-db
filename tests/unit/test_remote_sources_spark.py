import subprocess

import pytest

from personal_db.core.config import Config
from personal_db.remote_sources.spark import SparkCommandError, SparkEmailSource
from personal_db.core.sources import install_source_template

FOLDERS_OUTPUT = """
Unified
  Inbox       33920  messages  (Inbox)
  Archive     62061  messages  (Archive)
  --------------------------------------
  Total       101710 messages

Email Account: user@example.com (Gmail labels)
  Inbox       10 messages  (user@example.com:Inbox)
  Important   2  messages  (user@example.com:Important)
  --------------------------------------
  Total       12 messages
"""

EMAILS_OUTPUT = """
Emails in Unified Inbox

  ID     Account           From              Date              Subject              Flags
  87596  user@example.com  Alice <a@ex.com>  2026-06-01 20:42  Receipt              unread
  87597  user@example.com  Bob <b@ex.com>    2026-06-01 20:43  Re: Thing

Page 1 of 15 (719 total emails)
"""

SEARCH_OUTPUT = """
Search results for "receipt"
11 result(s), sorted by relevance

  ID: 87510
  Subject: Your ride
  From: Lyft Receipts <no-reply@lyftmail.com>

  100 California Dr
  Visa *1234 $50.30

  ID: 87511
  Subject: Your order
"""


def _proc(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess(["spark"], returncode, stdout, stderr)


def test_folders_parses_groups_and_counts():
    source = SparkEmailSource(runner=lambda args: _proc(FOLDERS_OUTPUT))

    result = source.folders()

    groups = result.data["groups"]
    assert groups[0]["kind"] == "unified"
    assert groups[0]["total"] == 101710
    assert groups[0]["folders"][0] == {
        "name": "Inbox",
        "count": 33920,
        "identifier": "Inbox",
    }
    assert groups[1]["kind"] == "account"
    assert groups[1]["name"] == "user@example.com"
    assert groups[1]["gmail_labels"] is True


def test_emails_builds_spark_args_and_parses_page_metadata():
    calls = []

    def runner(args):
        calls.append(args)
        return _proc(EMAILS_OUTPUT)

    source = SparkEmailSource(runner=runner)

    result = source.emails(
        folders=["Archive"],
        filter_="from:alice@example.com",
        page=2,
        page_size=25,
        order="ascending",
        new_senders=True,
    )

    assert calls == [
        [
            "spark",
            "emails",
            "--filter",
            "from:alice@example.com",
            "--page",
            "2",
            "--page-size",
            "25",
            "--order",
            "ascending",
            "--new-senders",
            "Archive",
        ]
    ]
    assert result.data["page"] == {"page": 1, "pages": 15, "total": 719}
    assert result.data["email_ids"] == ["87596", "87597"]


def test_search_builds_scope_and_filter_args():
    calls = []

    def runner(args):
        calls.append(args)
        return _proc(EMAILS_OUTPUT)

    source = SparkEmailSource(runner=runner)

    source.search("receipt", filter_="after:2026/01/01", in_="user@example.com:Archive")

    assert calls == [
        [
            "spark",
            "search",
            "--filter",
            "after:2026/01/01",
            "--in",
            "user@example.com:Archive",
            "receipt",
        ]
    ]


def test_search_parses_explicit_id_lines_without_body_numbers():
    source = SparkEmailSource(runner=lambda args: _proc(SEARCH_OUTPUT))

    result = source.search("receipt")

    assert result.data["email_ids"] == ["87510", "87511"]


def test_command_error_includes_stderr():
    source = SparkEmailSource(runner=lambda args: _proc(stderr="Spark is not running", returncode=1))

    with pytest.raises(SparkCommandError) as exc:
        source.accounts()

    assert "Spark is not running" in str(exc.value)


def test_from_config_uses_installed_source_yaml(tmp_root):
    cfg = Config(root=tmp_root)
    install_source_template(cfg, "spark_email")
    (cfg.sources_dir / "spark_email" / "source.yaml").write_text(
        "name: spark_email\n"
        "description: Custom Spark\n"
        "provider: spark\n"
        "enabled: true\n"
        "command: /tmp/custom-spark\n"
        "capabilities: [folders]\n"
        "config:\n"
        "  timeout_seconds: 12\n"
    )

    source = SparkEmailSource.from_config(cfg)

    assert source.command == "/tmp/custom-spark"
    assert source.timeout_seconds == 12
