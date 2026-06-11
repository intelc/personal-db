from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from dotenv import dotenv_values
from pydantic import BaseModel, Field


@dataclass(frozen=True)
class EnrichmentAgentRequest:
    """Structured request handed to an enrichment agent/harness."""

    enrichment_name: str
    prompt_version: str
    input: dict[str, Any]
    context: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class EnrichmentAgentResult:
    """Structured result returned by an enrichment agent/harness."""

    result: dict[str, Any]
    result_summary: str
    confidence: float | None = None
    model: str | None = None
    prompt_version: str | None = None
    raw_text: str | None = None


class EnrichmentAgentHarness(Protocol):
    def run(self, request: EnrichmentAgentRequest) -> EnrichmentAgentResult:
        """Run an enrichment request and return structured output."""


class StubEnrichmentAgentHarness:
    """Deterministic harness used until a real LLM backend is configured."""

    model = "stub"

    def run(self, request: EnrichmentAgentRequest) -> EnrichmentAgentResult:
        return EnrichmentAgentResult(
            result={
                "receipt_match": "unknown",
                "merchant": None,
                "description": None,
                "category": None,
                "amount": None,
                "currency": None,
                "transaction_date": None,
                "reasoning": "No LLM harness is configured.",
            },
            result_summary="LLM enrichment harness is not configured",
            confidence=0.0,
            model=self.model,
            prompt_version=request.prompt_version,
        )


class ReceiptAgentOutput(BaseModel):
    receipt_match: str = Field(description="yes, no, or unknown")
    merchant: str | None = None
    description: str | None = None
    category: str | None = None
    amount: float | None = None
    currency: str | None = None
    transaction_date: str | None = None
    reasoning: str = Field(description="Brief explanation grounded in the provided context.")


class OpenAIAgentsReceiptHarness:
    """OpenAI Agents SDK-backed harness for receipt enrichment."""

    def __init__(
        self,
        *,
        model: str = "gpt-5-mini",
        max_turns: int = 1,
        tracing_disabled: bool = True,
    ) -> None:
        self.model = model
        self.max_turns = max_turns
        self.tracing_disabled = tracing_disabled

    @classmethod
    def from_env(cls) -> OpenAIAgentsReceiptHarness:
        _load_openai_env()
        return cls(
            model=os.environ.get("PERSONAL_DB_OPENAI_MODEL")
            or os.environ.get("OPENAI_MODEL")
            or "gpt-5-mini",
            max_turns=_env_int("PERSONAL_DB_OPENAI_MAX_TURNS", 1),
            tracing_disabled=not _env_bool("PERSONAL_DB_OPENAI_TRACING_ENABLED", False),
        )

    def run(self, request: EnrichmentAgentRequest) -> EnrichmentAgentResult:
        _load_openai_env()
        if not os.environ.get("OPENAI_API_KEY"):
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Set it in the environment, "
                "~/personal_db/.env, or PERSONAL_DB_OPENAI_ENV_PATH."
            )
        from agents import Agent, RunConfig, Runner

        agent = Agent(
            name="Finance receipt enrichment",
            instructions=_receipt_agent_instructions(),
            model=self.model,
            output_type=ReceiptAgentOutput,
        )
        run_result = Runner.run_sync(
            agent,
            _receipt_agent_prompt(request),
            max_turns=self.max_turns,
            run_config=RunConfig(
                tracing_disabled=self.tracing_disabled,
                workflow_name=request.enrichment_name,
            ),
        )
        output = run_result.final_output
        if not isinstance(output, ReceiptAgentOutput):
            output = ReceiptAgentOutput.model_validate(output)
        usage = getattr(getattr(run_result, "context_wrapper", None), "usage", None)
        result = output.model_dump()
        if usage is not None:
            result["_usage"] = _usage_dict(usage)
        return EnrichmentAgentResult(
            result=result,
            result_summary=f"Receipt match: {output.receipt_match} ({output.merchant or 'unknown merchant'})",
            confidence=_confidence_from_match(output.receipt_match),
            model=self.model,
            prompt_version=request.prompt_version,
        )


def receipt_harness_from_env() -> EnrichmentAgentHarness:
    backend = os.environ.get("PERSONAL_DB_RECEIPT_HARNESS", "stub").strip().lower()
    if backend in {"openai", "agents", "openai_agents"}:
        return OpenAIAgentsReceiptHarness.from_env()
    return StubEnrichmentAgentHarness()


def _receipt_agent_instructions() -> str:
    return (
        "You classify whether the provided email context explains one finance "
        "transaction. Only use the transaction and email snippets provided. "
        "Return structured output. receipt_match must be yes, no, or unknown. "
        "Use yes only when merchant, amount, and timing are plausibly consistent. "
        "The context may include deterministic candidate_evidence snippets for "
        "many candidate emails plus fuller full_thread excerpts for the highest "
        "ranked candidates. Prefer exact amount/date/merchant evidence over search "
        "rank. Some merchants batch multiple same-day receipts into one card "
        "charge; when candidate_evidence includes amount_combination-style "
        "components, consider whether their sum explains the transaction. Keep "
        "reasoning concise and mention the decisive evidence."
    )


def _receipt_agent_prompt(request: EnrichmentAgentRequest) -> str:
    payload = {
        "task": request.input.get("task"),
        "transaction": request.input.get("transaction"),
        "context": request.context,
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _load_openai_env() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return
    explicit = os.environ.get("PERSONAL_DB_OPENAI_ENV_PATH")
    if not explicit:
        return
    path = Path(explicit).expanduser()
    if not path.exists():
        return
    key = dotenv_values(path).get("OPENAI_API_KEY")
    if key:
        os.environ.setdefault("OPENAI_API_KEY", key)


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _confidence_from_match(match: str) -> float:
    normalized = match.strip().lower()
    if normalized == "yes":
        return 0.85
    if normalized == "no":
        return 0.6
    return 0.25


def _usage_dict(usage: Any) -> dict[str, Any]:
    return {
        key: getattr(usage, key)
        for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
        if hasattr(usage, key)
    }
