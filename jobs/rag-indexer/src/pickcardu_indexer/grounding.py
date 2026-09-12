"""Versioned source-grounding primitives for structured card benefits.

This module intentionally has no provider, filesystem, or model dependency.  It
keeps raw strings for audit while producing small typed comparison keys locally.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
import unicodedata
from typing import Any


STRUCTURE_SCHEMA_VERSION = "field-evidence-relation-v6"
CHUNKING_CONTRACT = "source-grounded-raw-span-v6"
RELATION_FIELDS = (
    "benefit_type", "action", "target", "condition", "value", "unit",
    "cap", "frequency", "period", "exceptions",
)
RAW_FACT_FIELDS = RELATION_FIELDS[2:]
NUMBER = re.compile(r"(?<![0-9.])(-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(만원|천원|원|%|퍼센트)?")


def normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).split())


def _decimal(text: str) -> str | None:
    try:
        return format(Decimal(text.replace(",", "")).normalize(), "f")
    except (InvalidOperation, ValueError):
        return None


def typed_literals(value: str) -> list[dict[str, str]]:
    """Return lossless local typed literals; comparisons never coerce dimensions."""
    result: list[dict[str, str]] = []
    for raw_number, unit in NUMBER.findall(normalized(value)):
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
        inline_units = [unit for _number, unit in NUMBER.findall(value_with_unit) if unit]
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


def relation_key(fact: dict[str, Any]) -> tuple[str, ...]:
    typed = fact.get("typed_normalization")
    if not isinstance(typed, dict):
        raise ValueError("typed normalization is required for relation comparison")
    def semantic_text(field: str) -> str:
        text = str(fact.get(field, ""))
        if field == "value" and fact.get("unit") in {"%", "퍼센트", "원", "만원", "천원"} and not any(unit for _number, unit in NUMBER.findall(text)):
            text += str(fact["unit"])
        # The value key already includes its dimension, whether that unit was
        # inline (10%) or separate (10 + %). Do not compare it a second time.
        if field == "unit" and fact.get("unit") in {"%", "퍼센트", "원", "만원", "천원"}:
            return ""
        literals = iter(typed_literals(text))
        text = NUMBER.sub(
            lambda _match: (lambda literal: f"<{literal['kind']}:{literal['decimal']}>")(next(literals)),
            text,
        ).rstrip(".。,，;；")
        # Layout-only spacing is not a relationship difference.  Keep spaces
        # between adjacent numeric placeholders so separate values cannot join.
        return re.sub(r"(?<!>)\s+|\s+(?!<)", "", text)

    # Source strings remain in the lane artifact; canonical strings have only
    # NFKC/whitespace normalization. Replace typed literals in comparison keys,
    # so 30만원 and 300000원 agree without erasing target/action/exception meaning.
    raw = tuple(semantic_text(field) for field in RELATION_FIELDS)
    typed_key = repr(sorted((key, repr(value)) for key, value in typed.items()))
    return (*raw, typed_key)
