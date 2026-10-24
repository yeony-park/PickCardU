"""Build the stage-60 copied experiment DB with conservative derived repairs.

Only derived fields are changed.  Raw facts, source documents, identifiers,
aliases, schema objects, and the stage-56 input artifacts remain immutable.
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


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = REPOSITORY_ROOT / "notebooks/data/56_structured_normalization/db_v2"
OUTPUT_ROOT = REPOSITORY_ROOT / "notebooks/data/60_structured_retrieval_repair"
DB_OUTPUT_DIR = OUTPUT_ROOT / "db_v3"
REVIEW_DIR = OUTPUT_ROOT / "db_source_review"
IMAGE_DIR = REVIEW_DIR / "pdf_images"
RESULT_PATH = OUTPUT_ROOT / "normalization_result.json"
VERSION = "structured-benefit-experiment-v3-normalization60-v1"
INPUT_HASHES = {
    "structured.sqlite": "c984e5ffcca8e4fbcfd3fede8f8213b262e6227d5716aa95af306574c0eda61d",
    "documents.jsonl": "fdf855a8d3b11103f776f913b010860c6b9c5e53af5b63a61738683c3d678eac",
    "facts.jsonl": "6ac256b5daa030829ebd00651e6bdecb5ff0d48328560b634780358e2cccc64a",
}
EXPECTED_RULE_COUNTS = {
    "explicit_compound_no_spend": 23,
    "explicit_free_use_positive": 48,
}
_LINE_ID = re.compile(r"P(?P<page>\d{4})-L(?P<line>\d{4})")
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
_IMAGE_PAGE_NAMES = {
    ("BC/BC_KBank_SIMPLE", 2): "BC_KBank_SIMPLE_page2.png",
    ("ibk/IBK_Everyday_Joy_Credit", 2): "IBK_Everyday_Joy_Credit_page2.png",
    ("ibk/IBK_KPass(Credit)", 3): "IBK_KPass_Credit_page3.png",
    ("ibk/IBK_i-Mileage", 3): "IBK_i-Mileage_page3.png",
    ("woori/Woori_UniMile", 2): "Woori_UniMile_page2.png",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


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


def _assign_if_changed(result: dict[str, Any], updates: Mapping[str, Any]) -> bool:
    changed = any(result.get(key) != value for key, value in updates.items())
    if changed:
        result.update(updates)
    return changed


def normalize_fact(
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


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _fact_export_mismatches(
    database_records: Mapping[str, tuple[dict[str, Any], dict[str, Any]]],
    exported_records: list[dict[str, Any]],
) -> list[str]:
    """Return fact IDs whose JSONL raw/derived payload diverges from SQLite."""

    exported_by_id = {record["fact_id"]: record for record in exported_records}
    mismatches = set(database_records) ^ set(exported_by_id)
    for fact_id in set(database_records) & set(exported_by_id):
        raw_fact, derived = database_records[fact_id]
        exported = exported_by_id[fact_id]
        if exported.get("raw_fact") != raw_fact or exported.get("derived") != derived:
            mismatches.add(fact_id)
    return sorted(mismatches)


def _source_pages(raw_fact: Mapping[str, Any], rule: str) -> list[int]:
    if rule == "explicit_compound_no_spend":
        fields = ["condition"]
    else:
        _, _, fields = _free_match(raw_fact)
    pages = set()
    for field in fields:
        for line_id in _evidence_lines(raw_fact, field):
            match = _LINE_ID.fullmatch(line_id)
            if match:
                pages.add(int(match.group("page")))
    return sorted(pages)


def _target_anchor(raw_fact: Mapping[str, Any], compact_page: str) -> str | None:
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", str(raw_fact.get("target") or ""))
    candidates = sorted({_compact(token) for token in tokens if len(_compact(token)) >= 2}, key=len, reverse=True)
    return next((token for token in candidates if token in compact_page), None)


def _context_quotes(lines: list[str], required_terms: list[str]) -> list[str]:
    compact_lines = [_compact(line) for line in lines]
    indexes = {
        index
        for index, line in enumerate(compact_lines)
        if any(term and term in line for term in required_terms)
    }
    expanded = set(indexes)
    for index in indexes:
        expanded.update(value for value in (index - 1, index + 1) if 0 <= value < len(lines))
    return [lines[index].strip() for index in sorted(expanded) if lines[index].strip()]


def _text_proof(
    pdf_path: Path,
    pdf_sha256: str,
    pages: list[int],
    raw_fact: Mapping[str, Any],
    rule: str,
    fact_id: str,
) -> dict[str, Any] | None:
    document = fitz.open(pdf_path)
    proofs = []
    try:
        for page in pages:
            text = document[page - 1].get_text("text")
            if not text.strip():
                return None
            compact_page = _compact(text)
            if rule == "explicit_compound_no_spend":
                match = _NO_SPEND.search(_compact(raw_fact.get("condition")))
                core = match.group(0) if match else ""
                required = [core]
            else:
                phrase = next(
                    (token for token in _FREE_POSITIVE if token in _compact(raw_fact.get("action"))),
                    "무료이용",
                )
                anchor = _target_anchor(raw_fact, compact_page)
                if anchor is None:
                    return None
                required = [phrase, anchor]
            if not all(term and term in compact_page for term in required):
                return None
            lines = [line for line in text.splitlines()]
            quotes = _context_quotes(lines, required)
            if not quotes or not all(
                quote in [line.strip() for line in lines] for quote in quotes
            ):
                return None
            proofs.append(
                {
                    "page": page,
                    "quotes": quotes,
                    "required_compact_terms": required,
                    "text_layer_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                }
            )
    finally:
        document.close()
    proof = {
        "proof_type": "pdf_text_decisive_quote",
        "fact_binding": fact_id,
        "rule_id": rule,
        "pdf_path": pdf_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "pdf_sha256": pdf_sha256,
        "pages": proofs,
        "scope": "changed derived decision only; not a full raw-field or OCR audit",
    }
    payload = json.dumps(proof, ensure_ascii=False, sort_keys=True).encode("utf-8")
    proof["proof_id"] = "proof_" + hashlib.sha256(payload).hexdigest()[:24]
    return proof


def _render_receipt(
    document_id: str,
    pdf_path: Path,
    pdf_sha256: str,
    page: int,
) -> dict[str, Any]:
    name = _IMAGE_PAGE_NAMES[(document_id, page)]
    png_path = IMAGE_DIR / name
    document = fitz.open(pdf_path)
    try:
        rendered = document[page - 1].get_pixmap(
            matrix=fitz.Matrix(2, 2), alpha=False
        ).tobytes("png")
    finally:
        document.close()
    if not png_path.is_file() or png_path.read_bytes() != rendered:
        png_path.parent.mkdir(parents=True, exist_ok=True)
        png_path.write_bytes(rendered)
    return {
        "pdf_path": pdf_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "pdf_sha256": pdf_sha256,
        "page": page,
        "png_path": png_path.relative_to(REPOSITORY_ROOT).as_posix(),
        "png_sha256": _sha256(png_path),
        "render": {"zoom": 2, "alpha": False},
    }


def _manual_receipt_covers(
    record: Mapping[str, Any], fact_id: str, rule: str
) -> bool:
    """Require a fact-and-rule-specific observation, not page-level approval."""

    if fact_id not in (record.get("fact_ids") or []):
        return False
    if rule not in (record.get("supports_rules") or []):
        return False
    return any(
        binding.get("fact_id") == fact_id
        and binding.get("rule") == rule
        and bool(str(binding.get("observation") or "").strip())
        for binding in (record.get("claim_bindings") or [])
        if isinstance(binding, Mapping)
    )


def _load_manual_reviews() -> tuple[dict[tuple[str, str, int], dict[str, Any]], list[str]]:
    path = REVIEW_DIR / "manual_image_reviews.json"
    if not path.is_file():
        return {}, []
    payload = json.loads(path.read_text(encoding="utf-8"))
    index: dict[tuple[str, str, int], dict[str, Any]] = {}
    errors = []
    for record in payload.get("records") or []:
        key = (record["pdf_path"], record["pdf_sha256"], int(record["page"]))
        pdf_path = REPOSITORY_ROOT / record["pdf_path"]
        png_path = REPOSITORY_ROOT / record["png_path"]
        if _sha256(pdf_path) != record["pdf_sha256"]:
            errors.append(f"PDF hash mismatch: {key}")
        document = fitz.open(pdf_path)
        try:
            rendered = document[int(record["page"]) - 1].get_pixmap(
                matrix=fitz.Matrix(2, 2), alpha=False
            ).tobytes("png")
        finally:
            document.close()
        rendered_sha = hashlib.sha256(rendered).hexdigest()
        if not png_path.is_file() or _sha256(png_path) != record["png_sha256"] or rendered_sha != record["png_sha256"]:
            errors.append(f"PNG rerender mismatch: {key}")
        if not record.get("reviewed_by") or not record.get("observation") or not record.get("supports_rules"):
            errors.append(f"incomplete manual review: {key}")
        fact_ids = record.get("fact_ids") or []
        supports_rules = record.get("supports_rules") or []
        expected_bindings = {
            (fact_id, rule)
            for fact_id in fact_ids
            for rule in supports_rules
        }
        observed_bindings = {
            (binding.get("fact_id"), binding.get("rule"))
            for binding in (record.get("claim_bindings") or [])
            if isinstance(binding, Mapping)
            and str(binding.get("observation") or "").strip()
        }
        if observed_bindings != expected_bindings:
            errors.append(f"manual claim binding mismatch: {key}")
        index[key] = record
    return index, errors


def _database_invariants(source_db: Path, output_db: Path) -> dict[str, Any]:
    source = sqlite3.connect(f"file:{source_db}?mode=ro&immutable=1", uri=True)
    copied = sqlite3.connect(f"file:{output_db}?mode=ro&immutable=1", uri=True)
    try:
        schema = "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        mutable = {
            "action_kind", "action_status", "spend_threshold_krw", "spend_operator",
            "spend_state", "derived_json",
        }
        fact_columns = [row[1] for row in source.execute("PRAGMA table_info(facts)")]
        immutable = [column for column in fact_columns if column not in mutable]

        def rows(connection: sqlite3.Connection, table: str, columns: list[str], order: str):
            projection = ",".join(f'"{column}"' for column in columns)
            return connection.execute(f'SELECT {projection} FROM "{table}" ORDER BY {order}').fetchall()

        alias_columns = [row[1] for row in source.execute("PRAGMA table_info(target_aliases)")]
        document_columns = [row[1] for row in source.execute("PRAGMA table_info(documents)")]
        return {
            "sqlite_schema_identical": source.execute(schema).fetchall() == copied.execute(schema).fetchall(),
            "target_alias_rows_identical": rows(source, "target_aliases", alias_columns, "rowid") == rows(copied, "target_aliases", alias_columns, "rowid"),
            "document_rows_identical": rows(source, "documents", document_columns, "document_id") == rows(copied, "documents", document_columns, "document_id"),
            "immutable_fact_rows_identical": rows(source, "facts", immutable, "document_id,ordinal") == rows(copied, "facts", immutable, "document_id,ordinal"),
            "immutable_fact_columns": immutable,
        }
    finally:
        source.close()
        copied.close()


def _metadata_value(connection: sqlite3.Connection, key: str) -> Any:
    row = connection.execute("SELECT value_json FROM metadata WHERE key=?", (key,)).fetchone()
    if row is None:
        raise RuntimeError(f"missing metadata key: {key}")
    return json.loads(row[0])


def _update_metadata(connection: sqlite3.Connection, changes: list[dict[str, Any]]) -> None:
    counts = _metadata_value(connection, "counts")
    counts.update(
        known_spend_threshold=connection.execute("SELECT count(*) FROM facts WHERE spend_state='known'").fetchone()[0],
        positive_supported_action=connection.execute("SELECT count(*) FROM facts WHERE action_status='true'").fetchone()[0],
    )
    rates = _metadata_value(connection, "support_rates")
    rates["known_spend_threshold"] = counts["known_spend_threshold"] / counts["facts"]
    rates["positive_supported_action"] = counts["positive_supported_action"] / counts["facts"]
    limitations = _metadata_value(connection, "limitations")
    additions = [
        "Stage 60 recognizes only bounded explicit compound no-spend statements; missing or temporary conditions remain unknown.",
        "Stage 60 represents explicit positive free use as action_status true with action_kind null; it does not convert free use into discount or earn.",
    ]
    limitations.extend(item for item in additions if item not in limitations)
    values = {
        "schema_version": VERSION,
        "counts": counts,
        "support_rates": rates,
        "limitations": limitations,
        "normalization60": {
            "version": VERSION,
            "input_sha256": INPUT_HASHES,
            "changed_fact_count": len(changes),
            "rule_counts": dict(sorted(Counter(rule for item in changes for rule in item["rules"]).items())),
            "changed_fact_ids": [item["fact_id"] for item in changes],
            "rule_policy": "deterministic bounded semantic rules; no card or fact identifier whitelist",
        },
    }
    for key, value in values.items():
        connection.execute(
            "INSERT INTO metadata(key,value_json) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True)),
        )


def build_db() -> dict[str, Any]:
    DB_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    input_hashes = {name: _sha256(INPUT_DIR / name) for name in INPUT_HASHES}
    if input_hashes != INPUT_HASHES:
        raise RuntimeError(f"stage-56 input hash mismatch: {input_hashes}")

    source_db = INPUT_DIR / "structured.sqlite"
    output_db = DB_OUTPUT_DIR / "structured.sqlite"
    shutil.copyfile(source_db, output_db)
    shutil.copyfile(INPUT_DIR / "documents.jsonl", DB_OUTPUT_DIR / "documents.jsonl")
    original_facts = _read_jsonl(INPUT_DIR / "facts.jsonl")
    original_by_id = {row["fact_id"]: row for row in original_facts}
    documents = {row["document_id"]: row for row in _read_jsonl(INPUT_DIR / "documents.jsonl")}
    manual_reviews, manual_errors = _load_manual_reviews()
    if manual_errors:
        raise RuntimeError(f"manual review validation failed: {manual_errors}")

    changes = []
    connection = sqlite3.connect(output_db)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        db_rows = connection.execute(
            "SELECT fact_id,document_id,ordinal,raw_json,derived_json FROM facts ORDER BY document_id,ordinal"
        ).fetchall()
        for fact_id, document_id, ordinal, raw_json, derived_json in db_rows:
            raw_fact = json.loads(raw_json)
            before = json.loads(derived_json)
            after, rules = normalize_fact(raw_fact, before)
            if not rules:
                continue
            connection.execute(
                """UPDATE facts SET action_kind=?,action_status=?,spend_threshold_krw=?,
                   spend_operator=?,spend_state=?,derived_json=? WHERE fact_id=?""",
                (
                    after.get("action_kind"), after["action_status"],
                    after.get("spend_threshold_krw"), after.get("spend_operator"),
                    after["spend_state"], json.dumps(after, ensure_ascii=False, sort_keys=True),
                    fact_id,
                ),
            )
            changes.append(
                {
                    "fact_id": fact_id,
                    "document_id": document_id,
                    "ordinal": ordinal,
                    "rules": list(rules),
                    "before": before,
                    "after": after,
                    "raw_fact": raw_fact,
                }
            )
        observed = Counter(rule for item in changes for rule in item["rules"])
        if dict(observed) != EXPECTED_RULE_COUNTS or len(changes) != sum(EXPECTED_RULE_COUNTS.values()):
            raise RuntimeError(f"unexpected change set: facts={len(changes)} rules={dict(observed)}")
        _update_metadata(connection, changes)
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

    read_connection = sqlite3.connect(f"file:{output_db}?mode=ro&immutable=1", uri=True)
    try:
        database_fact_records = {
            fact_id: (json.loads(raw_json), json.loads(derived_json))
            for fact_id, raw_json, derived_json in read_connection.execute(
                "SELECT fact_id,raw_json,derived_json FROM facts"
            )
        }
        updated_derived = {
            fact_id: derived
            for fact_id, (_, derived) in database_fact_records.items()
        }
        integrity = read_connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = read_connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            "documents": read_connection.execute("SELECT count(*) FROM documents").fetchone()[0],
            "facts": read_connection.execute("SELECT count(*) FROM facts").fetchone()[0],
            "aliases": read_connection.execute("SELECT count(*) FROM target_aliases").fetchone()[0],
        }
        scalar_mismatches = []
        for row in read_connection.execute(
            "SELECT fact_id,action_kind,action_status,spend_threshold_krw,spend_operator,spend_state,derived_json FROM facts"
        ):
            fact_id, action_kind, action_status, spend, operator, spend_state, derived_json = row
            derived = json.loads(derived_json)
            if (action_kind, action_status, spend, operator, spend_state) != (
                derived.get("action_kind"), derived["action_status"],
                derived.get("spend_threshold_krw"), derived.get("spend_operator"),
                derived["spend_state"],
            ):
                scalar_mismatches.append(fact_id)
    finally:
        read_connection.close()

    second_pass = []
    for original in original_facts:
        again, rules = normalize_fact(original["raw_fact"], updated_derived[original["fact_id"]])
        if rules or again != updated_derived[original["fact_id"]]:
            second_pass.append(original["fact_id"])

    exported_facts = []
    raw_wrapper_mismatches = []
    for original in original_facts:
        exported = copy.deepcopy(original)
        exported["derived"] = updated_derived[original["fact_id"]]
        exported_facts.append(exported)
        if {k: v for k, v in original.items() if k != "derived"} != {k: v for k, v in exported.items() if k != "derived"}:
            raw_wrapper_mismatches.append(original["fact_id"])
    _write_jsonl(DB_OUTPUT_DIR / "facts.jsonl", exported_facts)
    fact_export_mismatches = _fact_export_mismatches(
        database_fact_records, exported_facts
    )

    pending_groups: dict[tuple[str, str, int], dict[str, Any]] = {}
    proof_errors = []
    confirmed_count = 0
    for change in changes:
        rule = change["rules"][0]
        raw_fact = change["raw_fact"]
        pages = _source_pages(raw_fact, rule)
        document = documents[change["document_id"]]
        pdf_path = REPOSITORY_ROOT / "data/raw" / f"{change['document_id']}.pdf"
        pdf_sha = _sha256(pdf_path)
        if pdf_sha != document["source_pdf_sha256"]:
            raise RuntimeError(f"source PDF hash mismatch: {change['document_id']}")
        proof = _text_proof(pdf_path, pdf_sha, pages, raw_fact, rule, change["fact_id"])
        if proof is not None:
            change["source_status"] = "confirmed_pdf_text"
            change["source_proof"] = proof
            confirmed_count += 1
            continue
        receipts = []
        for page in pages:
            receipt = _render_receipt(change["document_id"], pdf_path, pdf_sha, page)
            key = (receipt["pdf_path"], receipt["pdf_sha256"], page)
            group = pending_groups.setdefault(
                key,
                {
                    **receipt,
                    "fact_ids": [],
                    "rules": [],
                    "claims_to_review": [],
                    "status": "pending_main_visual_review",
                },
            )
            group["fact_ids"].append(change["fact_id"])
            group["rules"].append(rule)
            if rule == "explicit_compound_no_spend":
                claim = "The same benefit explicitly has no previous-spend condition; other channel restrictions remain separate."
            else:
                claim = f"{raw_fact.get('target')} is explicitly a positive free-use or free-entry benefit, while its stated spend and usage conditions remain in force."
            if claim not in group["claims_to_review"]:
                group["claims_to_review"].append(claim)
            manual = manual_reviews.get(key)
            if manual and _manual_receipt_covers(manual, change["fact_id"], rule):
                receipts.append(manual["review_id"])
        if receipts and len(receipts) == len(pages):
            change["source_status"] = "confirmed_manual_image"
            change["source_proof"] = {
                "proof_type": "manual_pdf_image_review",
                "fact_binding": change["fact_id"],
                "rule_id": rule,
                "manual_review_ids": receipts,
                "scope": "changed derived decision only; not a full raw-field or OCR audit",
            }
            confirmed_count += 1
        else:
            change["source_status"] = "pending_manual_image_review"
            change["source_proof"] = None

    pending = {
        "status": "pending_main_visual_review" if confirmed_count < len(changes) else "complete",
        "records": sorted(pending_groups.values(), key=lambda item: (item["pdf_path"], item["page"])),
    }
    (REVIEW_DIR / "pending_image_reviews.json").write_text(
        json.dumps(pending, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    excluded = []
    for original in original_facts:
        raw_fact = original["raw_fact"]
        derived = original["derived"]
        condition = _compact(raw_fact.get("condition"))
        free_blob = _compact(" ".join(str(raw_fact.get(key) or "") for key in ("target", "action", "benefit_type", "value", "exceptions")))
        if _NO_SPEND.search(condition) and derived.get("spend_state") != "known":
            matched, reason = _no_spend_match(raw_fact)
            if not matched and original["fact_id"] not in {item["fact_id"] for item in changes}:
                excluded.append({"fact_id": original["fact_id"], "document_id": original["document_id"], "candidate": "no_spend", "reason": reason})
        if ("무료이용" in free_blob or "무료입장" in free_blob) and derived.get("action_status") != "true":
            matched, reason, _ = _free_match(raw_fact)
            if not matched and original["fact_id"] not in {item["fact_id"] for item in changes}:
                excluded.append({"fact_id": original["fact_id"], "document_id": original["document_id"], "candidate": "free_use", "reason": reason})

    review = {
        "version": VERSION,
        "scope": "Validates only the 71 changed derived classifications, not every raw field or the complete OCR output.",
        "changed_fact_count": len(changes),
        "rule_counts": dict(sorted(Counter(rule for item in changes for rule in item["rules"]).items())),
        "source_confirmed_count": confirmed_count,
        "source_pending_count": len(changes) - confirmed_count,
        "source_gate_passed": confirmed_count == len(changes) and not proof_errors,
        "paid_followup_allowed": False,
        "changes": changes,
        "excluded_candidates": excluded,
        "limitations": [
            "Sentence and unbounded scopes are excluded from this correction even if free wording is present.",
            "Free exchange, free enrollment, free-stay vouchers, paid memberships, and negative free-use statements are not normalized as free use.",
            "A pending image receipt means the DB row is changed by the deterministic rule but its source gate is not yet accepted.",
        ],
    }
    review_path = REVIEW_DIR / "source_review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    output_hashes = {name: _sha256(DB_OUTPUT_DIR / name) for name in INPUT_HASHES}
    input_hashes_after = {name: _sha256(INPUT_DIR / name) for name in INPUT_HASHES}
    invariants = _database_invariants(source_db, output_db)
    validation = {
        "version": VERSION,
        "input_hashes_before": input_hashes,
        "input_hashes_after": input_hashes_after,
        "input_hashes_unchanged": input_hashes == input_hashes_after == INPUT_HASHES,
        "output_hashes": output_hashes,
        "counts": counts,
        "sqlite_integrity_check": integrity,
        "foreign_key_errors": foreign_keys,
        "database_invariants": invariants,
        "changed_fact_count": len(changes),
        "changed_fact_ids_unique": len({item["fact_id"] for item in changes}) == len(changes),
        "idempotence_second_pass_changes": second_pass,
        "scalar_derived_mismatches": scalar_mismatches,
        "raw_source_or_wrapper_mismatches": raw_wrapper_mismatches,
        "sqlite_jsonl_fact_payload_mismatches": fact_export_mismatches,
        "sqlite_jsonl_fact_payloads_identical": not fact_export_mismatches,
        "jsonl_fact_ids_unchanged": set(original_by_id) == {row["fact_id"] for row in exported_facts},
        "documents_jsonl_byte_identical": _sha256(INPUT_DIR / "documents.jsonl") == _sha256(DB_OUTPUT_DIR / "documents.jsonl"),
        "source_review_sha256": _sha256(review_path),
        "manual_review_errors": manual_errors,
    }
    required_true = [
        validation["input_hashes_unchanged"],
        counts == {"documents": 106, "facts": 3403, "aliases": 22},
        integrity == "ok",
        not foreign_keys,
        all(value is True for key, value in invariants.items() if key != "immutable_fact_columns"),
        not second_pass,
        not scalar_mismatches,
        not raw_wrapper_mismatches,
        not fact_export_mismatches,
        validation["jsonl_fact_ids_unchanged"],
        validation["documents_jsonl_byte_identical"],
    ]
    if not all(required_true):
        raise RuntimeError(f"stage-60 validation failed: {validation}")
    (REVIEW_DIR / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    summary = {
        "version": VERSION,
        "status": "source_gate_passed_awaiting_main_acceptance" if review["source_gate_passed"] else "db_built_source_gate_pending",
        "changed_fact_count": len(changes),
        "rule_counts": review["rule_counts"],
        "source_confirmed_count": confirmed_count,
        "source_pending_count": len(changes) - confirmed_count,
        "pending_image_page_count": len(pending["records"]) if pending["status"] != "complete" else 0,
        "paid_followup_allowed": False,
        "counts": counts,
        "sqlite_integrity_check": integrity,
        "foreign_key_error_count": len(foreign_keys),
        "input_hashes_unchanged": validation["input_hashes_unchanged"],
        "api_cost_usd": 0,
    }
    RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    print(json.dumps(build_db(), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
