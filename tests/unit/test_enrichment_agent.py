import os
import sys
import types

from personal_db.enrichments.agent import (
    EnrichmentAgentRequest,
    OpenAIAgentsReceiptHarness,
    ReceiptAgentOutput,
    StubEnrichmentAgentHarness,
    receipt_harness_from_env,
)


def test_receipt_harness_from_env_defaults_to_stub(monkeypatch):
    monkeypatch.delenv("PERSONAL_DB_RECEIPT_HARNESS", raising=False)

    harness = receipt_harness_from_env()

    assert isinstance(harness, StubEnrichmentAgentHarness)


def test_receipt_harness_from_env_selects_openai(monkeypatch):
    monkeypatch.setenv("PERSONAL_DB_RECEIPT_HARNESS", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("PERSONAL_DB_OPENAI_MODEL", "gpt-test")

    harness = receipt_harness_from_env()

    assert isinstance(harness, OpenAIAgentsReceiptHarness)
    assert harness.model == "gpt-test"


def test_openai_key_loads_from_explicit_env_path(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("OPENAI_API_KEY=test-key-from-file\n")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PERSONAL_DB_RECEIPT_HARNESS", "openai")
    monkeypatch.setenv("PERSONAL_DB_OPENAI_ENV_PATH", str(env_path))

    harness = receipt_harness_from_env()

    assert isinstance(harness, OpenAIAgentsReceiptHarness)
    assert os.environ["OPENAI_API_KEY"] == "test-key-from-file"


def test_openai_agents_receipt_harness_uses_pydantic_output(monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured["agent"] = kwargs

    class FakeRunConfig:
        def __init__(self, **kwargs):
            captured["run_config"] = kwargs

    class FakeUsage:
        requests = 1
        input_tokens = 10
        output_tokens = 5
        total_tokens = 15

    class FakeResult:
        final_output = ReceiptAgentOutput(
            receipt_match="yes",
            merchant="Lyft",
            description="Ride receipt",
            category="TRANSPORTATION",
            amount=50.3,
            currency="USD",
            transaction_date="2026-06-01",
            reasoning="Email matches merchant, date, and amount.",
        )
        context_wrapper = types.SimpleNamespace(usage=FakeUsage())

    class FakeRunner:
        @staticmethod
        def run_sync(agent, prompt, *, max_turns, run_config):
            captured["run_sync"] = {
                "agent": agent,
                "prompt": prompt,
                "max_turns": max_turns,
                "run_config": run_config,
            }
            return FakeResult()

    fake_agents = types.SimpleNamespace(
        Agent=FakeAgent,
        RunConfig=FakeRunConfig,
        Runner=FakeRunner,
    )
    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    request = EnrichmentAgentRequest(
        enrichment_name="finance.transaction_receipt_v1",
        prompt_version="finance-receipt-v1",
        input={"transaction": {"finance_transaction_id": "txn-1"}, "task": "match receipt"},
        context=[{"message_id": "123", "text": "Lyft $50.30 receipt"}],
    )
    harness = OpenAIAgentsReceiptHarness(model="gpt-test", max_turns=1)

    result = harness.run(request)

    assert captured["agent"]["model"] == "gpt-test"
    assert captured["agent"]["output_type"] is ReceiptAgentOutput
    assert captured["run_config"]["tracing_disabled"] is True
    assert captured["run_sync"]["max_turns"] == 1
    assert "txn-1" in captured["run_sync"]["prompt"]
    assert result.result["merchant"] == "Lyft"
    assert result.result["_usage"]["total_tokens"] == 15
    assert result.model == "gpt-test"
