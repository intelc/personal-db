import json
from datetime import UTC, datetime, timedelta

from personal_db.config import Config
from personal_db.context_providers.base import ContextResult, EvidenceRef
from personal_db.db import connect, init_db
from personal_db.enrichments.agent import EnrichmentAgentResult
from personal_db.enrichments.core import (
    EnrichmentRunRecord,
    cancel_enrichment_job,
    claim_due_enrichment_jobs,
    enqueue_enrichment_job,
    enrichment_queue_summary,
    get_enrichment_job_detail,
    get_latest_enrichment,
    list_enrichment_jobs,
    mark_enrichment_job_complete,
    mark_enrichment_job_failed,
    reap_expired_enrichment_jobs,
    record_enrichment_run,
    retry_enrichment_job,
)
from personal_db.enrichments.finance import (
    DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
    RECEIPT_ENRICHMENT,
    RECEIPT_V1_ENRICHMENT,
    FinanceTransaction,
    debug_receipt_batch_v1,
    debug_transaction_receipt_v1,
    enqueue_missing_receipt_enrichments,
    enqueue_missing_receipt_v1_enrichments,
    enrich_transaction_receipt_stub,
    enrich_transaction_receipt_v1,
    extract_receipt_evidence_windows,
    run_due_finance_receipt_jobs,
    run_due_finance_receipt_v1_jobs,
)


class FakeReceiptProvider:
    def search_receipts(
        self,
        *,
        merchant=None,
        amount=None,
        date_=None,
        window_days=7,
        scope=None,
    ):
        assert merchant == "Lyft"
        assert amount == 50.3
        assert date_ == "2026-06-01"
        assert window_days == 2
        assert scope == "Inbox"
        return ContextResult(
            provider="email",
            operation="search_receipts",
            query={
                "merchant": merchant,
                "amount": "50.30",
                "date": date_,
                "window_days": window_days,
                "scope": scope,
            },
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref="spark_email:message:87510",
                    kind="email_message",
                    title="Spark email message 87510",
                )
            ],
            data={"email_ids": ["87510"]},
            raw_text="raw",
        )


class FakeReceiptV1Provider(FakeReceiptProvider):
    def __init__(self):
        self.thread_calls = []

    def search_receipts(
        self,
        *,
        merchant=None,
        amount=None,
        date_=None,
        window_days=7,
        scope=None,
    ):
        base = super().search_receipts(
            merchant=merchant,
            amount=amount,
            date_=date_,
            window_days=window_days,
            scope=scope,
        )
        return ContextResult(
            provider=base.provider,
            operation=base.operation,
            query=base.query,
            evidence=[
                *base.evidence,
                EvidenceRef(
                    source="spark_email",
                    ref="spark_email:message:87511",
                    kind="email_message",
                    title="Spark email message 87511",
                ),
            ],
            data={"email_ids": ["87510", "87511"]},
            raw_text=base.raw_text,
        )

    def read_thread(self, message_id, *, download_attachments=False):
        self.thread_calls.append(message_id)
        text = (
            "Thanks for riding with Lyft.\n\n"
            "Receipt from Lyft for $50.30 on June 1, 2026. "
            f"Message {message_id}. Total charged to your card: USD 50.30."
        )
        if str(message_id) == "87511":
            text = (
                "Thanks for riding with Lyft.\n\n"
                "Receipt from Lyft for $13.30 on May 27, 2026. "
                f"Message {message_id}. Total charged to your card: USD 13.30."
            )
        return ContextResult(
            provider="email",
            operation="read_thread",
            query={"message_id": str(message_id)},
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref=f"spark_email:message:{message_id}",
                    kind="email_thread",
                    title=f"Spark email thread {message_id}",
                    excerpt=f"Receipt thread {message_id}",
                )
            ],
            data={},
            raw_text=text,
        )


class FakeReceiptHarness:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        assert request.enrichment_name == RECEIPT_V1_ENRICHMENT
        assert request.input["transaction"]["finance_transaction_id"] == "txn-1"
        assert len(request.context) == 2
        assert request.context[0]["kind"] == "candidate_evidence"
        candidates = request.context[0]["candidates"]
        assert [item["message_id"] for item in candidates] == ["87510", "87511"]
        assert candidates[0]["signals"]["amount"] is True
        assert candidates[0]["signals"]["date"] is True
        assert candidates[1]["signals"]["merchant"] is True
        assert request.context[1]["kind"] == "full_thread"
        assert request.context[1]["message_id"] == "87510"
        assert "Lyft" in request.context[1]["text"]
        return EnrichmentAgentResult(
            result={
                "receipt_match": "yes",
                "merchant": "Lyft",
                "description": "Ride receipt",
                "category": "TRANSPORTATION",
                "amount": 50.3,
                "currency": "USD",
                "transaction_date": "2026-06-01",
                "reasoning": "The email amount/date/merchant match the transaction.",
            },
            result_summary="Matched Lyft receipt",
            confidence=0.92,
            model="fake-model",
            prompt_version="fake-prompt",
        )


class FakeBatchReceiptProvider:
    def __init__(self):
        self.search_calls = []
        self.thread_calls = []

    def search_receipts(
        self,
        *,
        merchant=None,
        amount=None,
        date_=None,
        window_days=7,
        scope=None,
    ):
        self.search_calls.append((merchant, amount, date_, window_days, scope))
        message_id = "90001" if merchant == "Uber" else "87510"
        return ContextResult(
            provider="email",
            operation="search_receipts",
            query={
                "merchant": merchant,
                "amount": f"{float(amount):.2f}" if amount is not None else None,
                "date": date_,
                "window_days": window_days,
                "scope": scope,
            },
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref=f"spark_email:message:{message_id}",
                    kind="email_message",
                    title=f"Spark email message {message_id}",
                )
            ],
            data={"email_ids": [message_id]},
            raw_text="raw",
        )

    def read_thread(self, message_id, *, download_attachments=False):
        self.thread_calls.append(str(message_id))
        if str(message_id) == "90001":
            text = "Uber receipt. Total $21.00 on June 2, 2026. Thanks for riding with Uber."
        else:
            text = "Lyft receipt. Total $50.30 on June 1, 2026. Thanks for riding with Lyft."
        return ContextResult(
            provider="email",
            operation="read_thread",
            query={"message_id": str(message_id)},
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref=f"spark_email:message:{message_id}",
                    kind="email_thread",
                    title=f"Spark email thread {message_id}",
                )
            ],
            data={},
            raw_text=text,
        )


class FakeMixedReadyReceiptProvider:
    def __init__(self):
        self.search_calls = []
        self.thread_calls = []

    def search_receipts(
        self,
        *,
        merchant=None,
        amount=None,
        date_=None,
        window_days=7,
        scope=None,
    ):
        self.search_calls.append((merchant, amount, date_, window_days, scope))
        message_id = "90001" if merchant == "Uber" else "87510"
        return ContextResult(
            provider="email",
            operation="search_receipts",
            query={
                "merchant": merchant,
                "amount": f"{float(amount):.2f}" if amount is not None else None,
                "date": date_,
                "window_days": window_days,
                "scope": scope,
            },
            evidence=[],
            data={"email_ids": [message_id]},
            raw_text="raw",
        )

    def read_thread(self, message_id, *, download_attachments=False):
        self.thread_calls.append(str(message_id))
        if str(message_id) == "90001":
            text = "Uber receipt. Total $12.00 on June 2, 2026. Thanks for riding with Uber."
        else:
            text = "Lyft receipt. Total $50.30 on June 1, 2026. Thanks for riding with Lyft."
        return ContextResult(
            provider="email",
            operation="read_thread",
            query={"message_id": str(message_id)},
            evidence=[],
            data={},
            raw_text=text,
        )


class FakeBatchReceiptHarness:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        tx = request.input["transaction"]
        return EnrichmentAgentResult(
            result={
                "receipt_match": "yes",
                "merchant": tx["merchant_name"],
                "description": "Matched receipt",
                "category": tx["category"],
                "amount": tx["amount"],
                "currency": "USD",
                "transaction_date": tx["date"],
                "reasoning": "Candidate evidence contains amount/date/merchant.",
            },
            result_summary="Matched receipt",
            confidence=0.9,
            model="fake-model",
            prompt_version="fake-prompt",
        )


class FakeCombinedReceiptProvider:
    def __init__(self):
        self.thread_calls = []

    def search_receipts(
        self,
        *,
        merchant=None,
        amount=None,
        date_=None,
        window_days=7,
        scope=None,
    ):
        return ContextResult(
            provider="email",
            operation="search_receipts",
            query={"merchant": merchant, "amount": amount, "date": date_},
            evidence=[],
            data={"email_ids": ["combo-1", "combo-2"]},
            raw_text="raw",
        )

    def read_thread(self, message_id, *, download_attachments=False):
        self.thread_calls.append(str(message_id))
        amount = "$20.00" if str(message_id) == "combo-1" else "$30.30"
        return ContextResult(
            provider="email",
            operation="read_thread",
            query={"message_id": str(message_id)},
            evidence=[],
            data={},
            raw_text=f"Lyft receipt on June 1, 2026. Visa *8865 {amount}. Thanks for riding.",
        )


class FakeCombinedReceiptHarness:
    def __init__(self):
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        assert request.context[0]["kind"] == "candidate_evidence"
        assert request.context[1]["kind"] == "amount_combination"
        assert request.context[1]["combination"]["total"] == "50.30"
        return EnrichmentAgentResult(
            result={
                "receipt_match": "yes",
                "merchant": "Lyft",
                "description": "Combined Lyft receipts",
                "category": "TRANSPORTATION",
                "amount": 50.3,
                "currency": "USD",
                "transaction_date": "2026-06-01",
                "reasoning": "Two Lyft receipt charges sum to the transaction.",
            },
            result_summary="Matched combined Lyft receipts",
            confidence=0.88,
            model="fake-model",
            prompt_version="fake-prompt",
        )


def _seed_finance_transaction(cfg: Config) -> None:
    init_db(cfg.db_path)
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            CREATE TABLE finance_transactions (
              finance_transaction_id TEXT PRIMARY KEY,
              date TEXT,
              name TEXT,
              merchant_name TEXT,
              amount REAL,
              category TEXT
            )
            """
        )
        con.execute(
            """
            INSERT INTO finance_transactions(
              finance_transaction_id, date, name, merchant_name, amount, category
            )
            VALUES ('txn-1', '2026-06-01', 'LYFT RIDE', 'Lyft', 50.30, 'TRANSPORTATION')
            """
        )
        con.execute(
            """
            INSERT INTO finance_transactions(
              finance_transaction_id, date, name, merchant_name, amount, category
            )
            VALUES ('txn-2', '2026-06-02', 'UBER TRIP', 'Uber', 21.00, 'TRANSPORTATION')
            """
        )
        con.commit()
    finally:
        con.close()


def _seed_finance_receipt_candidate_filter_transactions(cfg: Config) -> None:
    init_db(cfg.db_path)
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            CREATE TABLE finance_transactions (
              finance_transaction_id TEXT PRIMARY KEY,
              date TEXT,
              name TEXT,
              merchant_name TEXT,
              amount REAL,
              category TEXT,
              pending INTEGER NOT NULL DEFAULT 0,
              is_credit_card_payment INTEGER NOT NULL DEFAULT 0,
              is_internal_transfer INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        con.executemany(
            """
            INSERT INTO finance_transactions(
              finance_transaction_id, date, name, merchant_name, amount, category,
              pending, is_credit_card_payment, is_internal_transfer
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("txn-receipt", "2026-06-01", "Lyft", "Lyft", 50.30, "TRANSPORTATION", 0, 0, 0),
                (
                    "txn-card-payment",
                    "2026-06-01",
                    "BILL PAY WELLS FARGO CREDIT CARD",
                    None,
                    2455.28,
                    "LOAN_PAYMENTS_CREDIT_CARD_PAYMENT",
                    0,
                    1,
                    0,
                ),
                (
                    "txn-transfer",
                    "2026-06-01",
                    "Corp E Corp E-CHECK Yiheng Chen",
                    None,
                    890.00,
                    "TRANSFER_OUT_ACCOUNT_TRANSFER",
                    0,
                    0,
                    1,
                ),
                ("txn-pending", "2026-06-01", "Apple", "Apple", 31.79, "SHOPPING", 1, 0, 0),
            ],
        )
        con.commit()
    finally:
        con.close()


def test_finance_receipt_stub_records_run_latest_and_evidence(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)

    result = enrich_transaction_receipt_stub(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        provider=FakeReceiptProvider(),
    )

    assert result["status"] == "context_found"
    assert result["result"]["decision"] == "needs_llm"
    assert result["evidence"][0]["ref"] == "spark_email:message:87510"

    latest = get_latest_enrichment(cfg, RECEIPT_ENRICHMENT, "finance_transactions", "txn-1")
    assert latest is not None
    assert latest["status"] == "context_found"
    assert latest["result"]["receipt_message_ids"] == ["87510"]

    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            """
            SELECT source, ref, kind
            FROM enrichment_evidence
            WHERE run_id=?
            """,
            (result["run_id"],),
        ).fetchone()
        run_json = con.execute(
            "SELECT result_json FROM enrichment_runs WHERE run_id=?",
            (result["run_id"],),
        ).fetchone()[0]
    finally:
        con.close()
    assert row == ("spark_email", "spark_email:message:87510", "email_message")
    assert json.loads(run_json)["receipt_candidate_count"] == 1


def test_finance_receipt_v1_reads_bounded_threads_and_records_agent_result(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeReceiptV1Provider()
    harness = FakeReceiptHarness()

    result = enrich_transaction_receipt_v1(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        max_threads=1,
        provider=provider,
        harness=harness,
    )

    assert result["enrichment_name"] == RECEIPT_V1_ENRICHMENT
    assert result["status"] == "enriched"
    assert result["result"]["decision"] == "receipt_matched"
    assert result["result"]["receipt_message_ids"] == ["87510", "87511"]
    assert result["result"]["inspected_message_ids"] == ["87510", "87511"]
    assert result["result"]["full_context_message_ids"] == ["87510"]
    assert result["result"]["candidate_evidence_count"] == 2
    assert result["result"]["agent_result"]["merchant"] == "Lyft"
    assert result["confidence"] == 0.92
    assert provider.thread_calls == ["87510", "87511"]
    assert len(harness.requests) == 1

    latest = get_latest_enrichment(cfg, RECEIPT_V1_ENRICHMENT, "finance_transactions", "txn-1")
    assert latest is not None
    assert latest["status"] == "enriched"
    assert latest["result"]["agent_result"]["receipt_match"] == "yes"

    detail = get_enrichment_job_detail(
        cfg,
        enqueue_enrichment_job(
            cfg,
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-1",
            force=True,
        )["job"]["job_id"],
    )
    assert detail["latest"]["run_id"] == result["run_id"]


def test_extract_receipt_evidence_windows_finds_amount_date_and_merchant():
    tx = FinanceTransaction(
        finance_transaction_id="txn-1",
        date="2026-06-01",
        name="LYFT RIDE",
        merchant_name="Lyft",
        amount=50.30,
        category="TRANSPORTATION",
    )
    text = (
        "Header\n\n"
        "Your Lyft receipt is ready. Total charged: $50.30 on June 1, 2026. "
        "Thanks for riding.\n\nFooter"
    )

    evidence = extract_receipt_evidence_windows(tx, "87510", text, window_chars=80)

    assert evidence["signals"] == {
        "amount": True,
        "date": True,
        "merchant": True,
        "receipt_language": True,
    }
    assert "$50.30" in {item["matched"] for item in evidence["snippets"]}
    assert evidence["primary_amount"]["value"] == "50.30"
    assert any("Your Lyft receipt is ready" in item["snippet"] for item in evidence["snippets"])


def test_extract_receipt_evidence_windows_prefers_real_merchant_over_generic_name():
    tx = FinanceTransaction(
        finance_transaction_id="txn-fee",
        date="2026-06-01",
        name="MONTHLY SERVICE FEE",
        merchant_name="Wells Fargo",
        amount=25.00,
        category="Financial Fees",
    )
    text = "Monthly service fee notice. Total $25.00 on June 1, 2026."

    evidence = extract_receipt_evidence_windows(tx, "fee-1", text, window_chars=80)

    assert evidence["signals"]["amount"] is True
    assert evidence["signals"]["date"] is True
    assert evidence["signals"]["merchant"] is False


def test_extract_receipt_evidence_windows_ignores_generic_corp_descriptor():
    tx = FinanceTransaction(
        finance_transaction_id="txn-echeck",
        date="2026-06-01",
        name="Corp E Corp E-CHECK 053126 0230386785 Yiheng Chen",
        merchant_name=None,
        amount=890.00,
        category="LOAN_PAYMENTS_OTHER_PAYMENT",
    )
    text = (
        "Corporate update for Yiheng Chen. "
        "Transfer amount $890.00 posted on June 1, 2026."
    )

    evidence = extract_receipt_evidence_windows(tx, "echeck-1", text, window_chars=80)

    assert evidence["signals"]["amount"] is True
    assert evidence["signals"]["date"] is True
    assert evidence["signals"]["merchant"] is False


def test_finance_receipt_debug_reads_candidates_without_persisting(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeReceiptV1Provider()

    result = debug_transaction_receipt_v1(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        provider=provider,
    )

    assert result["persisted"] is False
    assert result["run_agent"] is False
    assert result["decision"] == "debug"
    assert result["receipt_message_ids"] == ["87510", "87511"]
    assert result["inspected_message_ids"] == ["87510", "87511"]
    assert result["full_context_message_ids"] == ["87510"]
    assert result["candidate_evidence"][0]["signals"]["amount"] is True
    assert provider.thread_calls == ["87510", "87511"]
    assert get_latest_enrichment(cfg, RECEIPT_V1_ENRICHMENT, "finance_transactions", "txn-1") is None


def test_finance_receipt_debug_can_run_agent_without_persisting(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeReceiptV1Provider()
    harness = FakeReceiptHarness()

    result = debug_transaction_receipt_v1(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        run_agent=True,
        provider=provider,
        harness=harness,
    )

    assert result["persisted"] is False
    assert result["run_agent"] is True
    assert result["decision"] == "receipt_matched"
    assert result["agent_result"]["merchant"] == "Lyft"
    assert result["confidence"] == 0.92
    assert len(harness.requests) == 1
    assert get_latest_enrichment(cfg, RECEIPT_V1_ENRICHMENT, "finance_transactions", "txn-1") is None


def test_finance_receipt_debug_detects_combined_amount_evidence(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeCombinedReceiptProvider()

    result = debug_transaction_receipt_v1(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        provider=provider,
    )

    assert result["amount_combination"]["total"] == "50.30"
    assert result["amount_combination"]["message_ids"] == ["combo-2", "combo-1"]
    assert result["candidate_evidence"][0]["primary_amount"]["value"] == "20.00"
    assert provider.thread_calls == ["combo-1", "combo-2"]


def test_finance_receipt_debug_passes_combined_amount_evidence_to_agent(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeCombinedReceiptProvider()
    harness = FakeCombinedReceiptHarness()

    result = debug_transaction_receipt_v1(
        cfg,
        "txn-1",
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        run_agent=True,
        provider=provider,
        harness=harness,
    )

    assert result["decision"] == "receipt_matched"
    assert result["amount_combination"]["target"] == "50.30"
    assert result["agent_result"]["description"] == "Combined Lyft receipts"
    assert len(harness.requests) == 1


def test_finance_receipt_debug_batch_summarizes_evidence_buckets(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeBatchReceiptProvider()

    result = debug_receipt_batch_v1(
        cfg,
        limit=2,
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        provider=provider,
    )

    assert result["selected"] == 2
    assert result["run_agent"] is False
    assert result["summary"]["buckets"] == {"evidence_found": 2}
    assert result["summary"]["ok"] == 0
    assert result["summary"]["ready_for_agent"] == 2
    assert result["summary"]["needs_attention"] == 0
    assert [item["transaction"]["finance_transaction_id"] for item in result["items"]] == [
        "txn-2",
        "txn-1",
    ]
    assert result["items"][0]["best_candidate_ids"] == ["90001"]
    assert result["items"][0]["has_complete_evidence"] is True
    assert result["items"][0]["failure_bucket"] == "evidence_found"
    assert provider.thread_calls == ["90001", "87510"]
    assert get_latest_enrichment(cfg, RECEIPT_V1_ENRICHMENT, "finance_transactions", "txn-1") is None


def test_finance_receipt_debug_batch_filters_by_date_range(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeBatchReceiptProvider()

    result = debug_receipt_batch_v1(
        cfg,
        limit=10,
        start_date="2026-06-01",
        end_date="2026-06-02",
        window_days=2,
        scope="Inbox",
        provider=provider,
    )

    assert result["start_date"] == "2026-06-01"
    assert result["end_date"] == "2026-06-02"
    assert result["selected"] == 1
    assert result["items"][0]["transaction"]["finance_transaction_id"] == "txn-1"


def test_finance_receipt_debug_batch_can_run_agent_and_bucket_ok(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeBatchReceiptProvider()
    harness = FakeBatchReceiptHarness()

    result = debug_receipt_batch_v1(
        cfg,
        limit=1,
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        run_agent=True,
        provider=provider,
        harness=harness,
    )

    assert result["selected"] == 1
    assert result["summary"]["buckets"] == {"ok": 1}
    assert result["summary"]["ok"] == 1
    assert result["summary"]["ready_for_agent"] == 0
    assert result["summary"]["needs_attention"] == 0
    assert result["items"][0]["agent_decision"] == "yes"
    assert result["items"][0]["agent_confidence"] == 0.9
    assert result["items"][0]["failure_bucket"] == "ok"
    assert len(harness.requests) == 1


def test_enqueue_missing_receipt_enrichments_skips_existing_latest(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-2",
            status="no_context",
        ),
    )

    result = enqueue_missing_receipt_enrichments(cfg, limit=10, window_days=2, scope="Inbox")

    assert result["selected"] == 1
    job = result["jobs"][0]["job"]
    assert job["input_id"] == "txn-1"
    assert job["status"] == "pending"
    assert job["payload"] == {"window_days": 2, "scope": "Inbox"}

    again = enqueue_missing_receipt_enrichments(cfg, limit=10, window_days=2, scope="Inbox")
    assert again["jobs"][0]["created"] is False


def test_enqueue_missing_receipt_v1_enrichments_uses_v1_name_and_payload(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)

    result = enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=1,
        window_days=2,
        scope="Inbox",
        max_threads=2,
        max_candidate_threads=7,
    )

    assert result["enrichment_name"] == RECEIPT_V1_ENRICHMENT
    assert result["selected"] == 1
    job = result["jobs"][0]["job"]
    assert job["enrichment_name"] == RECEIPT_V1_ENRICHMENT
    assert job["payload"] == {
        "window_days": 2,
        "scope": "Inbox",
        "max_threads": 2,
        "max_candidate_threads": 7,
        "snippet_window_chars": 300,
    }


def test_enqueue_missing_receipt_v1_enrichments_filters_by_date_range(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)

    result = enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=10,
        start_date="2026-06-01",
        end_date="2026-06-02",
        window_days=2,
        scope="Inbox",
        max_threads=2,
        max_candidate_threads=7,
    )

    assert result["selected"] == 1
    assert result["ready_selected"] == 1
    assert result["enqueued"] == 1
    job = result["jobs"][0]["job"]
    assert job["input_id"] == "txn-1"


def test_enqueue_missing_receipt_v1_enrichments_only_ready_screens_before_queue(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    provider = FakeMixedReadyReceiptProvider()

    result = enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=2,
        window_days=2,
        scope="Inbox",
        max_threads=1,
        max_candidate_threads=2,
        only_ready=True,
        provider=provider,
    )

    assert result["selected"] == 2
    assert result["ready_selected"] == 1
    assert result["enqueued"] == 1
    assert result["only_ready"] is True
    assert result["readiness_summary"] == {
        "screened": 2,
        "buckets": {"no_exact_amount": 1, "evidence_found": 1},
        "ready": 1,
        "not_ready": 1,
    }
    assert result["skipped"][0]["transaction"]["finance_transaction_id"] == "txn-2"
    assert result["skipped"][0]["failure_bucket"] == "no_exact_amount"
    job = result["jobs"][0]["job"]
    assert job["input_id"] == "txn-1"
    assert job["payload"]["ready_check"]["failure_bucket"] == "evidence_found"
    assert job["payload"]["ready_check"]["best_candidate_ids"] == ["87510"]
    assert job["payload"]["ready_check"]["has_complete_evidence"] is True
    assert provider.thread_calls == ["90001", "87510"]


def test_enqueue_missing_receipt_enrichments_requeues_stale_latest(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-2",
            status="no_context",
        ),
    )
    old = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    con = connect(cfg.db_path)
    try:
        con.execute(
            """
            UPDATE enrichment_latest
            SET updated_at=?
            WHERE enrichment_name=? AND input_table='finance_transactions' AND input_id='txn-2'
            """,
            (old, RECEIPT_ENRICHMENT),
        )
        con.commit()
    finally:
        con.close()

    result = enqueue_missing_receipt_enrichments(
        cfg,
        limit=10,
        window_days=2,
        stale_after_days=30,
    )

    assert result["selected"] == 2
    input_ids = {item["job"]["input_id"] for item in result["jobs"]}
    assert input_ids == {"txn-1", "txn-2"}


def test_enqueue_missing_receipt_v1_enrichments_requeues_selected_latest_statuses(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-1",
            status="enriched",
        ),
    )
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-2",
            status="no_match",
        ),
    )

    result = enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=10,
        window_days=2,
        rerun_statuses=["no_match", "uncertain", "no_match"],
    )

    assert result["selected"] == 1
    assert result["rerun_statuses"] == ["no_match", "uncertain"]
    assert result["jobs"][0]["job"]["input_id"] == "txn-2"


def test_receipt_candidate_selection_skips_pending_transfers_and_card_payments(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_receipt_candidate_filter_transactions(cfg)

    debug = debug_receipt_batch_v1(
        cfg,
        limit=10,
        start_date="2026-06-01",
        end_date="2026-06-02",
        window_days=2,
        provider=FakeBatchReceiptProvider(),
    )
    queued = enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=10,
        start_date="2026-06-01",
        end_date="2026-06-02",
        window_days=2,
    )

    assert [item["transaction"]["finance_transaction_id"] for item in debug["items"]] == [
        "txn-receipt"
    ]
    assert queued["selected"] == 1
    assert queued["jobs"][0]["job"]["input_id"] == "txn-receipt"


def test_run_due_finance_receipt_jobs_marks_job_succeeded(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    enqueue_missing_receipt_enrichments(cfg, limit=1, window_days=2, scope="Inbox")

    def fake_enrich(cfg, transaction_id, *, window_days=7, scope=None):
        assert transaction_id == "txn-2"
        assert window_days == 2
        assert scope == "Inbox"
        return {"run_id": "run-queued", "status": "context_found"}

    monkeypatch.setattr("personal_db.enrichments.finance.enrich_transaction_receipt_stub", fake_enrich)

    result = run_due_finance_receipt_jobs(cfg, limit=5)

    assert result["ran"] == 1
    assert result["results"][0]["ok"] is True
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT status, attempts, last_run_id FROM enrichment_jobs"
        ).fetchone()
    finally:
        con.close()
    assert row == ("succeeded", 1, "run-queued")


def test_run_due_finance_receipt_v1_jobs_marks_job_succeeded(tmp_root, monkeypatch):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    enqueue_missing_receipt_v1_enrichments(
        cfg,
        limit=1,
        window_days=2,
        scope="Inbox",
        max_threads=2,
        max_candidate_threads=9,
    )

    def fake_enrich(
        cfg,
        transaction_id,
        *,
        window_days=7,
        scope=None,
        max_threads=3,
        max_candidate_threads=DEFAULT_MAX_RECEIPT_CANDIDATE_THREADS,
        snippet_window_chars=300,
        harness=None,
    ):
        assert transaction_id == "txn-2"
        assert window_days == 2
        assert scope == "Inbox"
        assert max_threads == 2
        assert max_candidate_threads == 9
        assert snippet_window_chars == 300
        assert harness is None
        return {"run_id": "run-v1", "status": "enriched"}

    monkeypatch.setattr("personal_db.enrichments.finance.enrich_transaction_receipt_v1", fake_enrich)

    result = run_due_finance_receipt_v1_jobs(cfg, limit=5)

    assert result["enrichment_name"] == RECEIPT_V1_ENRICHMENT
    assert result["ran"] == 1
    assert result["results"][0]["ok"] is True
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT enrichment_name, status, attempts, last_run_id FROM enrichment_jobs"
        ).fetchone()
    finally:
        con.close()
    assert row == (RECEIPT_V1_ENRICHMENT, "succeeded", 1, "run-v1")


def test_failed_claimed_job_retries_after_backoff(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    job = enqueue_enrichment_job(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        input_table="finance_transactions",
        input_id="txn-1",
    )["job"]

    claimed = claim_due_enrichment_jobs(cfg, enrichment_name=RECEIPT_ENRICHMENT)
    assert claimed[0].job_id == job["job_id"]

    failed = mark_enrichment_job_failed(
        cfg,
        job["job_id"],
        error="temporary failure",
        retry_delay_seconds=120,
    )

    assert failed["status"] == "pending"
    assert failed["attempts"] == 1
    assert failed["last_error"] == "temporary failure"
    assert failed["run_after"] > failed["updated_at"]
    assert failed["lease_until"] is None


def test_list_show_retry_and_cancel_enrichment_jobs(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    job = enqueue_enrichment_job(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        input_table="finance_transactions",
        input_id="txn-1",
        payload={"window_days": 2},
    )["job"]
    run = record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-1",
            status="context_found",
            result={"decision": "needs_llm"},
            evidence=[
                EvidenceRef(
                    source="spark_email",
                    ref="spark_email:message:123",
                    kind="email_message",
                    title="Receipt",
                )
            ],
        ),
    )
    mark_enrichment_job_complete(cfg, job["job_id"], run_id=run["run_id"])

    listed = list_enrichment_jobs(cfg, status="succeeded", limit=10)
    assert listed[0]["job_id"] == job["job_id"]

    detail = get_enrichment_job_detail(cfg, job["job_id"])
    assert detail["job"]["last_run_id"] == run["run_id"]
    assert detail["last_run"]["evidence"][0]["ref"] == "spark_email:message:123"
    assert detail["latest"]["result"]["decision"] == "needs_llm"

    retried = retry_enrichment_job(cfg, job["job_id"])
    assert retried["status"] == "pending"
    assert retried["attempts"] == 0
    assert retried["last_error"] is None

    canceled = cancel_enrichment_job(cfg, job["job_id"], reason="not needed")
    assert canceled["status"] == "canceled"
    assert canceled["last_error"] == "canceled: not needed"


def test_enrichment_queue_summary_counts_statuses_and_latest_runs(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    failed_job = enqueue_enrichment_job(
        cfg,
        enrichment_name=RECEIPT_V1_ENRICHMENT,
        input_table="finance_transactions",
        input_id="txn-2",
    )["job"]
    claimed = claim_due_enrichment_jobs(cfg, enrichment_name=RECEIPT_V1_ENRICHMENT, limit=1)
    assert claimed[0].job_id == failed_job["job_id"]
    mark_enrichment_job_failed(cfg, claimed[0].job_id, error="temporary", retry_delay_seconds=0)
    claimed = claim_due_enrichment_jobs(cfg, enrichment_name=RECEIPT_V1_ENRICHMENT, limit=1)
    mark_enrichment_job_failed(cfg, claimed[0].job_id, error="temporary", retry_delay_seconds=0)
    claimed = claim_due_enrichment_jobs(cfg, enrichment_name=RECEIPT_V1_ENRICHMENT, limit=1)
    mark_enrichment_job_failed(cfg, claimed[0].job_id, error="final")
    record_enrichment_run(
        cfg,
        EnrichmentRunRecord(
            enrichment_name=RECEIPT_V1_ENRICHMENT,
            input_table="finance_transactions",
            input_id="txn-1",
            status="enriched",
        ),
    )

    summary = enrichment_queue_summary(cfg)

    entry = summary["by_enrichment"][RECEIPT_V1_ENRICHMENT]
    assert entry["statuses"]["failed"] == 1
    assert entry["latest_run_completed_at"] is not None
    assert summary["failed_jobs"][0]["job_id"] == failed_job["job_id"]
    assert summary["failed_jobs"][0]["last_error"] == "final"


def test_reap_expired_enrichment_jobs_returns_running_job_to_pending(tmp_root):
    cfg = Config(root=tmp_root)
    _seed_finance_transaction(cfg)
    job = enqueue_enrichment_job(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        input_table="finance_transactions",
        input_id="txn-1",
    )["job"]
    claimed = claim_due_enrichment_jobs(
        cfg,
        enrichment_name=RECEIPT_ENRICHMENT,
        lease_seconds=-1,
    )
    assert claimed[0].status == "running"

    reaped = reap_expired_enrichment_jobs(cfg)

    assert reaped["job_ids"] == [job["job_id"]]
    con = connect(cfg.db_path, read_only=True)
    try:
        row = con.execute(
            "SELECT status, locked_at, lease_until, last_error FROM enrichment_jobs WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
    finally:
        con.close()
    assert row == ("pending", None, None, "lease expired")
