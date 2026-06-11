from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from personal_db.config import Config
from personal_db.context_providers.base import ContextResult, EvidenceRef
from personal_db.remote_sources.spark import SparkEmailSource


@dataclass(frozen=True)
class ReceiptSearchQuery:
    merchant: str | None = None
    amount: str | None = None
    date: str | None = None
    window_days: int = 7
    scope: str | None = None


class SparkEmailContextProvider:
    """Semantic email context provider backed by the installed Spark source."""

    name = "email"

    def __init__(self, source: SparkEmailSource) -> None:
        self.source = source

    @classmethod
    def from_config(cls, cfg: Config) -> SparkEmailContextProvider:
        return cls(SparkEmailSource.from_config(cfg, require_installed=True))

    def search_receipts(
        self,
        *,
        merchant: str | None = None,
        amount: str | float | Decimal | None = None,
        date_: str | None = None,
        window_days: int = 7,
        scope: str | None = None,
    ) -> ContextResult:
        query = ReceiptSearchQuery(
            merchant=_clean_optional(merchant),
            amount=_format_amount(amount),
            date=date_,
            window_days=max(0, int(window_days)),
            scope=_clean_optional(scope),
        )
        strategies = _receipt_search_strategies(query)
        filter_ = _receipt_filter(query)
        remote_results = [
            {
                "name": name,
                "about": about,
                "remote": self.source.search(about, filter_=filter_, in_=query.scope),
            }
            for name, about in strategies
        ]
        email_ids = list(
            dict.fromkeys(
                message_id
                for item in remote_results
                for message_id in (item["remote"].data.get("email_ids") or [])
            )
        )
        evidence = [
            EvidenceRef(
                source="spark_email",
                ref=f"spark_email:message:{message_id}",
                kind="email_message",
                title=f"Spark email message {message_id}",
            )
            for message_id in email_ids
        ]
        return ContextResult(
            provider=self.name,
            operation="search_receipts",
            query={
                "merchant": query.merchant,
                "amount": query.amount,
                "date": query.date,
                "window_days": query.window_days,
                "scope": query.scope,
                "about": strategies[0][1],
                "filter": filter_,
                "strategies": [
                    {
                        "name": item["name"],
                        "about": item["about"],
                        "email_ids": list(item["remote"].data.get("email_ids") or []),
                        "page": item["remote"].data.get("page"),
                    }
                    for item in remote_results
                ],
            },
            evidence=evidence,
            data={
                "source": remote_results[0]["remote"].source,
                "operation": "search",
                "email_ids": email_ids,
                "page": remote_results[0]["remote"].data.get("page"),
                "searches": [
                    {
                        "name": item["name"],
                        "about": item["about"],
                        "email_ids": list(item["remote"].data.get("email_ids") or []),
                        "page": item["remote"].data.get("page"),
                    }
                    for item in remote_results
                ],
            },
            raw_text="\n\n".join(
                f"=== {item['name']}: {item['about']} ===\n{item['remote'].raw_text}"
                for item in remote_results
            ),
        )

    def read_thread(self, message_id: str, *, download_attachments: bool = False) -> ContextResult:
        remote = self.source.thread(message_id, download_attachments=download_attachments)
        evidence = [
            EvidenceRef(
                source="spark_email",
                ref=f"spark_email:message:{message_id}",
                kind="email_thread",
                title=f"Spark email thread {message_id}",
                excerpt=_excerpt(remote.raw_text),
            )
        ]
        return ContextResult(
            provider=self.name,
            operation="read_thread",
            query={
                "message_id": str(message_id),
                "download_attachments": download_attachments,
            },
            evidence=evidence,
            data=remote.data,
            raw_text=remote.raw_text,
        )


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def _format_amount(value: str | float | Decimal | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().replace("$", "").replace(",", "")
        if not value:
            return None
    try:
        return f"{Decimal(str(value)):.2f}"
    except (InvalidOperation, ValueError):
        return str(value)


def _receipt_about(query: ReceiptSearchQuery) -> str:
    parts = ["receipt", "invoice", "order", "confirmation"]
    if query.merchant:
        parts.insert(0, query.merchant)
    if query.amount:
        parts.append(query.amount)
    return " ".join(parts)


def _receipt_search_strategies(query: ReceiptSearchQuery) -> list[tuple[str, str]]:
    strategies = []
    if query.amount and query.date:
        strategies.append(("amount_date", query.amount))
    strategies.append(("receipt_semantic", _receipt_about(query)))
    return list(dict.fromkeys(strategies))


def _receipt_filter(query: ReceiptSearchQuery) -> str | None:
    filters = []
    if query.date:
        base = date.fromisoformat(query.date)
        start = base - timedelta(days=query.window_days)
        end = base + timedelta(days=query.window_days + 1)
        filters.append(f"after:{start:%Y/%m/%d}")
        filters.append(f"before:{end:%Y/%m/%d}")
    return " ".join(filters) or None


def _excerpt(raw: str, limit: int = 240) -> str | None:
    compact = " ".join(raw.split())
    if not compact:
        return None
    return compact[:limit]
