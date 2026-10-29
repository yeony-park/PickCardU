"""Pure DB3 repairs ported from experiments 56 and 60; no DB5 changes."""
from __future__ import annotations
import copy
import re
from typing import Any, Mapping
from pickcardu_rag.structured_values import _derive_fact

_NO_SPEND56 = re.compile(r"전월\s*(?:이용\s*금액|실적)\s*조건\s*없음")
_LINE_ID = re.compile(r"P(?P<page>\d{4})-L(?P<line>\d{4})")




def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _evidence_line_ids(raw_fact: Mapping[str, Any], field: str) -> set[str]:
    evidence = (raw_fact.get("field_evidence") or {}).get(field) or []
    return {str(item.get("line_id")) for item in evidence if isinstance(item, Mapping) and item.get("line_id")}


def _nearby_same_page(left: set[str], right: set[str], distance: int = 2) -> bool:
    for a in left:
        ma = _LINE_ID.fullmatch(a)
        if not ma:
            continue
        for b in right:
            mb = _LINE_ID.fullmatch(b)
            if mb and ma.group("page") == mb.group("page") and abs(int(ma.group("line")) - int(mb.group("line"))) <= distance:
                return True
    return False


def _non_krw_accrual_transaction_ceiling(raw_fact: Mapping[str, Any]) -> bool:
    """Recognize a per-transaction KRW base for non-KRW point accrual.

    The bare phrase ``1회 10만원 한도`` is intentionally insufficient.  The
    benefit must accrue points per fuel unit, and the evidence must bind the
    point value and KRW ceiling within one bounded source block.
    """

    action = _compact(raw_fact.get("action"))
    unit = _compact(raw_fact.get("unit"))
    cap = _compact(raw_fact.get("cap"))
    context = _compact(f"{raw_fact.get('target', '')} {raw_fact.get('benefit_type', '')} {raw_fact.get('condition', '')}")
    scope = raw_fact.get("relation_scope") or {}
    if not (
        "적립" in action
        and any(token in unit for token in ("포인트", "마일리지"))
        and re.fullmatch(r"1회\d[\d,.]*(?:천|만)?원한도", cap)
        and any(token in context for token in ("주유", "리터"))
        and scope.get("scope_type") == "bounded_block"
    ):
        return False
    scope_lines = {str(value) for value in scope.get("line_ids") or []}
    cap_lines = _evidence_line_ids(raw_fact, "cap")
    value_lines = _evidence_line_ids(raw_fact, "value")
    condition_lines = _evidence_line_ids(raw_fact, "condition")
    benefit_lines = set().union(
        *(_evidence_line_ids(raw_fact, field) for field in ("target", "action", "value", "unit", "condition"))
    )
    source_lines = raw_fact.get("_source_line_text_by_id") or {}
    per_liter_lines = {
        str(line_id) for line_id, text in source_lines.items() if "리터당" in _compact(text)
    }
    for field in ("target", "action", "value", "unit", "condition"):
        for item in (raw_fact.get("field_evidence") or {}).get(field) or []:
            if isinstance(item, Mapping) and "리터당" in _compact(item.get("fragment")):
                per_liter_lines.add(str(item.get("line_id")))
    return bool(
        cap_lines
        and value_lines
        and condition_lines
        and per_liter_lines
        and cap_lines | value_lines | condition_lines | per_liter_lines <= scope_lines
        and per_liter_lines & benefit_lines
        and _nearby_same_page(cap_lines, value_lines, distance=2)
        and _nearby_same_page(cap_lines, condition_lines, distance=2)
        and _nearby_same_page(cap_lines, per_liter_lines, distance=2)
    )


def _cap_semantics_rule(raw_fact: Mapping[str, Any]) -> str | None:
    cap = _compact(raw_fact.get("cap"))
    benefit = _compact(raw_fact.get("benefit_type"))
    action = _compact(raw_fact.get("action"))
    target = _compact(raw_fact.get("target"))

    if re.fullmatch(r"전월(?:이용금액|실적)\d[\d,.]*(?:천|만)?원이상에해당하는할인한도", cap):
        return "previous_spend_tier_is_not_payout_cap"
    if "이용금액한도" in benefit:
        return "eligible_use_amount_is_not_payout_cap"
    if re.search(r"(?:캐시백|할인|적립)대상(?:으로)?인정", cap):
        return "recognized_purchase_amount_is_not_payout_cap"
    if (
        "이용금액" in target
        and ("적립대상" in benefit or "적립대상" in action)
        and re.fullmatch(r"합산최대월\d[\d,.]*(?:천|만)?원까지", cap)
    ):
        return "recognized_purchase_amount_is_not_payout_cap"
    if re.search(r"(?:회당|1회)(?:이용|주유|결제)금액\d[\d,.]*(?:천|만)?원한도", cap):
        return "per_transaction_spend_is_not_payout_cap"
    if _non_krw_accrual_transaction_ceiling(raw_fact):
        return "non_krw_accrual_transaction_ceiling"
    return None


def _assign_if_changed(result: dict[str, Any], updates: Mapping[str, Any]) -> bool:
    changed = any(result.get(key) != value for key, value in updates.items())
    if changed:
        result.update(updates)
    return changed


def _normalize56(
    raw_fact: Mapping[str, Any], current_derived: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return a non-mutating, deterministic derived-field normalization.

    Missing or vague language remains unknown.  Rules use semantic field
    patterns and evidence bindings; card and fact identifiers are never read.
    """

    result = copy.deepcopy(dict(current_derived))
    rules: list[str] = []
    condition = str(raw_fact.get("condition") or "").strip()
    if _NO_SPEND56.fullmatch(condition):
        if _assign_if_changed(
            result,
            {
                "spend_threshold_krw": 0,
                "spend_operator": "gte",
                "spend_state": "known",
                "spend_reason": "explicit statement that no previous-month performance is required",
            },
        ):
            rules.append("explicit_no_previous_month_requirement")

    action_text = _compact(raw_fact.get("action"))
    benefit_text = _compact(raw_fact.get("benefit_type"))
    combined_action = action_text + benefit_text
    if (
        "바우처" in benefit_text
        and any(token in action_text for token in ("교환", "전환"))
        and not any(token in combined_action for token in ("적립", "캐시백"))
        and (result.get("action_kind") is not None or result.get("action_status") != "unknown")
    ):
        if _assign_if_changed(
            result,
            {
                "action_kind": None,
                "action_status": "unknown",
                "action_reason": "voucher exchange is not a supported purchase accrual action",
            },
        ):
            rules.append("exchange_voucher_is_not_accrual")

    cap_rule = _cap_semantics_rule(raw_fact)
    if cap_rule and result.get("cap_state") == "known" and _assign_if_changed(
        result,
        {
            "cap_krw": None,
            "cap_state": "unknown",
            "cap_reason": "amount is an eligible purchase, performance tier, transaction, or intermediate calculation ceiling; not a benefit payout cap",
            "cap_period": None,
            "cap_period_state": "unknown",
            "cap_period_reason": "period belongs to a non-payout amount ceiling",
        },
    ):
        rules.append(cap_rule)
    return result, tuple(rules)


_NO_SPEND = re.compile(
    r"전월(?:이용금액|실적)조건(?:및할인한도)?(?:없음|제한없이)"
)
_PREVIOUS_SPEND_THRESHOLD = re.compile(
    r"전월(?:이용금액|실적)[^\d]{0,12}\d[\d,.]*(?:만|천)?원이상"
)
_TEMPORAL_NO_SPEND = ("신규발급", "발급월", "등록월", "다음달말")
_FREE_POSITIVE = ("무료이용", "무료입장")
_FREE_NEGATIVE = (
    "무료이용불가",
    "무료입장불가",
    "제공하지않",
    "제공되지않",
    "유료",
)
_FREE_EXCLUDED_KINDS = ("바우처", "무료교환", "무료가입", "무료숙박권")
_GENERIC_POSITIVE_ACTIONS = {
    "제공",
    "제공됩니다",
    "제공되며",
    "이용",
    "입장",
    "서비스이용",
    "무료이용서비스제공",
}
def _evidence_lines(raw_fact: Mapping[str, Any], field: str) -> set[str]:
    return {
        str(item["line_id"])
        for item in (raw_fact.get("field_evidence") or {}).get(field) or []
        if isinstance(item, Mapping) and item.get("line_id")
    }


def _scope_lines(raw_fact: Mapping[str, Any]) -> set[str]:
    scope = raw_fact.get("relation_scope") or {}
    return {
        str(value)
        for key in ("line_ids", "row_line_ids")
        for value in scope.get(key) or []
    }


def _bounded_relation(raw_fact: Mapping[str, Any], fields: list[str]) -> bool:
    scope = raw_fact.get("relation_scope") or {}
    if scope.get("scope_type") not in {"bounded_block", "table_row"}:
        return False
    field_lines = [_evidence_lines(raw_fact, field) for field in fields]
    return bool(field_lines) and all(field_lines) and set().union(*field_lines) <= _scope_lines(raw_fact)


def _no_spend_match(raw_fact: Mapping[str, Any]) -> tuple[bool, str]:
    condition = _compact(raw_fact.get("condition"))
    if not _NO_SPEND.search(condition):
        return False, "no supported explicit no-spend phrase"
    if _PREVIOUS_SPEND_THRESHOLD.search(condition):
        return False, "same condition also contains an explicit previous-spend threshold"
    if "추가혜택" in condition:
        return False, "no-spend phrase is mixed with a separately described additional benefit"
    if any(token in condition for token in _TEMPORAL_NO_SPEND):
        return False, "no-spend phrase is limited to a new-card or temporary period"
    if not _bounded_relation(raw_fact, ["target", "action", "condition"]):
        return False, "target, action, and condition evidence are not bound to the same bounded benefit scope"
    return True, "explicit compound statement says no previous-spend condition"


def _free_match(raw_fact: Mapping[str, Any]) -> tuple[bool, str, list[str]]:
    action = _compact(raw_fact.get("action"))
    benefit = _compact(raw_fact.get("benefit_type"))
    value = _compact(raw_fact.get("value"))
    target = _compact(raw_fact.get("target"))
    combined = action + benefit + value
    if any(token in combined for token in _FREE_NEGATIVE):
        return False, "negative or paid wording prevents a positive free-use classification", []
    if any(token in combined + target for token in _FREE_EXCLUDED_KINDS):
        return False, "voucher, exchange, enrollment, or free-stay wording is outside free-use scope", []
    direct = any(token in action for token in _FREE_POSITIVE)
    generic = action in _GENERIC_POSITIVE_ACTIONS and (
        "무료이용" in benefit or "무료이용" in value
    )
    if not (direct or generic):
        return False, "no explicit positive free-use or free-entry action", []
    fields = ["target", "action"]
    for field, compact_value in (("benefit_type", benefit), ("value", value)):
        if compact_value:
            fields.append(field)
    if not _bounded_relation(raw_fact, fields):
        return False, "positive free-use wording is not bound to one bounded benefit scope", fields
    return True, "explicit positive free-use or free-entry benefit", fields




def _normalize60(
    raw_fact: Mapping[str, Any], current_derived: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return a non-mutating conservative stage-60 derived normalization."""

    result = copy.deepcopy(dict(current_derived))
    rules: list[str] = []
    no_spend, _ = _no_spend_match(raw_fact)
    if result.get("spend_state") != "known" and no_spend:
        if _assign_if_changed(
            result,
            {
                "spend_threshold_krw": 0,
                "spend_operator": "gte",
                "spend_state": "known",
                "spend_reason": "explicit compound statement that no previous-month spend is required",
            },
        ):
            rules.append("explicit_compound_no_spend")

    free_positive, _, _ = _free_match(raw_fact)
    if result.get("action_status") == "unknown" and free_positive:
        if _assign_if_changed(
            result,
            {
                "action_kind": None,
                "action_status": "true",
                "action_reason": "explicit positive free-use or free-entry service",
            },
        ):
            rules.append("explicit_free_use_positive")
    return result, tuple(rules)



def normalize_fact(raw_fact: dict, pages: list[dict]) -> dict:
    lines = {f"P{int(p['page']):04d}-L{i:04d}": text for p in pages
             for i, text in enumerate(str(p.get('text') or '').splitlines(), 1)}
    evidence_ids = {e['line_id'] for refs in (raw_fact.get('field_evidence') or {}).values()
                    for e in refs or [] if isinstance(e, dict) and e.get('line_id')}
    enriched = {**raw_fact, '_source_line_text_by_id': {k: lines[k] for k in evidence_ids if k in lines}}
    v2, _ = _normalize56(enriched, _derive_fact(raw_fact))
    return _normalize60(raw_fact, v2)[0]
