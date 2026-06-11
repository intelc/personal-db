from personal_db.context_providers.email import SparkEmailContextProvider
from personal_db.remote_sources.base import RemoteCallResult


class FakeSparkSource:
    def __init__(self):
        self.calls = []

    def search(self, about, *, filter_=None, in_=None):
        self.calls.append(("search", about, filter_, in_))
        email_id = "456" if about == "12.34" else "123"
        return RemoteCallResult(
            source="spark_email",
            operation="search",
            raw_text=(
                "Emails matching receipt\n"
                f"  {email_id}  user@example.com  Merchant <m@example.com>  2026-06-01  Receipt\n"
                "Page 1 of 1 (1 total emails)\n"
            ),
            data={"email_ids": [email_id], "page": {"page": 1, "pages": 1, "total": 1}},
        )

    def thread(self, message_id, *, download_attachments=False):
        self.calls.append(("thread", message_id, download_attachments))
        return RemoteCallResult(
            source="spark_email",
            operation="thread",
            raw_text="From: Merchant\nBody: Receipt for $12.34",
            data={"message_id": message_id, "download_attachments": download_attachments},
        )


def test_search_receipts_builds_bounded_spark_query_and_evidence_refs():
    source = FakeSparkSource()
    provider = SparkEmailContextProvider(source)

    result = provider.search_receipts(
        merchant="Lyft",
        amount="$12.34",
        date_="2026-06-01",
        window_days=2,
        scope="Inbox",
    )

    assert source.calls == [
        (
            "search",
            "12.34",
            "after:2026/05/30 before:2026/06/04",
            "Inbox",
        ),
        (
            "search",
            "Lyft receipt invoice order confirmation 12.34",
            "after:2026/05/30 before:2026/06/04",
            "Inbox",
        ),
    ]
    assert result.query["amount"] == "12.34"
    assert result.query["strategies"][0]["name"] == "amount_date"
    assert result.evidence[0].ref == "spark_email:message:456"
    assert result.evidence[1].ref == "spark_email:message:123"
    assert result.data["email_ids"] == ["456", "123"]
    assert result.data["searches"][0]["email_ids"] == ["456"]


def test_read_thread_returns_thread_evidence_ref_and_excerpt():
    source = FakeSparkSource()
    provider = SparkEmailContextProvider(source)

    result = provider.read_thread("123")

    assert source.calls == [("thread", "123", False)]
    assert result.evidence[0].kind == "email_thread"
    assert result.evidence[0].excerpt == "From: Merchant Body: Receipt for $12.34"
