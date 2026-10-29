"""DB3 base value rules, ported from experiment 30 without runtime imports.

Later DB3 repairs are applied offline by the indexer. Keep raw fields intact.
"""
from __future__ import annotations
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

FIELDS = ("target", "action", "benefit_type", "value", "unit", "condition", "cap", "period", "frequency", "exceptions")
NORMALIZATION_CONTRACT = "db3-normalization30-56-60-v1"
QUERY_CONTRACT = "db3-query60-v1"

TARGET_ALIASES: dict[str, list[str]] = {
    "여행": ["여행", "항공", "항공사", "호텔", "숙박", "공항", "라운지", "면세점"],
    "커피": ["커피", "카페", "스타벅스", "투썸", "이디야", "폴바셋", "커피빈"],
    "카페": ["커피", "카페", "스타벅스", "투썸", "이디야", "폴바셋", "커피빈"],
}

_MONEY_TOKEN = re.compile(r"(?P<number>\d[\d,]*(?:\.\d+)?)\s*(?P<unit>천|만)?\s*원")
_PERCENT_TOKEN = re.compile(r"(?P<number>\d+(?:\.\d+)?)\s*%")
_SPEND_PHRASE = re.compile(r"전월\s*(?:이용\s*)?(?:실적|이용금액|이용\s*실적)")


def _canonical(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _money_from_match(match: re.Match[str]) -> int | None:
    try:
        number = Decimal(match.group("number").replace(",", ""))
    except InvalidOperation:
        return None
    multiplier = {None: 1, "천": 1_000, "만": 10_000}[match.group("unit")]
    amount = number * multiplier
    if amount != amount.to_integral_value():
        return None
    return int(amount)


def _typed_decimals(typed: dict[str, Any], field: str, kind: str) -> list[Decimal]:
    values: list[Decimal] = []
    for entry in typed.get(field, []) or []:
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            continue
        try:
            values.append(Decimal(str(entry["decimal"])))
        except (InvalidOperation, KeyError, TypeError):
            continue
    return values


def _derive_percent(fact: dict[str, Any]) -> tuple[float | None, str, str]:
    typed = _typed_decimals(fact.get("typed_normalization") or {}, "value", "ratio")
    raw_text = f"{fact.get('value', '')} {fact.get('unit', '')}"
    raw = [Decimal(match.group("number")) for match in _PERCENT_TOKEN.finditer(raw_text)]
    has_numeric_range = bool(re.search(r"\d\s*(?:~|∼|～|–|—|-)\s*\d", raw_text))
    has_bound = bool(re.search(r"최대|최소|이상|이하|초과|미만|까지", raw_text))
    has_mixed_money_percent = bool(_MONEY_TOKEN.search(raw_text) and _PERCENT_TOKEN.search(raw_text))
    if has_numeric_range or has_bound or has_mixed_money_percent:
        return None, "unknown", "raw benefit is a range, bound, or mixed money/percent expression"
    if len(typed) != 1:
        state = "unknown" if typed or fact.get("value") else "not_applicable"
        return None, state, "typed ratio count is not one"
    percent = typed[0] * Decimal(100)
    if len(raw) != 1 or raw[0] != percent:
        return None, "unknown", "typed ratio and raw percent do not uniquely agree"
    return float(percent), "known", "single typed ratio agrees with raw percent"


def _derive_spend_threshold(fact: dict[str, Any]) -> tuple[int | None, str | None, str, str]:
    condition = str(fact.get("condition") or "")
    phrases = list(_SPEND_PHRASE.finditer(condition))
    typed_krw = _typed_decimals(fact.get("typed_normalization") or {}, "condition", "KRW")
    if not phrases:
        state = "unknown" if typed_krw and condition else "not_applicable"
        return None, None, state, "no explicit previous-month spend phrase"

    connected: list[tuple[int, str]] = []
    for phrase in phrases:
        # Thresholds normally appear immediately after the phrase.  A limited
        # backward window handles table-derived text such as "30만원 이상 전월 실적".
        windows = [condition[phrase.end() : phrase.end() + 45], condition[max(0, phrase.start() - 30) : phrase.start()]]
        for window in windows:
            for match in _MONEY_TOKEN.finditer(window):
                amount = _money_from_match(match)
                if amount is None:
                    continue
                tail = window[match.end() : match.end() + 8]
                operator = "gte" if re.search(r"이상", tail) else "lte" if re.search(r"이하", tail) else "unknown"
                connected.append((amount, operator))

    distinct = sorted(set(connected))
    if len(distinct) != 1:
        return None, None, "unknown", "explicit spend phrase has zero or multiple connected thresholds"
    amount, operator = distinct[0]
    if operator != "gte":
        return None, operator, "unknown", "only an explicit minimum spend threshold is supported"
    if Decimal(amount) not in typed_krw:
        return None, operator, "unknown", "connected raw threshold does not agree with typed KRW"
    return amount, operator, "known", "single explicit previous-month threshold agrees with typed KRW"


def _derive_cap(fact: dict[str, Any]) -> tuple[int | None, str, str]:
    cap_text = str(fact.get("cap") or "")
    typed = _typed_decimals(fact.get("typed_normalization") or {}, "cap", "KRW")
    raw = [_money_from_match(match) for match in _MONEY_TOKEN.finditer(cap_text)]
    raw = [value for value in raw if value is not None]
    spend_ceiling = bool(
        re.search(
            r"이용\s*금액|이용액|결제\s*금액|결제액|승인\s*금액|매출\s*(?:금액|액|건당)",
            cap_text,
        )
    )
    if spend_ceiling:
        return None, "unknown", "cap text describes an eligible spend or transaction ceiling"
    if len(typed) != 1:
        state = "unknown" if typed or cap_text else "not_applicable"
        return None, state, "typed cap count is not one"
    if len(raw) != 1 or Decimal(raw[0]) != typed[0]:
        return None, "unknown", "typed cap and raw KRW do not uniquely agree"
    return int(typed[0]), "known", "single typed cap agrees with raw KRW"


def _derive_cap_period(fact: dict[str, Any]) -> tuple[str | None, str, str]:
    cap_text = str(fact.get("cap") or "")
    period_text = str(fact.get("period") or "")
    text = _canonical(f"{cap_text} {period_text}")
    month = any(
        token in text
        for token in ("매월", "월간", "월별", "당월", "해당월", "월한도", "월최대", "월통합")
    ) or bool(re.search(r"(?:^|[^가-힣])월\d", text))
    day = any(token in text for token in ("하루", "일일", "1일최대", "1일한도", "1일1회")) or bool(
        re.search(r"(?:^|[^가-힣])일(?:최대|한도|\d)", text)
    )
    if month and day:
        return None, "unknown", "both monthly and daily cap periods are explicit"
    if month:
        return "month", "known", "explicit monthly cap period"
    if day:
        return "day", "known", "explicit daily cap period"
    if cap_text or period_text:
        return None, "unknown", "cap period is not explicit"
    return None, "not_applicable", "cap and period are absent"


def _derive_action(fact: dict[str, Any]) -> tuple[str | None, str, str]:
    text = _canonical(f"{fact.get('action', '')} {fact.get('benefit_type', '')}")
    negative = ("제외" in text or "제공되지않" in text or "적용되지않" in text or "불가" in text)
    kinds = []
    if "할인" in text:
        kinds.append("discount")
    if any(token in text for token in ("적립", "포인트", "마일리지", "airmoney", "에어머니")):
        kinds.append("earn")
    if negative:
        return (kinds[0] if len(kinds) == 1 else None), "false", "action text is negated or excluded"
    if len(kinds) == 1:
        return kinds[0], "true", "single supported positive action"
    if len(kinds) > 1:
        return None, "unknown", "multiple action kinds"
    return None, "unknown", "unsupported or ambiguous action"


def _derive_fact(fact: dict[str, Any]) -> dict[str, Any]:
    percent, percent_state, percent_reason = _derive_percent(fact)
    spend, spend_operator, spend_state, spend_reason = _derive_spend_threshold(fact)
    cap, cap_state, cap_reason = _derive_cap(fact)
    cap_period, cap_period_state, cap_period_reason = _derive_cap_period(fact)
    action_kind, action_status, action_reason = _derive_action(fact)
    exceptions = str(fact.get("exceptions") or "")
    return {
        "target_normalized": _canonical(str(fact.get("target") or "")),
        "action_kind": action_kind,
        "action_status": action_status,
        "action_reason": action_reason,
        "percent": percent,
        "percent_state": percent_state,
        "percent_reason": percent_reason,
        "spend_threshold_krw": spend,
        "spend_operator": spend_operator,
        "spend_state": spend_state,
        "spend_reason": spend_reason,
        "cap_krw": cap,
        "cap_state": cap_state,
        "cap_reason": cap_reason,
        "cap_period": cap_period,
        "cap_period_state": cap_period_state,
        "cap_period_reason": cap_period_reason,
        "period_raw": str(fact.get("period") or ""),
        "has_exceptions": bool(exceptions.strip()),
        "exceptions_normalized": _canonical(exceptions),
    }


def _fact_id(provider: str, source_pdf_sha256: str, document_id: str, ordinal: int) -> str:
    fingerprint = f"{provider}|{source_pdf_sha256}|{document_id}|{ordinal}".encode("utf-8")
    return f"fact_{_sha256_bytes(fingerprint)[:24]}"


def _expanded_targets(targets: list[str]) -> list[str]:
    expanded: list[str] = []
    for target in targets:
        normalized = _canonical(target)
        aliases = TARGET_ALIASES.get(normalized, [target])
        expanded.extend(_canonical(alias) for alias in aliases)
    return sorted(set(item for item in expanded if item))


@dataclass(frozen=True)
class _FactEvaluation:
    status: str
    fact_id: str | None = None


def _evaluate_fact(row: dict[str, Any], requirement: dict[str, Any]) -> _FactEvaluation:
    fields = row["fields"]
    derived = row["derived"]
    target = derived.get("target_normalized") or _canonical(str(fields.get("target") or ""))
    wanted = _expanded_targets(list(requirement.get("targets") or []))
    excluded = _expanded_targets(list(requirement.get("exclude_targets") or []))
    if wanted and not any(alias in target for alias in wanted):
        return _FactEvaluation("false")
    if excluded and any(alias in target for alias in excluded):
        return _FactEvaluation("false")

    requested_action = requirement.get("action", "any")
    action_status = derived.get("action_status", "unknown")
    if action_status == "false":
        return _FactEvaluation("false")
    if requested_action != "any" and derived.get("action_kind") != requested_action:
        if derived.get("action_kind") is None:
            return _FactEvaluation("unknown")
        return _FactEvaluation("false")
    uncertain = action_status != "true"

    # A target mentioned as an exception cannot be promoted to a positive match.
    exception_text = derived.get("exceptions_normalized", "")
    if wanted and exception_text and any(alias in exception_text for alias in wanted):
        uncertain = True

    min_percent = requirement.get("min_percent")
    if min_percent is not None:
        if derived.get("percent_state") != "known":
            uncertain = True
        elif float(derived["percent"]) < float(min_percent):
            return _FactEvaluation("false")

    max_spend = requirement.get("max_spend_krw")
    if max_spend is not None:
        if derived.get("spend_state") != "known":
            uncertain = True
        elif int(derived["spend_threshold_krw"]) > int(max_spend):
            return _FactEvaluation("false")

    min_cap = requirement.get("min_cap_krw")
    if min_cap is not None:
        if derived.get("cap_state") != "known":
            uncertain = True
        elif int(derived["cap_krw"]) < int(min_cap):
            return _FactEvaluation("false")
        requested_period = requirement.get("cap_period", "any")
        if requested_period not in {"any", "month", "day"}:
            raise ValueError("unsupported cap_period")
        if requested_period != "any":
            if derived.get("cap_period_state") != "known":
                uncertain = True
            elif derived.get("cap_period") != requested_period:
                return _FactEvaluation("false")
    return _FactEvaluation("unknown" if uncertain else "true", row["fact_id"] if not uncertain else None)
