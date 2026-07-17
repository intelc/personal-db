"""Deterministic receipt evidence and signal extraction."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from personal_db.enrichments.finance.constants import (
    DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    _GENERIC_MERCHANT_TOKENS,
)
from personal_db.enrichments.finance.transactions import FinanceTransaction

def _candidate_signal_score(candidate: dict[str, Any]) -> int:
    signals = candidate.get("signals") or {}
    return (
        (4 if signals.get("amount") else 0)
        + (3 if signals.get("date") else 0)
        + (2 if signals.get("merchant") else 0)
        + (1 if signals.get("receipt_language") else 0)
    )


def _candidate_has_complete_receipt_signals(candidate: dict[str, Any]) -> bool:
    signals = candidate.get("signals") or {}
    return bool(signals.get("amount") and signals.get("date") and signals.get("merchant"))


def _find_amount_combination(
    transaction: dict[str, Any],
    candidates: list[dict[str, Any]],
    *,
    max_items: int = 5,
) -> dict[str, Any] | None:
    target = _decimal_amount(transaction.get("amount"))
    if target is None:
        return None
    eligible = []
    for candidate in candidates:
        signals = candidate.get("signals") or {}
        if not (signals.get("date") and signals.get("merchant")):
            continue
        primary = candidate.get("primary_amount") or {}
        value = _decimal_amount(primary.get("value") if isinstance(primary, dict) else None)
        if value is None or value <= 0 or value >= target:
            continue
        eligible.append(
            {
                "message_id": candidate.get("message_id"),
                "value": value,
                "matched": primary.get("matched") if isinstance(primary, dict) else None,
                "snippet": primary.get("snippet") if isinstance(primary, dict) else None,
            }
        )
    # Keep the search small and deterministic: closest/largest charges first tends
    # to find ride-share daily batches without exploring noisy marketing amounts.
    eligible = sorted(eligible, key=lambda item: item["value"], reverse=True)[:12]
    combo = _find_decimal_subset(eligible, target, max_items=max_items)
    if not combo:
        return None
    total = sum((item["value"] for item in combo), Decimal("0.00"))
    return {
        "target": f"{target:.2f}",
        "total": f"{total:.2f}",
        "message_ids": [str(item["message_id"]) for item in combo],
        "components": [
            {
                "message_id": str(item["message_id"]),
                "value": f"{item['value']:.2f}",
                "matched": item.get("matched"),
                "snippet": item.get("snippet"),
            }
            for item in combo
        ],
    }


def _find_decimal_subset(
    items: list[dict[str, Any]],
    target: Decimal,
    *,
    max_items: int,
) -> list[dict[str, Any]] | None:
    cents_target = int((target * 100).to_integral_value())
    cents = [int((item["value"] * 100).to_integral_value()) for item in items]

    def search(start: int, remaining: int, chosen: list[int]) -> list[int] | None:
        if remaining == 0 and len(chosen) >= 2:
            return chosen
        if remaining <= 0 or len(chosen) >= max_items:
            return None
        for i in range(start, len(items)):
            found = search(i + 1, remaining - cents[i], [*chosen, i])
            if found is not None:
                return found
        return None

    indexes = search(0, cents_target, [])
    if indexes is None:
        return None
    return [items[i] for i in indexes]


def _extract_currency_amounts(text: str, *, window_chars: int) -> list[dict[str, Any]]:
    values = []
    seen: set[tuple[str, str]] = set()
    money_re = re.compile(
        r"(?<![A-Za-z0-9])(?:(?:USD\s*)?\$\s*(\d{1,6}(?:,\d{3})*\.\d{2})|USD\s+(\d{1,6}(?:,\d{3})*\.\d{2}))(?![A-Za-z0-9])",
        flags=re.IGNORECASE,
    )
    for match in money_re.finditer(text):
        matched = match.group(0).strip()
        # Avoid bare decimals buried inside URLs/encoded tracking payloads. Receipt
        # text usually has whitespace or punctuation around the amount.
        context = text[max(0, match.start() - 12) : min(len(text), match.end() + 12)]
        if "%" in context and "$" not in matched:
            continue
        value = _decimal_amount(match.group(1) or match.group(2))
        if value is None:
            continue
        key = (f"{value:.2f}", matched)
        if key in seen:
            continue
        seen.add(key)
        values.append(
            {
                "value": f"{value:.2f}",
                "matched": matched,
                "snippet": _evidence_snippet(
                    text,
                    match.start(),
                    match.end(),
                    window_chars=window_chars,
                ),
            }
        )
        if len(values) >= 24:
            break
    return values


def _decimal_amount(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).replace("$", "").replace(",", "")).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def extract_receipt_evidence_windows(
    tx: FinanceTransaction,
    message_id: str,
    text: str,
    *,
    window_chars: int = DEFAULT_RECEIPT_SNIPPET_WINDOW_CHARS,
    extra_merchant_tokens: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Extract deterministic receipt evidence snippets from one email thread.

    `extra_merchant_tokens` supplements the static `_GENERIC_MERCHANT_TOKENS`
    exclusion set with user-specific tokens (config.yaml `user.name_tokens`,
    see Config.user_name_tokens) — e.g. the account holder's own name, which
    would otherwise show up as a spurious "merchant" term match in every
    email that happens to mention them.
    """
    signal_terms = {
        "amount": _amount_terms(tx.amount),
        "date": _date_terms(tx.date),
        "merchant": _merchant_terms(
            tx.merchant_hint, tx.name, extra_excluded_tokens=extra_merchant_tokens
        ),
        "receipt_language": ["receipt", "total", "charged", "payment", "order", "invoice"],
    }
    snippets = []
    matched_terms: dict[str, list[str]] = {}
    seen_snippets: set[tuple[str, str, str]] = set()
    for signal, terms in signal_terms.items():
        for term in terms:
            for match in re.finditer(re.escape(term), text, flags=re.IGNORECASE):
                snippet = _evidence_snippet(
                    text,
                    match.start(),
                    match.end(),
                    window_chars=window_chars,
                )
                key = (signal, term.lower(), snippet)
                if key in seen_snippets:
                    continue
                seen_snippets.add(key)
                matched_terms.setdefault(signal, [])
                if term not in matched_terms[signal]:
                    matched_terms[signal].append(term)
                snippets.append(
                    {
                        "signal": signal,
                        "matched": text[match.start() : match.end()],
                        "snippet": snippet,
                    }
                )
                if len(snippets) >= 16:
                    break
            if len(snippets) >= 16:
                break
        if len(snippets) >= 16:
            break

    amount_values = _extract_currency_amounts(text, window_chars=window_chars)
    signals = {signal: bool(matched_terms.get(signal)) for signal in signal_terms}
    return {
        "message_id": str(message_id),
        "source_ref": f"spark_email:message:{message_id}",
        "signals": signals,
        "matched_terms": matched_terms,
        "amount_values": amount_values,
        "primary_amount": amount_values[0] if amount_values else None,
        "snippet_count": len(snippets),
        "snippets": snippets,
    }


def _amount_terms(amount: float | None) -> list[str]:
    if amount is None:
        return []
    value = abs(float(amount))
    fixed = f"{value:.2f}"
    terms = [fixed, f"${fixed}", f"USD {fixed}", f"US${fixed}"]
    if fixed.endswith("0"):
        terms.append(fixed.rstrip("0").rstrip("."))
    if value >= 1000:
        comma = f"{value:,.2f}"
        terms.extend([comma, f"${comma}", f"USD {comma}"])
    return _unique_nonempty(terms)


def _date_terms(date_value: str | None) -> list[str]:
    if not date_value:
        return []
    terms = [date_value]
    try:
        dt = datetime.fromisoformat(date_value.replace("Z", "+00:00"))
    except ValueError:
        return _unique_nonempty(terms)
    month = dt.strftime("%B")
    month_abbr = dt.strftime("%b")
    terms.extend(
        [
            dt.strftime("%m/%d/%Y"),
            f"{dt.month}/{dt.day}/{dt.year}",
            f"{month} {dt.day}, {dt.year}",
            f"{month} {dt.day}",
            f"{month_abbr} {dt.day}, {dt.year}",
            f"{month_abbr} {dt.day}",
        ]
    )
    return _unique_nonempty(terms)


def _merchant_terms(
    merchant: str | None,
    transaction_name: str | None,
    *,
    extra_excluded_tokens: frozenset[str] = frozenset(),
) -> list[str]:
    excluded_tokens = _GENERIC_MERCHANT_TOKENS | extra_excluded_tokens
    values = [merchant] if merchant else [transaction_name]
    terms: list[str] = []
    for value in values:
        if not value:
            continue
        compact = re.sub(r"\s+", " ", value).strip()
        if compact:
            terms.append(compact)
        for token in re.findall(r"[A-Za-z0-9]{3,}", value):
            lowered = token.lower()
            if lowered not in excluded_tokens:
                terms.append(token)
    return _unique_nonempty(terms)


def _evidence_snippet(text: str, start: int, end: int, *, window_chars: int) -> str:
    paragraph_start = text.rfind("\n\n", 0, start)
    paragraph_end = text.find("\n\n", end)
    if paragraph_start == -1:
        paragraph_start = 0
    else:
        paragraph_start += 2
    if paragraph_end == -1:
        paragraph_end = len(text)
    paragraph = text[paragraph_start:paragraph_end].strip()
    max_paragraph_chars = max(200, int(window_chars) * 2)
    if paragraph and len(paragraph) <= max_paragraph_chars:
        return _clean_receipt_snippet(paragraph)

    radius = max(50, int(window_chars))
    snippet_start = max(0, start - radius)
    snippet_end = min(len(text), end + radius)
    prefix = "..." if snippet_start > 0 else ""
    suffix = "..." if snippet_end < len(text) else ""
    return prefix + _clean_receipt_snippet(text[snippet_start:snippet_end]) + suffix


def _clean_receipt_snippet(text: str) -> str:
    without_markdown_urls = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    without_bare_urls = re.sub(r"https?://\S+", "", without_markdown_urls)
    without_encoded_noise = re.sub(
        r"\S*(?:%[0-9A-Fa-f]{2}\S*){3,}",
        "",
        without_bare_urls,
    )
    without_tracking_tail = re.sub(
        r"\S*(?:safelinks|reserved=0|data=)\S*",
        "",
        without_encoded_noise,
        flags=re.IGNORECASE,
    )
    return _compact_whitespace(without_tracking_tail)


def _compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        normalized = value.strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            out.append(normalized)
    return out
