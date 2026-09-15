"""Versioned source-grounding primitives for structured card benefits.

This module intentionally has no provider, filesystem, or model dependency.  It
keeps raw strings for audit while producing small typed comparison keys locally.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import json
import re
import unicodedata
from typing import Any


STRUCTURE_SCHEMA_VERSION = "field-evidence-relation-v6"
NORMALIZATION_CONTRACT = "typed-facts-v2"
CHUNKING_CONTRACT = "source-grounded-raw-span-v6"
RELATION_FIELDS = (
    "benefit_type", "action", "target", "condition", "value", "unit",
    "cap", "frequency", "period", "exceptions",
)
RAW_FACT_FIELDS = RELATION_FIELDS[2:]
NUMBER = re.compile(r"(?<![0-9.])(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(만원|천원|원|%|퍼센트)?")
WON_AMOUNT = re.compile(r"(?<![0-9.])[-+]?\d[\d,.]*(?:\s*[조억만천백십]\s*(?:\d[\d,.]*)?)+\s*원")
MONEY_PART = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|[조억만천백십]")
BUNDLE_FIELDS = {
    "benefit": ("target", "action", "value", "unit"),
    "condition": ("condition",), "cap": ("cap",),
    "frequency": ("frequency",), "period": ("period",),
    "exceptions": ("exceptions",), "label_context": ("benefit_type",),
}


def normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _decimal(text: str) -> str | None:
    try:
        return format(Decimal(text.replace(",", "")).normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def _numeric_text(value: str) -> str:
    """Comparison-only view; never rewrite the saved OCR or its offsets."""
    def money(match: re.Match[str]) -> str:
        body = match.group().removesuffix('원').strip()
        sign = -1 if body.startswith('-') else 1
        body = body.lstrip('+-')
        parts = MONEY_PART.findall(body)
        if ''.join(parts) != re.sub(r'\s+', '', body):
            raise ValueError('invalid monetary literal')
        total = section = Decimal(0)
        pending = None
        last_large, last_small = 10**16, 10000
        scales = {'조':10**12, '억':10**8, '만':10000, '천':1000, '백':100, '십':10}
        for part in parts:
            if part not in scales:
                if pending is not None:
                    raise ValueError('ambiguous adjacent monetary numbers')
                pending = Decimal(part.replace(',', ''))
                continue
            scale = scales[part]
            if scale >= 10000:
                if scale >= last_large:
                    raise ValueError('monetary units must descend')
                if pending is None and last_small == 10000:
                    raise ValueError('monetary group requires an explicit amount')
                section += pending if pending is not None else 0
                total += section * scale
                section, pending, last_large, last_small = Decimal(0), None, scale, 10000
            else:
                if scale >= last_small:
                    raise ValueError('monetary units must descend')
                section += (pending if pending is not None else Decimal(1)) * scale
                pending, last_small = None, scale
        amount = (total + section + (pending if pending is not None else 0)) * sign
        return format(amount.normalize(), 'f') + '원'
    return WON_AMOUNT.sub(money, normalized(value))


def literal_number_spans(text: str) -> list[tuple[int, int]]:
    """Original-source number ranges using the same longest-money grammar.

    The final unit suffix stays outside the number range. Internal scales in
    a compound amount belong to that single number, not separate assertions.
    """
    compounds = list(WON_AMOUNT.finditer(text))
    spans = []
    for match in compounds:
        _numeric_text(match.group())  # Reject invalid scale order consistently.
        unit = re.search(r"(?:[조억만천백십])?\s*원\s*$", match.group())
        spans.append((match.start(), match.start() + unit.start()))
    for match in NUMBER.finditer(text):
        if not any(left.start() <= match.start() < left.end() for left in compounds):
            spans.append(match.span(1))
    return sorted(spans)


def typed_literals(value: str) -> list[dict[str, str]]:
    """Return lossless local typed literals; comparisons never coerce dimensions."""
    result: list[dict[str, str]] = []
    for raw_number, unit in NUMBER.findall(_numeric_text(value)):
        number = _decimal(raw_number)
        if number is None:
            continue
        if unit == "%" or unit == "퍼센트":
            result.append({"kind": "ratio", "decimal": format((Decimal(number) / Decimal(100)).normalize(), "f")})
        elif unit == "만원":
            result.append({"kind": "KRW", "decimal": format((Decimal(number) * 10_000).normalize(), "f")})
        elif unit == "천원":
            result.append({"kind": "KRW", "decimal": format((Decimal(number) * 1_000).normalize(), "f")})
        elif unit == "원":
            result.append({"kind": "KRW", "decimal": number})
        else:
            result.append({"kind": "number", "decimal": number})
    return result


def condition_operators(value: str) -> list[str]:
    text = normalized(value)
    result: list[str] = []
    if ">=" in text or "이상" in text:
        result.append("gte")
    elif ">" in text or "초과" in text:
        result.append("gt")
    if "이하" in text or "<=" in text:
        result.append("lte")
    elif "미만" in text or "<" in text:
        result.append("lt")
    return result


def normalise_fact(raw: dict[str, Any]) -> dict[str, Any]:
    if any(not isinstance(raw.get(field, ""), str) for field in RELATION_FIELDS):
        raise ValueError("raw fact fields must be strings")
    fact = {field: normalized(raw.get(field, "")) for field in RELATION_FIELDS}
    if not fact["benefit_type"] or not fact["action"] or not fact["target"]:
        raise ValueError("required fact field missing: benefit_type, action, or target")
    if not any(fact[field] for field in ("condition", "value", "cap", "frequency", "period", "exceptions")):
        raise ValueError("required fact content is missing")
    if fact["value"] in {"-", "—"}:
        raise ValueError("blank or dash value is a rule failure")
    value_with_unit = fact["value"]
    dimensions = {"%": "ratio", "퍼센트": "ratio", "원": "KRW", "만원": "KRW", "천원": "KRW"}
    if fact["unit"] in dimensions:
        inline_units = [unit for _number, unit in NUMBER.findall(_numeric_text(value_with_unit)) if unit]
        if inline_units and any(dimensions[unit] != dimensions[fact["unit"]] for unit in inline_units):
            raise ValueError("value and unit have incompatible dimensions")
        if not inline_units:
            value_with_unit = f"{value_with_unit}{fact['unit']}"
    typed = {
        field: typed_literals(value_with_unit if field == "value" else fact[field])
        for field in ("condition", "value", "cap", "frequency", "period")
        if fact[field]
    }
    typed["condition_operators"] = condition_operators(fact["condition"])
    return {**fact, "typed_normalization": typed}


def material_text_key(value: str) -> str:
    """Comparison view of material tokens, never a source/offset rewrite.

    Only affirmative grammar is discarded. Negation, only/from/to, operators,
    amounts and unknown lexical content remain significant. This is not a
    sentence-level semantic model and does not guess unknown paraphrases.
    """
    text = normalized(value)
    # Only an explicit editorial reference; '(앱 결제만)' remains material.
    text = re.sub(r"\((?:자세한|상세한)\s*내용은\s*안내\s*참고\)", "", text)
    text = re.sub(r"[※•]", " ", text)
    text = re.sub(r"(?<![가-힣])(?:서비스\s*)?(?:적용|제공)(?=\s|$)", "", text)
    text = re.sub(r"(?:입니다|됩니다|합니다|이다|이며)(?=[\s.!。,，;；]|$)", "", text)
    text = re.sub(r"(실적|금액|한도|할인율|적립률)(?:은|는|이|가)(?=\s|\d|$)", r"\1", text)
    text = re.sub(r"(이내|이후|이전)에(?=[\s.!。,，;；]|$)", r"\1", text)
    text = _numeric_text(text)
    literals = iter(typed_literals(text))
    text = NUMBER.sub(lambda _: (lambda x: f"<{x['kind']}:{x['decimal']}>")(next(literals)), text)
    text = text.strip(" .。,，;；")
    return re.sub(r"(?<!>)\s+|\s+(?!<)", "", text)


def _literal_key(fact: dict[str, Any], field: str) -> str:
    text = normalized(fact.get(field, ""))
    # Only affirmative presentation endings. Negation, scope particles (만,
    # 부터, 까지), comparison operators and unknown prose remain significant.
    text = re.sub(r"(?:입니다|됩니다)(?=[.!。]?\s*$)", "", text)
    text = re.sub(r"((?:전월|지난달)\s*실적|할인율|적립률|한도)(?:은|는|이|가)(?=\s|\d)", r"\1", text)
    if field == "value" and fact.get("unit") in {"%", "퍼센트", "원", "만원", "천원"} and not any(unit for _number, unit in NUMBER.findall(text)):
        text += str(fact["unit"])
    # Value already carries the unit dimension, including a separate unit field.
    if field == "unit" and fact.get("unit") in {"%", "퍼센트", "원", "만원", "천원"}:
        return ""
    return material_text_key(text)


def field_comparison_key(fact: dict[str, Any], field: str) -> str:
    """Role-bound atoms, retaining unparsed text instead of guessing its meaning.

    This is deliberately not a bag of words/numbers. Unsupported clauses retain
    their residual text and cannot become equal merely by containing the same
    amounts. Labels remove only content repeated in this same fact's core roles;
    the source validator separately checks ownership of those exact spans.
    """
    text = _literal_key(fact, field)
    if field == "target":
        # An explicit target enumeration is a set, not word/sentence order.
        parts = re.split(r"[,，/·]", text)
        if len(parts) > 1 and all(parts):
            return json.dumps(sorted(set(parts)), ensure_ascii=False)
    if field == "action":
        action = re.fullmatch(r"(청구할인|할인|적립|캐시백|면제|무료)(?:서비스|율|률|제공)?", text)
        if action:
            return action[1].replace("청구할인", "할인")
    if field == "benefit_type":
        repeated = {_literal_key(fact, key) for key in ("target", "action", "value")}
        for token in sorted(repeated - {""}, key=len, reverse=True):
            # Keep embedded standalone numerals (e.g. 10 in 100) intact.
            if re.fullmatch(r"<(?:number|ratio|KRW):[^>]+>", token) or not NUMBER.search(token):
                text = text.replace(token, "")
        # Generic display labels add no material predicate. Qualifiers such as
        # daily/shared/online and unmatched values remain comparison content.
        return "" if text in {"", "서비스", "안내", "서비스안내", "혜택", "청구", "제공"} else text
    if field == "condition":
        # Only complete, named spending predicates can commute under explicit
        # AND. OR, mixed logic, exceptions and unknown prose remain residual.
        parts = re.split(r"및|그리고", text)
        atoms = []
        role = r"(?:전월실적|전월이용금액|건당결제금액|건당이용금액|건당)"
        amount = r"<KRW:-?\d+(?:\.\d+)?>"
        operator = r"(?:이상|초과|이하|미만)"
        for part in parts:
            forward = re.fullmatch(f"({role})({amount})({operator})", part)
            reverse = re.fullmatch(f"({amount})({operator})({role})", part)
            if forward:
                name, value, op = forward.groups()
            elif reverse:
                value, op, name = reverse.groups()
            else:
                break
            atoms.append((name, value, op))
        if len(atoms) == len(parts):
            return json.dumps({"and": sorted(atoms)}, ensure_ascii=False, sort_keys=True)
    if field in {"period", "condition", "cap", "frequency"}:
        # A final locative particle does not change a fully identified deadline.
        text = re.sub(r"(<number:-?\d+(?:\.\d+)?>)(영업일|일|개월|년)(이내|이후|이전)에$", r"\1\2\3", text)
    if field == "cap":
        # A complete cap atom can commute with its period label. Never apply
        # this to a sentence containing exceptions or other residual content.
        amount = r"<KRW:-?\d+(?:\.\d+)?>"
        prefix = re.fullmatch(r"(월|연|일|건당|건)(통합|공유|합산)?(?:최대|한도|할인한도|적립한도)?(" + amount + r")", text)
        suffix = re.fullmatch(r"(" + amount + r")/(월|연|일|건당|건)", text)
        if prefix:
            period, shared, value = prefix.groups()
            return json.dumps({'cap': [period, shared or '', value]}, ensure_ascii=False)
        if suffix:
            value, period = suffix.groups()
            return json.dumps({'cap': [period, '', value]}, ensure_ascii=False)
    return text


def fact_bundles(fact: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Named role mappings; each qualifier remains attached to ONE benefit."""
    return {group: {field: field_comparison_key(fact, field) for field in fields}
            for group, fields in BUNDLE_FIELDS.items()}


def relation_key(fact: dict[str, Any]) -> tuple[str, ...]:
    typed = fact.get("typed_normalization")
    if not isinstance(typed, dict):
        raise ValueError("typed normalization is required for relation comparison")

    # Derived typed_normalization is not a second, order-sensitive raw-text
    # equality gate. Units/operators and all residuals live in the role keys.
    return tuple(json.dumps({name: value}, ensure_ascii=False, sort_keys=True)
                 for name, value in sorted(fact_bundles(fact).items()))
