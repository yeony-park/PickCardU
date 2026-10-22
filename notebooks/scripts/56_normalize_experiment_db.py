"""Normalize a copied stage-30 experiment database without touching its sources.

The pure ``normalize_fact`` function is the contract used by the independent
regression test.  The runner verifies immutable inputs, copies the stage-30
database, updates only conservative derived fields, and exports a fact map that
is bound to the copied database version.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

import fitz


INPUT_DIR = Path("notebooks/data/30_structured_benefit_db")
OUTPUT_ROOT = Path("notebooks/data/56_structured_normalization")
DB_OUTPUT_DIR = OUTPUT_ROOT / "db_v2"
REVIEW_OUTPUT_DIR = OUTPUT_ROOT / "db_source_review"
NORMALIZATION_VERSION = "structured-benefit-experiment-v3-normalization56-v3"
EXPECTED_INPUT_HASHES = {
    "structured.sqlite": "65cb90fd3d41ab8b9d3ba3db1cc7e841d2415768f278f0e7d3337c96fb9a38c8",
    "documents.jsonl": "fdf855a8d3b11103f776f913b010860c6b9c5e53af5b63a61738683c3d678eac",
    "facts.jsonl": "bdd116338687101a0362c703839ca24056abbe0443c50b7bd622f905db1b3d9c",
}
EXPECTED_RULE_COUNTS = {
    "explicit_no_previous_month_requirement": 7,
    "exchange_voucher_is_not_accrual": 1,
    "previous_spend_tier_is_not_payout_cap": 1,
    "eligible_use_amount_is_not_payout_cap": 6,
    "recognized_purchase_amount_is_not_payout_cap": 2,
    "per_transaction_spend_is_not_payout_cap": 2,
    "non_krw_accrual_transaction_ceiling": 2,
}

_NO_SPEND = re.compile(r"전월\s*(?:이용\s*금액|실적)\s*조건\s*없음")
_LINE_ID = re.compile(r"P(?P<page>\d{4})-L(?P<line>\d{4})")
_RULE_CLAIMS = {
    "explicit_no_previous_month_requirement": "explicit_zero_minimum_required_spend",
    "exchange_voucher_is_not_accrual": "voucher_exchange_not_purchase_accrual",
    "previous_spend_tier_is_not_payout_cap": "performance_tier_not_payout_cap",
    "eligible_use_amount_is_not_payout_cap": "eligible_purchase_amount_not_payout_cap",
    "recognized_purchase_amount_is_not_payout_cap": "recognized_purchase_amount_not_payout_cap",
    "per_transaction_spend_is_not_payout_cap": "transaction_purchase_amount_not_payout_cap",
    "non_krw_accrual_transaction_ceiling": "per_unit_points_transaction_amount_not_payout_cap",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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


def normalize_fact(
    raw_fact: Mapping[str, Any], current_derived: Mapping[str, Any]
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Return a non-mutating, deterministic derived-field normalization.

    Missing or vague language remains unknown.  Rules use semantic field
    patterns and evidence bindings; card and fact identifiers are never read.
    """

    result = copy.deepcopy(dict(current_derived))
    rules: list[str] = []
    condition = str(raw_fact.get("condition") or "").strip()
    if _NO_SPEND.fullmatch(condition):
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _document_line_text_by_id(document: Mapping[str, Any]) -> dict[str, str]:
    lines: dict[str, str] = {}
    for page in document.get("pages") or []:
        page_number = int(page["page"])
        for line_number, text in enumerate(str(page.get("text") or "").splitlines(), start=1):
            lines[f"P{page_number:04d}-L{line_number:04d}"] = text
    return lines


def _with_source_lines(raw_fact: Mapping[str, Any], document: Mapping[str, Any]) -> dict[str, Any]:
    enriched = copy.deepcopy(dict(raw_fact))
    line_ids = {
        str(item.get("line_id"))
        for evidence in (raw_fact.get("field_evidence") or {}).values()
        for item in evidence or []
        if isinstance(item, Mapping) and item.get("line_id")
    }
    source_lines = _document_line_text_by_id(document)
    enriched["_source_line_text_by_id"] = {
        line_id: source_lines[line_id] for line_id in line_ids if line_id in source_lines
    }
    return enriched


def _direct_pdf_evidence(pdf_path: Path, pages: set[int], fragments: list[str]) -> dict[str, Any]:
    excerpts: list[dict[str, Any]] = []
    image_review_required_pages: list[int] = []
    document = fitz.open(pdf_path)
    try:
        for page_number in sorted(pages):
            text = document[page_number - 1].get_text("text")
            if not text.strip():
                image_review_required_pages.append(page_number)
                continue
            candidates = []
            for line in text.splitlines():
                compact_line = _compact(line)
                if any(
                    len(_compact(fragment)) >= 3 and _compact(fragment) in compact_line
                    for fragment in fragments
                ):
                    candidates.append(line.strip())
            excerpts.append({"page": page_number, "lines": candidates[:12]})
    finally:
        document.close()
    return {
        "pdf_text_layer_excerpts": excerpts,
        "image_review_required_pages": image_review_required_pages,
        "review_method": (
            "PyMuPDF text-layer extraction only; pages without a text layer require a separately bound manual image-review record."
        ),
    }


def _manual_review_index(
    repository_root: Path, review_path: Path
) -> tuple[dict[tuple[str, str, int], dict[str, Any]], list[str]]:
    payload = json.loads(review_path.read_text(encoding="utf-8"))
    index: dict[tuple[str, str, int], dict[str, Any]] = {}
    errors: list[str] = []
    receipt_ids: set[str] = set()
    for record in payload.get("records") or []:
        key = (str(record["pdf_path"]), str(record["pdf_sha256"]), int(record["page"]))
        pdf_path = repository_root / record["pdf_path"]
        png_path = repository_root / record["rendered_png_path"]
        if _sha256(pdf_path) != record["pdf_sha256"]:
            errors.append(f"PDF hash mismatch: {key}")
        if not png_path.is_file() or _sha256(png_path) != record["rendered_png_sha256"]:
            errors.append(f"rendered PNG hash mismatch: {key}")
        render = record.get("render") or {}
        zoom = float(render.get("zoom", 0))
        alpha = render.get("alpha")
        if zoom <= 0 or alpha is not False:
            errors.append(f"unexpected manual review render contract: {key}")
        document = fitz.open(pdf_path)
        try:
            rendered = document[int(record["page"]) - 1].get_pixmap(
                matrix=fitz.Matrix(zoom, zoom), alpha=False
            ).tobytes("png")
        finally:
            document.close()
        if hashlib.sha256(rendered).hexdigest() != record["rendered_png_sha256"]:
            errors.append(f"original PDF page rerender mismatch: {key}")
        if not str(record.get("reviewer") or "").strip() or not str(record.get("observation") or "").strip():
            errors.append(f"missing manual review attribution or observation: {key}")
        if not str(record.get("receipt_id") or "").strip():
            errors.append(f"missing manual review receipt id: {key}")
        elif record["receipt_id"] in receipt_ids:
            errors.append(f"duplicate manual review receipt id: {record['receipt_id']}")
        else:
            receipt_ids.add(record["receipt_id"])
        if not record.get("supports_rules") or not record.get("claim_tags"):
            errors.append(f"missing manual review rule or claim binding: {key}")
        if key in index:
            errors.append(f"duplicate manual review: {key}")
        index[key] = record
    return index, errors


def _page_text_lines(pdf_path: Path, pages: list[int]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    document = fitz.open(pdf_path)
    try:
        for page in pages:
            result[page] = [line.strip() for line in document[page - 1].get_text("text").splitlines()]
    finally:
        document.close()
    return result


def _decisive_text_quotes(
    pdf_path: Path, pages: list[int], rule: str, raw_fact: Mapping[str, Any]
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for page, lines in _page_text_lines(pdf_path, pages).items():
        compact_lines = [_compact(line) for line in lines]
        indexes: set[int] = set()
        if rule == "explicit_no_previous_month_requirement":
            indexes = {
                index for index, line in enumerate(compact_lines)
                if "전월실적조건없음" in line or "전월이용금액조건없음" in line
            }
        elif rule == "exchange_voucher_is_not_accrual":
            indexes = {
                index for index, line in enumerate(compact_lines)
                if any(token in line for token in ("교환바우처", "교환해드리는", "교환가능"))
            }
        elif rule == "previous_spend_tier_is_not_payout_cap":
            anchors = [
                index for index, line in enumerate(compact_lines)
                if "전월실적30만원이상에해당하는" in line
            ]
            for anchor in anchors:
                indexes.update(range(anchor, min(anchor + 3, len(lines))))
        elif rule == "eligible_use_amount_is_not_payout_cap":
            benefit = _compact(raw_fact.get("benefit_type"))
            service = benefit.replace("월이용금액한도", "").replace("이용금액한도", "")
            cap = _compact(raw_fact.get("cap"))
            indexes = {
                index for index, line in enumerate(compact_lines)
                if (benefit and benefit in line)
                or (service and service in line and "월이용금액한도" in line)
                or (cap and cap in line)
                or ("주유이용금액" in line and "10%" in line)
            }
        elif rule == "non_krw_accrual_transaction_ceiling":
            indexes = {
                index for index, line in enumerate(compact_lines)
                if any(token in line for token in ("리터당", "1회10만원한도", "10만원초과주유금액"))
            }
        for index in sorted(indexes):
            selected.append({"page": page, "text": lines[index]})
    return selected


def _text_proof_terms(rule: str, raw_fact: Mapping[str, Any]) -> list[str]:
    if rule == "explicit_no_previous_month_requirement":
        return ["전월실적조건없음"]
    if rule == "exchange_voucher_is_not_accrual":
        return ["교환바우처", "교환해드리는"]
    if rule == "previous_spend_tier_is_not_payout_cap":
        return ["전월실적30만원이상에해당하는할인한도", "해당실적구간"]
    if rule == "eligible_use_amount_is_not_payout_cap":
        benefit = _compact(raw_fact.get("benefit_type"))
        service = benefit.replace("월이용금액한도", "").replace("이용금액한도", "")
        return [service, "월이용금액한도", _compact(raw_fact.get("cap"))]
    if rule == "non_krw_accrual_transaction_ceiling":
        return ["리터당", _compact(raw_fact.get("cap")), _compact("M포인트")]
    return []


def _attach_decisive_proof(
    repository_root: Path,
    source_support: dict[str, Any],
    rule: str,
    raw_fact: Mapping[str, Any],
    fact_id: str,
    manual_reviews: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> dict[str, Any]:
    pdf_path = repository_root / source_support["path"]
    claim_tag = _RULE_CLAIMS[rule]
    if source_support.get("image_review_required_pages"):
        receipts = []
        for page in source_support["image_review_required_pages"]:
            record = manual_reviews.get((source_support["path"], source_support["sha256"], int(page)))
            if record and rule in record["supports_rules"] and claim_tag in record["claim_tags"]:
                receipts.append(record["receipt_id"])
        proof = {
            "proof_type": "manual_pdf_image_review",
            "claim_tag": claim_tag,
            "rule_id": rule,
            "manual_receipt_ids": receipts,
            "required_pages": source_support["image_review_required_pages"],
            "scope": "derived classification decision only; not a full OCR or raw-field audit",
        }
    else:
        quotes = _decisive_text_quotes(pdf_path, source_support["pages"], rule, raw_fact)
        proof = {
            "proof_type": "pdf_text_layer_quote",
            "claim_tag": claim_tag,
            "rule_id": rule,
            "quotes": quotes,
            "required_compact_terms": _text_proof_terms(rule, raw_fact),
            "scope": "derived classification decision only; not a full OCR or raw-field audit",
        }
        if rule == "non_krw_accrual_transaction_ceiling" and "60-80" in str(raw_fact.get("value")):
            proof["meaning_preserving_notation_note"] = (
                "The structured value 60-80 corresponds to the original PDF typography 60·80; this notation difference is not the cap-classification proof."
            )
    proof["fact_binding"] = fact_id
    encoded = json.dumps(proof, ensure_ascii=False, sort_keys=True).encode("utf-8")
    proof["proof_id"] = "proof_" + hashlib.sha256(encoded).hexdigest()[:24]
    source_support["decisive_proof"] = proof
    return source_support


def _decisive_proof_complete(
    repository_root: Path,
    source_support: Mapping[str, Any],
    manual_reviews: Mapping[tuple[str, str, int], Mapping[str, Any]],
) -> bool:
    proof = source_support.get("decisive_proof") or {}
    if proof.get("proof_type") == "pdf_text_layer_quote":
        page_lines = _page_text_lines(repository_root / source_support["path"], source_support["pages"])
        quotes = proof.get("quotes") or []
        if not quotes or not all(item["text"] in page_lines.get(int(item["page"]), []) for item in quotes):
            return False
        compact_quotes = _compact(" ".join(item["text"] for item in quotes))
        return all(term and term in compact_quotes for term in proof.get("required_compact_terms") or [])
    if proof.get("proof_type") == "manual_pdf_image_review":
        matched = []
        for page in proof.get("required_pages") or []:
            record = manual_reviews.get((source_support["path"], source_support["sha256"], int(page)))
            if record:
                matched.append(record)
        return bool(matched) and all(
            proof["rule_id"] in record["supports_rules"]
            and proof["claim_tag"] in record["claim_tags"]
            and record["receipt_id"] in proof.get("manual_receipt_ids", [])
            for record in matched
        ) and len(matched) == len(proof.get("required_pages") or [])
    return False


def _source_support(repository_root: Path, document: Mapping[str, Any], raw_fact: Mapping[str, Any], rule: str) -> dict[str, Any]:
    field_by_rule = {
        "explicit_no_previous_month_requirement": ("condition",),
        "exchange_voucher_is_not_accrual": ("action", "benefit_type", "exceptions"),
        "previous_spend_tier_is_not_payout_cap": ("benefit_type", "cap"),
        "eligible_use_amount_is_not_payout_cap": ("target", "action", "benefit_type", "value", "unit", "cap"),
        "recognized_purchase_amount_is_not_payout_cap": ("target", "action", "benefit_type", "value", "unit", "cap"),
        "per_transaction_spend_is_not_payout_cap": ("target", "action", "benefit_type", "value", "unit", "cap", "frequency"),
        "non_krw_accrual_transaction_ceiling": ("target", "action", "benefit_type", "value", "unit", "condition", "cap", "frequency"),
    }
    fields = field_by_rule.get(rule, ("target", "action", "benefit_type", "value", "unit", "condition", "cap", "frequency", "exceptions"))
    evidence: list[dict[str, Any]] = []
    pages: set[int] = set()
    for field in fields:
        for item in (raw_fact.get("field_evidence") or {}).get(field) or []:
            if not isinstance(item, Mapping):
                continue
            line_id = str(item.get("line_id") or "")
            match = _LINE_ID.fullmatch(line_id)
            if match:
                pages.add(int(match.group("page")))
            evidence.append({"field": field, "line_id": line_id, "fragment": str(item.get("fragment") or "")})
    pdf_path = repository_root / "data" / "raw" / f"{document['document_id']}.pdf"
    actual_hash = _sha256(pdf_path)
    if actual_hash != document["source_pdf_sha256"]:
        raise RuntimeError(f"source PDF hash mismatch for {document['document_id']}")
    observations = {
        "explicit_no_previous_month_requirement": "The source explicitly says there is no previous-month performance condition; zero is the minimum required threshold, not customer spend.",
        "exchange_voucher_is_not_accrual": "The source describes presenting or registering a voucher to exchange it for points, not earning points from a purchase.",
        "previous_spend_tier_is_not_payout_cap": "The amount is the previous-month performance tier used to select a separate discount-limit row.",
        "eligible_use_amount_is_not_payout_cap": "The source labels the amount as the monthly eligible purchase amount; the payout is the stated percentage of that amount.",
        "recognized_purchase_amount_is_not_payout_cap": "The source says purchases up to this amount are recognized as the cashback or point base.",
        "per_transaction_spend_is_not_payout_cap": "The source limits fuel or other purchase amount per transaction; it does not state a benefit payout of that size.",
        "non_krw_accrual_transaction_ceiling": "The benefit accrues non-KRW points per fuel unit while the KRW figure is the bound purchase amount in the same source block.",
    }
    direct_evidence = _direct_pdf_evidence(
        pdf_path,
        pages,
        [item["fragment"] for item in evidence if item["fragment"]],
    )
    return {
        "path": pdf_path.relative_to(repository_root).as_posix(),
        "sha256": actual_hash,
        "pages": sorted(pages),
        "observation": observations[rule],
        "field_evidence": evidence,
        "evidence_mode": "mechanical PDF-page extraction plus a separate rule-specific decisive proof; validates only the changed derived classification",
        **direct_evidence,
    }


def _negative_control(facts: list[dict[str, Any]], rule: str, documents: Mapping[str, dict[str, Any]], repository_root: Path) -> dict[str, Any]:
    def predicate(row: dict[str, Any]) -> bool:
        raw = row["raw_fact"]
        if rule == "explicit_no_previous_month_requirement":
            return "관계없이" in str(raw.get("condition") or "") and not normalize_fact(raw, row["derived"])[1]
        if rule == "exchange_voucher_is_not_accrual":
            text = _compact(f"{raw.get('action', '')} {raw.get('benefit_type', '')}")
            return "적립" in text and "교환" not in text and row["derived"].get("action_kind") == "earn"
        cap = _compact(raw.get("cap"))
        text = _compact(f"{raw.get('action', '')} {raw.get('benefit_type', '')}")
        return (
            row["derived"].get("cap_state") == "known"
            and any(token in text for token in ("할인", "적립", "캐시백"))
            and re.search(r"(?:할인|적립|캐시백)(?:한도)?.*\d", cap) is not None
            and _cap_semantics_rule(raw) is None
        )

    candidates = [row for row in facts if predicate(row)]
    if not candidates:
        raise RuntimeError(f"no negative source control found for {rule}")
    row = sorted(candidates, key=lambda item: (item["document_id"], item["ordinal"]))[0]
    support_rule = (
        "explicit_no_previous_month_requirement"
        if rule == "explicit_no_previous_month_requirement"
        else ("exchange_voucher_is_not_accrual" if rule == "exchange_voucher_is_not_accrual" else rule)
    )
    source_support = _source_support(
        repository_root, documents[row["document_id"]], row["raw_fact"], support_rule
    )
    source_support["evidence_mode"] = (
        "diagnostic negative-control page context only; not part of the 21 changed-row decisive source gate"
    )
    if rule == "explicit_no_previous_month_requirement":
        source_support["observation"] = (
            "The source does not contain the exact no-previous-month-condition statement required to normalize a zero threshold."
        )
    elif rule == "exchange_voucher_is_not_accrual":
        source_support["observation"] = (
            "The source explicitly describes purchase accrual and is not a voucher exchange row, so the supported earn action remains."
        )
    else:
        source_support["observation"] = (
            "The source states a benefit payout cap and does not meet the complete eligible-spend, tier, transaction, or intermediate-calculation predicate."
        )
    return {
        "rule_id": rule,
        "fact_id": row["fact_id"],
        "card_key": row["document_id"],
        "reason": "Nearby source semantics do not meet the complete normalization predicate and remain unchanged.",
        "source_support": source_support,
    }


def _database_invariant_checks(source_db: Path, output_db: Path) -> dict[str, Any]:
    """Compare schema and immutable source-bearing rows between original and copy."""

    source = sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True)
    copied = sqlite3.connect(f"file:{output_db}?mode=ro&immutable=1", uri=True)
    try:
        for connection in (source, copied):
            connection.execute("PRAGMA query_only=ON")
        schema_query = (
            "SELECT type,name,tbl_name,sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )
        schema_identical = source.execute(schema_query).fetchall() == copied.execute(schema_query).fetchall()
        alias_columns = [row[1] for row in source.execute("PRAGMA table_info(target_aliases)")]
        document_columns = [row[1] for row in source.execute("PRAGMA table_info(documents)")]
        fact_columns = [row[1] for row in source.execute("PRAGMA table_info(facts)")]
        mutable_fact_columns = {
            "action_kind",
            "action_status",
            "spend_threshold_krw",
            "spend_operator",
            "spend_state",
            "cap_krw",
            "cap_state",
            "cap_period",
            "derived_json",
        }
        immutable_fact_columns = [name for name in fact_columns if name not in mutable_fact_columns]

        def ordered_rows(
            connection: sqlite3.Connection, table: str, columns: list[str], order: str
        ) -> list[tuple[Any, ...]]:
            projection = ",".join(f'"{column}"' for column in columns)
            return connection.execute(f'SELECT {projection} FROM "{table}" ORDER BY {order}').fetchall()

        aliases_identical = ordered_rows(source, "target_aliases", alias_columns, "rowid") == ordered_rows(
            copied, "target_aliases", alias_columns, "rowid"
        )
        documents_identical = ordered_rows(source, "documents", document_columns, "document_id") == ordered_rows(
            copied, "documents", document_columns, "document_id"
        )
        immutable_facts_identical = ordered_rows(
            source, "facts", immutable_fact_columns, "document_id,ordinal"
        ) == ordered_rows(copied, "facts", immutable_fact_columns, "document_id,ordinal")
        return {
            "sqlite_schema_identical": schema_identical,
            "target_alias_rows_identical": aliases_identical,
            "document_rows_identical": documents_identical,
            "immutable_fact_columns": immutable_fact_columns,
            "immutable_fact_rows_identical": immutable_facts_identical,
        }
    finally:
        source.close()
        copied.close()


def _metadata_value(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"missing metadata key {key}")
    return json.loads(row[0])


def _update_metadata(connection: sqlite3.Connection, changes: list[dict[str, Any]], input_hashes: Mapping[str, str]) -> None:
    counts = _metadata_value(connection, "counts")
    counts.update(
        known_spend_threshold=connection.execute("SELECT count(*) FROM facts WHERE spend_state='known'").fetchone()[0],
        known_cap=connection.execute("SELECT count(*) FROM facts WHERE cap_state='known'").fetchone()[0],
        known_cap_period=connection.execute(
            "SELECT count(*) FROM facts WHERE cap_state='known' AND json_extract(derived_json, '$.cap_period_state')='known'"
        ).fetchone()[0],
        positive_supported_action=connection.execute("SELECT count(*) FROM facts WHERE action_status='true'").fetchone()[0],
    )
    rates = _metadata_value(connection, "support_rates")
    for key in ("known_percent", "known_spend_threshold", "known_cap", "known_cap_period", "positive_supported_action"):
        rates[key] = counts[key] / counts["facts"]
    limitations = _metadata_value(connection, "limitations")
    extra = [
        "Stage 56 sets zero spend only for an exact explicit no-previous-month-condition statement.",
        "Voucher exchange is not treated as purchase accrual without an explicit accrual action.",
        "Eligible purchase, transaction, and performance-tier ceilings remain unknown as payout caps.",
        "Reward amounts calculated after applying a benefit rate remain payout caps, including the Olive Young reward-application limits.",
    ]
    limitations.extend(item for item in extra if item not in limitations)
    values = {
        "schema_version": NORMALIZATION_VERSION,
        "counts": counts,
        "support_rates": rates,
        "limitations": limitations,
        "normalization56": {
            "version": NORMALIZATION_VERSION,
            "input_sha256": dict(input_hashes),
            "changed_fact_count": len(changes),
            "rule_counts": dict(sorted(Counter(rule for item in changes for rule in item["rules"]).items())),
            "changed_fact_ids": [item["fact_id"] for item in changes],
            "rule_policy": "deterministic source-semantic rules; no card or fact identifier whitelist",
        },
    }
    for key, value in values.items():
        connection.execute(
            "INSERT INTO metadata(key,value_json) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )


def build_copy(repository_root: Path) -> dict[str, Any]:
    repository_root = repository_root.resolve()
    source_dir = repository_root / INPUT_DIR
    output_dir = repository_root / DB_OUTPUT_DIR
    review_dir = repository_root / REVIEW_OUTPUT_DIR
    output_dir.mkdir(parents=True, exist_ok=True)
    review_dir.mkdir(parents=True, exist_ok=True)
    manual_review_path = review_dir / "manual_image_reviews.json"
    if not manual_review_path.is_file():
        raise RuntimeError(f"missing independent manual image-review record: {manual_review_path}")
    manual_reviews, manual_review_errors = _manual_review_index(repository_root, manual_review_path)
    if manual_review_errors:
        raise RuntimeError(f"invalid manual image-review record: {manual_review_errors}")

    input_hashes = {name: _sha256(source_dir / name) for name in EXPECTED_INPUT_HASHES}
    if input_hashes != EXPECTED_INPUT_HASHES:
        raise RuntimeError(f"immutable stage-30 input hash mismatch: {input_hashes}")

    source_db = source_dir / "structured.sqlite"
    output_db = output_dir / "structured.sqlite"
    shutil.copyfile(source_db, output_db)
    shutil.copyfile(source_dir / "documents.jsonl", output_dir / "documents.jsonl")

    original_facts = _read_jsonl(source_dir / "facts.jsonl")
    original_documents = {row["document_id"]: row for row in _read_jsonl(source_dir / "documents.jsonl")}
    original_facts_by_id = {row["fact_id"]: row for row in original_facts}
    changes: list[dict[str, Any]] = []

    connection = sqlite3.connect(output_db)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT fact_id,document_id,ordinal,raw_json,derived_json FROM facts ORDER BY document_id,ordinal"
        ).fetchall()
        for fact_id, document_id, ordinal, raw_json, derived_json in rows:
            raw_fact = json.loads(raw_json)
            normalization_fact = _with_source_lines(raw_fact, original_documents[document_id])
            before = json.loads(derived_json)
            after, rules = normalize_fact(normalization_fact, before)
            if not rules:
                continue
            connection.execute(
                """UPDATE facts SET action_kind=?,action_status=?,spend_threshold_krw=?,spend_operator=?,spend_state=?,
                   cap_krw=?,cap_state=?,cap_period=?,derived_json=? WHERE fact_id=?""",
                (
                    after.get("action_kind"), after["action_status"], after.get("spend_threshold_krw"),
                    after.get("spend_operator"), after["spend_state"], after.get("cap_krw"),
                    after["cap_state"], after.get("cap_period"),
                    json.dumps(after, ensure_ascii=False, sort_keys=True), fact_id,
                ),
            )
            source_support = [
                _attach_decisive_proof(
                    repository_root,
                    _source_support(repository_root, original_documents[document_id], raw_fact, rule),
                    rule,
                    raw_fact,
                    fact_id,
                    manual_reviews,
                )
                for rule in rules
            ]
            source_confirmed = bool(source_support) and all(
                _decisive_proof_complete(repository_root, item, manual_reviews)
                for item in source_support
            )
            changes.append(
                {
                    "fact_id": fact_id,
                    "card_key": document_id,
                    "ordinal": ordinal,
                    "rules": list(rules),
                    "before": before,
                    "after": after,
                    "source_confirmed": source_confirmed,
                    "source_support": source_support,
                    "decisive_proof_ids": [item["decisive_proof"]["proof_id"] for item in source_support],
                    "negative_control_rule_ids": list(rules),
                }
            )

        observed_counts = Counter(rule for item in changes for rule in item["rules"])
        expected_change_count = sum(EXPECTED_RULE_COUNTS.values())
        if dict(observed_counts) != EXPECTED_RULE_COUNTS or len(changes) != expected_change_count:
            raise RuntimeError(f"unexpected normalization set: facts={len(changes)} rules={dict(observed_counts)}")
        _update_metadata(connection, changes, input_hashes)
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("foreign key violation before commit")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity failure before commit")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    copy_connection = sqlite3.connect(f"file:{output_db}?mode=ro&immutable=1", uri=True)
    try:
        copy_connection.execute("PRAGMA query_only=ON")
        updated_derived = {
            fact_id: json.loads(derived_json)
            for fact_id, derived_json in copy_connection.execute("SELECT fact_id,derived_json FROM facts")
        }
        restored_second_pass = []
        scalar_mismatches = []
        for row in original_facts:
            derived = updated_derived[row["fact_id"]]
            normalization_fact = _with_source_lines(
                row["raw_fact"], original_documents[row["document_id"]]
            )
            again, rules = normalize_fact(normalization_fact, derived)
            if rules or again != derived:
                restored_second_pass.append(row["fact_id"])
        for values in copy_connection.execute(
            "SELECT fact_id,action_kind,action_status,spend_threshold_krw,spend_operator,spend_state,cap_krw,cap_state,cap_period,derived_json FROM facts"
        ):
            fact_id, action_kind, action_status, spend, spend_operator, spend_state, cap, cap_state, cap_period, derived_json = values
            derived = json.loads(derived_json)
            scalar = (action_kind, action_status, spend, spend_operator, spend_state, cap, cap_state, cap_period)
            expected = (
                derived.get("action_kind"), derived["action_status"], derived.get("spend_threshold_krw"),
                derived.get("spend_operator"), derived["spend_state"], derived.get("cap_krw"),
                derived["cap_state"], derived.get("cap_period"),
            )
            if scalar != expected:
                scalar_mismatches.append(fact_id)
        integrity = copy_connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_key_errors = copy_connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            "documents": copy_connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "facts": copy_connection.execute("SELECT count(*) FROM facts").fetchone()[0],
            "aliases": copy_connection.execute("SELECT count(*) FROM target_aliases").fetchone()[0],
        }
    finally:
        copy_connection.close()

    exported_facts: list[dict[str, Any]] = []
    for original in original_facts:
        exported = copy.deepcopy(original)
        exported["derived"] = updated_derived[original["fact_id"]]
        exported_facts.append(exported)
    _write_jsonl(output_dir / "facts.jsonl", exported_facts)

    changed_ids = {item["fact_id"] for item in changes}
    raw_or_wrapper_mismatches = []
    jsonl_derived_mismatches = []
    for original, exported in zip(original_facts, exported_facts, strict=True):
        original_without_derived = {k: v for k, v in original.items() if k != "derived"}
        exported_without_derived = {k: v for k, v in exported.items() if k != "derived"}
        if original_without_derived != exported_without_derived:
            raw_or_wrapper_mismatches.append(original["fact_id"])
        if exported["derived"] != updated_derived[original["fact_id"]]:
            jsonl_derived_mismatches.append(original["fact_id"])
        if original["fact_id"] not in changed_ids and original["derived"] != exported["derived"]:
            raw_or_wrapper_mismatches.append(original["fact_id"])

    rule_ids = sorted(EXPECTED_RULE_COUNTS)
    negative_controls = {
        rule: _negative_control(original_facts, rule, original_documents, repository_root) for rule in rule_ids
    }
    for record in changes:
        record["negative_controls"] = [negative_controls[rule] for rule in record["rules"]]
    decisive_proof_ids = [
        proof_id for record in changes for proof_id in record["decisive_proof_ids"]
    ]
    proof_fact_bindings_valid = all(
        support["decisive_proof"].get("fact_binding") == record["fact_id"]
        for record in changes
        for support in record["source_support"]
    )
    review = {
        "version": NORMALIZATION_VERSION,
        "source_gate_scope": (
            "This gate validates only whether each of the 21 changed derived values or classification decisions agrees with decisive source evidence; it does not validate every raw field or the full OCR output."
        ),
        "input_database_sha256": input_hashes["structured.sqlite"],
        "changed_fact_count": len(changes),
        "decisive_proof_count": len(decisive_proof_ids),
        "decisive_proof_ids_unique": len(set(decisive_proof_ids)) == len(decisive_proof_ids),
        "proof_fact_bindings_valid": proof_fact_bindings_valid,
        "rule_counts": dict(sorted(Counter(rule for item in changes for rule in item["rules"]).items())),
        "source_confirmed_count": sum(item["source_confirmed"] for item in changes),
        "source_mismatch_count": 0,
        "source_uncertain_count": sum(not item["source_confirmed"] for item in changes),
        "records": changes,
        "negative_controls": negative_controls,
        "source_evidence_gate": {
            "passed": (
                len(changes) == sum(EXPECTED_RULE_COUNTS.values())
                and all(item["source_confirmed"] for item in changes)
                and len(decisive_proof_ids) == len(changes)
                and len(set(decisive_proof_ids)) == len(decisive_proof_ids)
                and proof_fact_bindings_valid
                and len(negative_controls) == len(EXPECTED_RULE_COUNTS)
            ),
            "condition": "each of the 21 changed derived classifications references a rule-specific decisive PDF-text quote or bound manual PDF-image receipt; this is not a full raw-field or OCR audit",
        },
        "paid_followup_gate": {
            "allowed": False,
            "status": "awaiting_main_independent_acceptance",
            "condition": "main must independently accept the mechanically validated source-evidence gate before paid follow-up",
        },
        "deferred": [
            "ambiguous large monthly discount-cap wording without enough evidence to distinguish payout from eligible amount",
            "Olive Young monthly reward-application limits remain known payout caps because the source defines them after percentage calculation",
            "general point conversion or exchange rows outside the exchange-voucher rule",
            "previous-month phrases such as 관계없이 that do not exactly state 조건 없음",
            "new aliases, typo repair, target cross-field matching, and 기차-철도 expansion",
        ],
    }
    review_path = review_dir / "normalization_source_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_hashes = {name: _sha256(output_dir / name) for name in EXPECTED_INPUT_HASHES}
    original_hashes_after = {name: _sha256(source_dir / name) for name in EXPECTED_INPUT_HASHES}
    database_invariants = _database_invariant_checks(source_db, output_db)
    validation = {
        "version": NORMALIZATION_VERSION,
        "input_hashes_before": input_hashes,
        "input_hashes_after": original_hashes_after,
        "input_hashes_unchanged": input_hashes == original_hashes_after == EXPECTED_INPUT_HASHES,
        "output_hashes": output_hashes,
        "counts": counts,
        "sqlite_integrity_check": integrity,
        "foreign_key_errors": foreign_key_errors,
        "changed_fact_count": len(changes),
        "changed_fact_ids_unique": len(changed_ids) == len(changes),
        "idempotence_second_pass_changes": restored_second_pass,
        "scalar_derived_mismatches": scalar_mismatches,
        "jsonl_derived_mismatches": jsonl_derived_mismatches,
        "raw_source_or_wrapper_mismatches": sorted(set(raw_or_wrapper_mismatches)),
        "documents_jsonl_byte_identical": _sha256(source_dir / "documents.jsonl") == _sha256(output_dir / "documents.jsonl"),
        "fact_id_set_unchanged": set(original_facts_by_id) == set(updated_derived),
        "database_invariants": database_invariants,
        "source_review_sha256": _sha256(review_path),
        "source_gate_scope": review["source_gate_scope"],
        "decisive_proof_count": review["decisive_proof_count"],
        "decisive_proof_ids_unique": review["decisive_proof_ids_unique"],
        "proof_fact_bindings_valid": review["proof_fact_bindings_valid"],
        "manual_image_review_sha256": _sha256(manual_review_path),
        "manual_image_review_errors": manual_review_errors,
    }
    if not (
        validation["input_hashes_unchanged"]
        and counts == {"documents": 106, "facts": 3403, "aliases": 22}
        and integrity == "ok"
        and not foreign_key_errors
        and len(changes) == sum(EXPECTED_RULE_COUNTS.values())
        and not restored_second_pass
        and not scalar_mismatches
        and not jsonl_derived_mismatches
        and not raw_or_wrapper_mismatches
        and validation["documents_jsonl_byte_identical"]
        and validation["fact_id_set_unchanged"]
        and database_invariants["sqlite_schema_identical"]
        and database_invariants["target_alias_rows_identical"]
        and database_invariants["document_rows_identical"]
        and database_invariants["immutable_fact_rows_identical"]
        and review["source_evidence_gate"]["passed"]
        and review["decisive_proof_count"] == len(changes)
        and review["decisive_proof_ids_unique"]
        and review["proof_fact_bindings_valid"]
    ):
        raise RuntimeError(f"stage-56 validation failed: {validation}")
    validation_path = review_dir / "validation_results.json"
    validation_path.write_text(json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gate_summary = {
        "status": "mechanically_passed_awaiting_main_independent_acceptance",
        "changed_count": len(changes),
        "source_mismatch_count": review["source_mismatch_count"],
        "source_uncertain_count": review["source_uncertain_count"],
        "source_evidence_status": "passed",
        "source_gate_scope": review["source_gate_scope"],
        "decisive_proof_count": review["decisive_proof_count"],
        "paid_followup_allowed": False,
        "original_files_sha256": original_hashes_after,
        "copied_files_sha256": output_hashes,
    }
    (review_dir / "validation.json").write_text(
        json.dumps(gate_summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {"review": review, "validation": validation}


def main() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    result = build_copy(repository_root)
    print(
        json.dumps(
            {
                "version": result["validation"]["version"],
                "changed_fact_count": result["validation"]["changed_fact_count"],
                "counts": result["validation"]["counts"],
                "sqlite_integrity_check": result["validation"]["sqlite_integrity_check"],
                "paid_followup_gate": result["review"]["paid_followup_gate"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
