"""Offline JSON/CSV evaluator for notebook 12 runs."""

from __future__ import annotations

import csv
import json
import os
import re
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rapidfuzz.distance import Levenshtein


ROOT = Path(__file__).resolve().parents[1]
CRITICAL_RULES = ROOT / "data/ocr_benchmark/gold/critical_rules/critical_rules_v2.json"
PAGE_MARKER = re.compile(r"^\[page\s*(\d+)\]\s*$", re.IGNORECASE | re.MULTILINE)
PANEL_MARKER = re.compile(r"^\[(?:left|center|right)_panel\]\s*$", re.IGNORECASE | re.MULTILINE)
NUMERIC_TOKEN = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?(?:%|원|만원|천원|개월|년|회|점|km|마일)?")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


def atomic_write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(dict.fromkeys(key for row in rows for key in row)) or ["status"]
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=columns)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value for key, value in row.items()})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        if temporary:
            temporary.unlink(missing_ok=True)
        raise


def normalize(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return " ".join(unicodedata.normalize("NFKC", value).split())


def strip_synthetic_markers(value: str) -> str:
    return normalize(PANEL_MARKER.sub("", PAGE_MARKER.sub("", value)))


def parse_pages(value: str) -> dict[int, str]:
    matches = list(PAGE_MARKER.finditer(value))
    if not matches:
        return {1: strip_synthetic_markers(value)}
    pages = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        pages[int(match.group(1))] = strip_synthetic_markers(value[match.end():end])
    return pages


def parse_pages_preserving_lines(value: str) -> dict[int, str]:
    """Split gold TXT by page without destroying lines needed by excerpt markers."""
    value = unicodedata.normalize("NFKC", value)
    matches = list(PAGE_MARKER.finditer(value))
    if not matches:
        return {1: PANEL_MARKER.sub("", value)}
    pages = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        pages[int(match.group(1))] = PANEL_MARKER.sub("", value[match.end():end])
    return pages


def edit_distance(reference: str, prediction: str) -> int:
    return Levenshtein.distance(reference, prediction)


def multiset_scores(reference: list[str], prediction: list[str]) -> dict[str, float | int]:
    ref, pred = Counter(reference), Counter(prediction)
    matches = sum((ref & pred).values())
    precision = matches / sum(pred.values()) if pred else 0.0
    recall = matches / sum(ref.values()) if ref else 0.0
    return {
        "matches": matches,
        "reference_count": sum(ref.values()),
        "prediction_count": sum(pred.values()),
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
    }


def text_scores(reference: str, prediction: str) -> dict[str, Any]:
    reference, prediction = normalize(reference), normalize(prediction)
    distance = edit_distance(reference, prediction)
    tokens = multiset_scores(reference.split(), prediction.split())
    numbers = multiset_scores(NUMERIC_TOKEN.findall(reference), NUMERIC_TOKEN.findall(prediction))
    return {
        "reference_chars": len(reference),
        "prediction_chars": len(prediction),
        "edit_distance": distance,
        "normalized_edit_distance": distance / max(1, len(reference)),
        "cer": distance / max(1, len(reference)),
        "token_precision": tokens["precision"],
        "token_recall": tokens["recall"],
        "token_f1": tokens["f1"],
        "token_matches": tokens["matches"],
        "reference_tokens": tokens["reference_count"],
        "prediction_tokens": tokens["prediction_count"],
        "numeric_token_exact": NUMERIC_TOKEN.findall(reference) == NUMERIC_TOKEN.findall(prediction),
        "numeric_token_f1": numbers["f1"],
        "numeric_matches": numbers["matches"],
        "reference_numeric_tokens": numbers["reference_count"],
        "prediction_numeric_tokens": numbers["prediction_count"],
    }


def safe_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def structure_schema_valid(result: Any, gold: dict[str, Any]) -> bool:
    if not isinstance(result, dict) or set(result) != {"field_labels", "numeric_labels", "table_labels"}:
        return False
    for kind in result:
        labels = result[kind]
        expected = {label["id"]: label for label in gold.get(kind, [])}
        if not isinstance(labels, dict) or set(labels) != set(expected):
            return False
        if kind == "numeric_labels" and any(
            not isinstance(value, dict) or set(value) != {"surface_text", "normalized_value", "unit"}
            for value in labels.values()
        ):
            return False
        if kind == "table_labels":
            for label_id, value in labels.items():
                if value is None:
                    continue
                columns = len(expected[label_id].get("headers", []))
                if not isinstance(value, dict) or set(value) != {"headers", "rows"}:
                    return False
                if len(value.get("headers", [])) != columns or any(len(row) != columns for row in value.get("rows", [])):
                    return False
    return True


def validate_normalized_pages(document: dict[str, Any], expected_pages: int) -> list[dict[str, Any]]:
    pages = document.get("pages")
    if document.get("schema_version") != "normalized_ocr_v2" or not isinstance(pages, list):
        raise ValueError("normalized_ocr_v2 문서가 아닙니다.")
    numbers = [page.get("page_num") for page in pages]
    if numbers != list(range(1, expected_pages + 1)) or len(numbers) != len(set(numbers)):
        raise ValueError(f"normalized page 연속/중복/개수 오류: expected={expected_pages}, actual={numbers}")
    if any(not isinstance(page.get("text"), str) for page in pages):
        raise TypeError("normalized pages[].text는 문자열이어야 합니다.")
    return pages


def coverage_scope(card: dict[str, Any], coverage_policy: dict[str, Any]) -> tuple[str, bool]:
    policy = coverage_policy.get(card["key"])
    if not isinstance(policy, dict):
        raise ValueError(f"manifest coverage policy 누락: {card['key']}")
    scope = policy.get("annotation_scope", "unknown")
    official = policy.get("full_page_cer") == "included"
    if policy.get("full_page_cer") == "excluded_until_visual_audit":
        scope = "candidate_unapproved"
    return scope, official


def selected_excerpt_pair(
    gold_pages: dict[int, str], candidate_pages: dict[int, str], gold_structured: dict[str, Any]
) -> dict[str, Any]:
    labels = gold_structured.get("text_labels")
    if not isinstance(labels, list) or len(labels) != 1:
        return {"status": "invalid_excerpt_contract", "reference": None, "prediction": None}
    label = labels[0]
    page_num = label.get("page_num")
    marker = normalize(label.get("raw_start_marker"))
    if not isinstance(page_num, int) or not marker or page_num not in gold_pages:
        return {"status": "invalid_excerpt_contract", "reference": None, "prediction": None}
    marker_key = re.sub(r"^#{1,6}\s+", "", marker)

    def excerpt(page_text: str) -> str | None:
        lines = [line for line in page_text.splitlines() if not normalize(line).startswith("```")]
        for index, line in enumerate(lines):
            line_key = re.sub(r"^#{1,6}\s+", "", normalize(line))
            if line_key == marker_key:
                lines[index] = marker_key
                return normalize("\n".join(lines[index:]))
        return None

    reference = excerpt(gold_pages[page_num])
    if reference is None:
        return {"status": "gold_missing_marker", "page_num": page_num, "marker": marker, "reference": None, "prediction": None}
    prediction = excerpt(candidate_pages.get(page_num, ""))
    if prediction is None:
        return {"status": "missing_marker", "page_num": page_num, "marker": marker, "reference": None, "prediction": None}
    return {
        "status": "evaluated",
        "page_num": page_num,
        "marker": marker,
        "reference": reference,
        "prediction": prediction,
    }


def usage_totals(entries: list[dict[str, Any]]) -> dict[str, int | float]:
    totals: dict[str, int | float] = defaultdict(int)

    def collect(value: Any, prefix: str = "") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, f"{prefix}.{key}" if prefix else key)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            totals[prefix] += value

    for entry in entries:
        collect(entry)
    return dict(totals)


def integrity_rows(run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for engine in engines:
        total_pages = empty_pages = normalized_valid = completed = structure_valid = 0
        ocr_models, ocr_usage, structure_models, structure_usage = set(), [], set(), []
        for card in cards:
            normalized_path = run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.json"
            document = safe_json(normalized_path)
            try:
                pages = validate_normalized_pages(document or {}, card["page_count"])
                valid = True
            except (TypeError, ValueError):
                pages, valid = [], False
            normalized_valid += int(valid)
            total_pages += len(pages)
            empty_pages += sum(not normalize(page.get("text")) for page in pages)
            if engine == "upstage":
                raw_paths = [run_root / "raw" / engine / card["issuer"] / f"{card['card_name']}.json"]
            else:
                raw_paths = sorted((run_root / "raw" / engine / card["issuer"] / card["card_name"]).glob("page_*.json"))
            raw_entries = [safe_json(path) for path in raw_paths]
            expected_raw = card["page_count"] if engine != "upstage" else 1
            completed += int(len(raw_entries) == expected_raw and all(entry and entry.get("status") == "succeeded" for entry in raw_entries))
            for entry in raw_entries:
                if entry:
                    ocr_models.add(entry.get("effective_model") or entry.get("requested_model"))
                    if entry.get("usage") is not None:
                        ocr_usage.append(entry["usage"])
            struct_raw = safe_json(run_root / "raw/field_extraction" / engine / card["issuer"] / f"{card['card_name']}.json")
            struct_result = safe_json(run_root / "structured" / engine / card["issuer"] / f"{card['card_name']}.json")
            gold = safe_json(ROOT / "data/ocr_benchmark/gold/structured" / card["issuer"] / f"{card['card_name']}.json") or {}
            structure_valid += int(bool(
                struct_raw and struct_raw.get("status") == "succeeded" and struct_result
                and struct_raw.get("request_fingerprint") == struct_result.get("request_fingerprint")
                and struct_raw.get("normalized_sha256") == struct_result.get("normalized_sha256")
                and structure_schema_valid(struct_result.get("result"), gold)
            ))
            if struct_raw:
                structure_models.update(struct_raw.get("effective_models") or [struct_raw.get("requested_model")])
                structure_usage.extend(part.get("usage") for part in struct_raw.get("responses", []) if part.get("usage") is not None)
        rows.append(
            {
                "engine": engine,
                "expected_pages": sum(card["page_count"] for card in cards),
                "actual_pages": total_pages,
                "empty_pages": empty_pages,
                "empty_rate": empty_pages / max(1, total_pages),
                "normalized_schema_valid_documents": normalized_valid,
                "ocr_completed_documents": completed,
                "structure_schema_valid_documents": structure_valid,
                "structure_completed_documents": structure_valid,
                "ocr_models": sorted(model for model in ocr_models if model),
                "ocr_usage": usage_totals(ocr_usage),
                "structure_models": sorted(model for model in structure_models if model),
                "structure_usage": usage_totals(structure_usage),
            }
        )
    return rows


def text_metric_rows(
    run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]], coverage_policy: dict[str, Any]
) -> list[dict[str, Any]]:
    rows = []
    for engine in engines:
        for card in cards:
            scope, official = coverage_scope(card, coverage_policy)
            gold_path = ROOT / "data/ocr_benchmark/gold/raw" / card["issuer"] / f"{card['card_name']}.txt"
            normalized_path = run_root / "normalized" / engine / card["issuer"] / f"{card['card_name']}.json"
            document = safe_json(normalized_path)
            if document is None:
                rows.append({"engine": engine, "issuer": card["issuer"], "card_name": card["card_name"], "coverage_status": scope, "status": "missing"})
                continue
            try:
                pages = validate_normalized_pages(document, card["page_count"])
            except (TypeError, ValueError) as error:
                rows.append({"engine": engine, "issuer": card["issuer"], "card_name": card["card_name"], "coverage_status": scope, "status": f"invalid_normalized:{error}"})
                continue
            gold_pages = parse_pages(gold_path.read_text(encoding="utf-8"))
            pred_pages = {page["page_num"]: strip_synthetic_markers(page["text"]) for page in pages}
            txt_path = normalized_path.with_suffix(".txt")
            txt_reproduces_json = txt_path.is_file() and parse_pages(txt_path.read_text(encoding="utf-8")) == pred_pages
            excerpt = None
            if scope == "selected_excerpt":
                gold_structured = safe_json(
                    ROOT / "data/ocr_benchmark/gold/structured" / card["issuer"] / f"{card['card_name']}.json"
                ) or {}
                excerpt = selected_excerpt_pair(
                    parse_pages_preserving_lines(gold_path.read_text(encoding="utf-8")),
                    {
                        page["page_num"]: PANEL_MARKER.sub("", unicodedata.normalize("NFKC", page["text"]))
                        for page in pages
                    },
                    gold_structured,
                )
                card_scores = (
                    text_scores(excerpt["reference"], excerpt["prediction"])
                    if excerpt["status"] == "evaluated" else {}
                )
            else:
                card_scores = text_scores("\n".join(gold_pages.values()), "\n".join(pred_pages.get(page, "") for page in gold_pages))
            rows.append(
                {
                    "engine": engine,
                    "issuer": card["issuer"],
                    "card_name": card["card_name"],
                    "coverage_status": scope,
                    "official_aggregate_eligible": official,
                    "preview_aggregate_eligible": scope == "candidate_unapproved",
                    "txt_reproduces_normalized_json": txt_reproduces_json,
                    "level": "card",
                    "status": (
                        excerpt["status"] if excerpt is not None
                        else "evaluated" if scope != "incomplete_or_ambiguous" else "excluded"
                    ),
                    "excerpt_page_num": excerpt.get("page_num") if excerpt else None,
                    "excerpt_start_marker": excerpt.get("marker") if excerpt else None,
                    "excerpt_contract": "same-page line exact after NFKC/whitespace and leading Markdown heading removal; fence delimiters ignored" if excerpt else None,
                    **({} if scope == "incomplete_or_ambiguous" else card_scores),
                }
            )
            if official or scope == "candidate_unapproved":
                for page_num, reference in gold_pages.items():
                    rows.append(
                        {
                            "engine": engine,
                            "issuer": card["issuer"],
                            "card_name": card["card_name"],
                            "coverage_status": scope,
                            "official_aggregate_eligible": official,
                            "preview_aggregate_eligible": scope == "candidate_unapproved",
                            "level": "page",
                            "page_num": page_num,
                            "status": "evaluated",
                            **text_scores(reference, pred_pages.get(page_num, "")),
                        }
                    )
    return rows


def same(expected: Any, actual: Any) -> bool:
    return normalize(expected) == normalize(actual)


def structured_rows(
    run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]], metrics_available: bool
) -> list[dict[str, Any]]:
    rows = []
    for engine in engines:
        for card in cards:
            gold = safe_json(ROOT / "data/ocr_benchmark/gold/structured" / card["issuer"] / f"{card['card_name']}.json") or {}
            structured = safe_json(run_root / "structured" / engine / card["issuer"] / f"{card['card_name']}.json")
            result = structured.get("result", {}) if structured else {}
            for kind in ("field_labels", "numeric_labels", "table_labels"):
                predictions = result.get(kind, {}) if isinstance(result.get(kind), dict) else {}
                for label in gold.get(kind, []):
                    label_id = label["id"]
                    present = label_id in predictions
                    prediction = predictions.get(label_id)
                    row = {
                        "engine": engine,
                        "issuer": card["issuer"],
                        "card_name": card["card_name"],
                        "label_kind": kind,
                        "label_id": label_id,
                        "present": present,
                        "null": present and prediction is None,
                        "status": "evaluated" if structured else "missing_structure",
                        "metric_status": "available" if metrics_available else "not_available",
                    }
                    if kind == "field_labels":
                        row["field_value_exact"] = present and prediction is not None and same(label.get("value"), prediction)
                    elif kind == "numeric_labels":
                        valid_prediction = present and isinstance(prediction, dict)
                        prediction = prediction if valid_prediction else {}
                        row.update(
                            {
                                "numeric_normalized_value_exact": valid_prediction and same(label.get("normalized_value"), prediction.get("normalized_value")),
                                "numeric_surface_exact": valid_prediction and same(label.get("surface_text"), prediction.get("surface_text")),
                                "numeric_unit_exact": valid_prediction and same(label.get("unit"), prediction.get("unit")),
                            }
                        )
                    else:
                        valid_prediction = present and isinstance(prediction, dict)
                        prediction = prediction if valid_prediction else {}
                        row.update(
                            {
                                "table_header_exact": valid_prediction and same(label.get("headers"), prediction.get("headers")),
                                "table_rows_exact": valid_prediction and same(label.get("rows"), prediction.get("rows")),
                            }
                        )
                    rows.append(row)
                    if not metrics_available:
                        for key in list(row):
                            if key.endswith("_exact"):
                                row[key] = None
    return rows


def nested_get(value: Any, path: list[Any]) -> tuple[bool, Any]:
    current = value
    for key in path:
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and isinstance(key, int) and 0 <= key < len(current):
            current = current[key]
        else:
            return False, None
    return True, current


def value_matches(expected: Any, actual: Any, membership: bool = False) -> bool:
    if membership and isinstance(actual, (list, tuple, set)):
        return any(same(expected, item) for item in actual)
    return same(expected, actual)


def numeric_cell_matches(expected: Any, cell: Any) -> bool:
    if same(expected, cell):
        return True
    if not isinstance(expected, (int, float)) or isinstance(expected, bool):
        return False
    text = normalize(cell).replace(",", "")
    matches = re.findall(r"[-+]?\d+(?:\.\d+)?", text)
    values = []
    for token in matches:
        value = float(token)
        if "만" in text:
            value *= 10000
        elif "천" in text:
            value *= 1000
        if "%" in text:
            value /= 100
        values.append(value)
    return any(float(expected) == value for value in values)


def recursive_key_values(value: Any, key: str) -> list[Any]:
    found = []
    if isinstance(value, dict):
        for item_key, item in value.items():
            if item_key == key:
                found.append(item)
            found.extend(recursive_key_values(item, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(recursive_key_values(item, key))
    return found


def project_field_fact(fact: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    source = fact["source"]
    fact_id = source["v1_fact_id"]
    if fact_id.startswith("field__"):
        label_id = fact_id[len("field__"):]
        labels = result.get("field_labels", {})
        root = labels.get(label_id) if isinstance(labels, dict) else None
        mapped, value = nested_get(root, source.get("prediction_path") or [])
        return {"mapped": label_id in labels and mapped, "value": value, "root": root, "unit_mapped": False, "unit": None}
    prefix = "numeric__" if fact_id.startswith("numeric__") else "supplementary_numeric__"
    label_id = fact_id[len(prefix):]
    labels = result.get("numeric_labels", {})
    label = labels.get(label_id) if isinstance(labels, dict) else None
    if not isinstance(label, dict):
        return {"mapped": False, "value": None, "root": None, "unit_mapped": False, "unit": None}
    path = list(source.get("prediction_path") or [])
    if path and path[0] == "value":
        path = path[1:]
    mapped, value = nested_get(label.get("normalized_value"), path)
    return {"mapped": mapped, "value": value, "root": label, "unit_mapped": label.get("unit") is not None, "unit": label.get("unit")}


def diagnose_table_fact(fact: dict[str, Any], result: dict[str, Any], gold: dict[str, Any]) -> dict[str, Any]:
    """Heuristic only: never use this result as a relation-accuracy pass."""
    tables = result.get("table_labels", {})
    if not isinstance(tables, dict):
        return {"available": False, "value_exact": False, "condition_exact": False, "table_id": None}
    page_num = fact["source"].get("page_num")
    field_id = fact["source"]["v1_fact_id"].removeprefix("field__")
    field_tokens = set(field_id.split("_"))
    candidates = []
    for label in gold.get("table_labels", []):
        if label.get("page_num") != page_num or not isinstance(tables.get(label["id"]), dict):
            continue
        overlap = len(field_tokens & set(label["id"].split("_")))
        candidates.append((overlap, label["id"], tables[label["id"]]))
    if not candidates:
        return {"available": False, "value_exact": False, "condition_exact": False, "table_id": None}
    best = max(item[0] for item in candidates)
    candidates = [item for item in candidates if item[0] == best]
    index_match = re.search(r"\[(\d+)\]$", fact["relation"].get("attribute_path", ""))
    row_index = int(index_match.group(1)) if index_match else None
    expected = fact["expected"].get("value")
    condition_results = []
    value_exact = False
    chosen_id = None
    for _, table_id, table in candidates:
        rows = table.get("rows", [])
        selected_rows = [rows[row_index]] if row_index is not None and row_index < len(rows) else rows
        for row in selected_rows:
            if any(numeric_cell_matches(expected, cell) for cell in row):
                value_exact, chosen_id = True, table_id
            condition_results.append(all(
                any(numeric_cell_matches(condition.get("value"), cell) for cell in row)
                for condition in fact.get("conditions", [])
            ))
    return {
        "available": True,
        "value_exact": value_exact,
        "condition_exact": any(condition_results) if fact.get("conditions") and condition_results else not fact.get("conditions"),
        "table_id": chosen_id or candidates[0][1],
    }


def explicit_table_projection(fact: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    locator = fact.get("source", {}).get("table_locator")
    required = {"table_id", "row_index", "column_index"}
    if not isinstance(locator, dict) or not required <= set(locator):
        return {"supported": False, "mapped": False, "value": None, "condition_exact": None}
    tables = result.get("table_labels", {})
    table = tables.get(locator["table_id"]) if isinstance(tables, dict) else None
    try:
        row = table["rows"][int(locator["row_index"])]
        value = row[int(locator["column_index"])]
    except (IndexError, KeyError, TypeError, ValueError):
        return {"supported": True, "mapped": False, "value": None, "condition_exact": False}
    conditions = fact.get("conditions", [])
    condition_exact = all(any(numeric_cell_matches(condition.get("value"), cell) for cell in row) for condition in conditions)
    return {"supported": True, "mapped": True, "value": value, "condition_exact": condition_exact}


def conditions_match(fact: dict[str, Any], root: Any) -> bool:
    conditions = fact.get("conditions", [])
    if not conditions:
        return True
    for condition in conditions:
        values = recursive_key_values(root, condition["field"])
        if not any(value_matches(condition.get("value"), value, isinstance(value, list)) for value in values):
            return False
    return True


def fact_triage(fact: dict[str, Any], normalized: dict[str, Any], value_exact: bool) -> str:
    source = fact["source"]
    page_num = source.get("page_num")
    page = next((item for item in normalized.get("pages", []) if item.get("page_num") == page_num), {})
    text = normalize(page.get("text"))
    evidence = [source.get("surface_text"), *(source.get("context_terms") or [])]
    available = [normalize(item) in text for item in evidence if normalize(item)]
    if value_exact:
        return "needs_review"
    if available and all(available):
        return "structure_origin_candidate"
    if available and not any(available):
        return "ocr_origin_candidate"
    return "needs_review"


def critical_rows(
    run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]], metrics_available: bool
) -> list[dict[str, Any]]:
    rules = json.loads(CRITICAL_RULES.read_text(encoding="utf-8"))
    selected = {(card["issuer"], card["card_name"]) for card in cards}
    rows = []
    for engine in engines:
        for rule_card in rules["cards"]:
            key = (rule_card["issuer"], rule_card["card_name"])
            if key not in selected:
                continue
            structured = safe_json(run_root / "structured" / engine / key[0] / f"{key[1]}.json") or {}
            normalized = safe_json(run_root / "normalized" / engine / key[0] / f"{key[1]}.json") or {}
            result = structured.get("result", {})
            gold = safe_json(ROOT / "data/ocr_benchmark/gold/structured" / key[0] / f"{key[1]}.json") or {}
            for fact in rule_card["facts"]:
                projected = project_field_fact(fact, result)
                membership = fact["relation"].get("match_mode") == "membership"
                expected = fact["expected"]
                source_fact_id = fact["source"].get("v1_fact_id", "")
                numeric_source_fact = source_fact_id.startswith(("numeric__", "supplementary_numeric__"))
                expected_unit_contract = numeric_source_fact and expected.get("unit") is not None
                field_value_exact = projected["mapped"] and value_matches(expected.get("value"), projected["value"], membership)
                condition_exact = conditions_match(fact, projected.get("root"))
                is_table_row = fact["relation"]["group_type"] == "table_row"
                table_diagnostic = diagnose_table_fact(fact, result, gold) if is_table_row else None
                explicit_table = explicit_table_projection(fact, result) if is_table_row else None
                relation_scoring_supported = bool(
                    fact["source"].get("prediction_supported", False)
                    and (not is_table_row or explicit_table["supported"])
                )
                if is_table_row and explicit_table["supported"]:
                    value_exact = explicit_table["mapped"] and value_matches(expected.get("value"), explicit_table["value"])
                    condition_exact = explicit_table["condition_exact"]
                elif is_table_row:
                    value_exact = condition_exact = None
                else:
                    value_exact = field_value_exact
                unit_exact = bool(
                    expected_unit_contract and projected["unit_mapped"] and same(expected.get("unit"), projected["unit"])
                )
                atomic_exact = (
                    bool(value_exact) and bool(condition_exact) and (unit_exact if expected_unit_contract else True)
                    if relation_scoring_supported else None
                )
                if not metrics_available:
                    value_exact = unit_exact = atomic_exact = condition_exact = None
                rows.append(
                    {
                        "engine": engine,
                        "issuer": key[0],
                        "card_name": key[1],
                        "fact_id": fact["fact_id"],
                        "benefit_or_fee_id": fact["benefit_or_fee_id"],
                        "group_type": fact["relation"]["group_type"],
                        "prediction_supported": fact["source"].get("prediction_supported", False),
                        "match_mode": fact["relation"].get("match_mode"),
                        "expected_value_is_numeric": isinstance(expected.get("value"), (int, float)) and not isinstance(expected.get("value"), bool),
                        "mapped": projected["mapped"] or bool(explicit_table and explicit_table["mapped"]),
                        "source_prediction_supported": fact["source"].get("prediction_supported", False),
                        "relation_scoring_supported": relation_scoring_supported,
                        "relation_scoring_status": (
                            "scorable" if relation_scoring_supported
                            else "needs_review_missing_explicit_table_locator" if is_table_row
                            else "source_prediction_unsupported"
                        ),
                        "missing": (not projected["mapped"] and not bool(explicit_table and explicit_table["mapped"])),
                        "atomic_relation_exact": atomic_exact,
                        "numeric_value_accuracy": value_exact,
                        "condition_exact": condition_exact,
                        "numeric_source_fact": numeric_source_fact,
                        "expected_unit_contract": expected_unit_contract,
                        "unit_mapped": projected["unit_mapped"],
                        "unit_comparison_eligible": bool(
                            relation_scoring_supported and expected_unit_contract
                        ),
                        "unit_accuracy": unit_exact,
                        "explicit_table_locator_available": bool(explicit_table and explicit_table["supported"]),
                        "diagnostic_heuristic_table_projection_available": table_diagnostic["available"] if table_diagnostic else False,
                        "diagnostic_heuristic_table_value_match": table_diagnostic["value_exact"] if table_diagnostic else None,
                        "diagnostic_heuristic_table_id": table_diagnostic["table_id"] if table_diagnostic else None,
                        "field_fallback_value_exact": field_value_exact if metrics_available else None,
                        "unsafe_mismatch": (not fact["source"].get("prediction_supported", False) and projected["mapped"] and not atomic_exact) if metrics_available else None,
                        "attribution_triage": (
                            "needs_review" if is_table_row and not relation_scoring_supported
                            else fact_triage(fact, normalized, bool(value_exact)) if metrics_available else "needs_review"
                        ),
                        "triage_is_final": False,
                        "metric_status": "available" if metrics_available else "not_available",
                    }
                )
    group_rows: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        group_rows[(row["engine"], row["issuer"], row["card_name"], row["benefit_or_fee_id"])].append(row)
    group_exact = {}
    for key, items in group_rows.items():
        supported = [item for item in items if item["source_prediction_supported"]]
        group_exact[key] = (
            all(item["atomic_relation_exact"] for item in supported)
            if metrics_available and supported and all(item["relation_scoring_supported"] for item in supported)
            else None
        )
    for row in rows:
        key = (row["engine"], row["issuer"], row["card_name"], row["benefit_or_fee_id"])
        row["relation_group_exact"] = group_exact[key]
        row["table_row_exact"] = None
    return rows


def ratio(rows: list[dict[str, Any]], key: str, predicate: Any = None) -> dict[str, Any]:
    eligible = [row for row in rows if predicate(row)] if predicate else rows
    values = [bool(row.get(key)) for row in eligible]
    return {"correct": sum(values), "denominator": len(values), "accuracy": sum(values) / len(values) if values else None}


def text_aggregate(rows: list[dict[str, Any]], eligibility_key: str) -> dict[str, Any]:
    text_cards = [row for row in rows if row.get("level") == "card" and row.get(eligibility_key) and row.get("status") == "evaluated"]
    if not text_cards:
        return {"card_engine_rows": 0, "status": "not_available", "macro_cer": None, "micro_cer": None,
                "macro_token_f1": None, "micro_token_precision": None, "micro_token_recall": None,
                "micro_token_f1": None, "macro_numeric_token_f1": None, "micro_numeric_token_precision": None,
                "micro_numeric_token_recall": None, "micro_numeric_token_f1": None, "numeric_token_exact_cards": None}
    micro_ref = sum(row["reference_chars"] for row in text_cards)
    micro_edit = sum(row["edit_distance"] for row in text_cards)
    token_matches = sum(row["token_matches"] for row in text_cards)
    ref_tokens = sum(row["reference_tokens"] for row in text_cards)
    pred_tokens = sum(row["prediction_tokens"] for row in text_cards)
    numeric_matches = sum(row["numeric_matches"] for row in text_cards)
    ref_numeric = sum(row["reference_numeric_tokens"] for row in text_cards)
    pred_numeric = sum(row["prediction_numeric_tokens"] for row in text_cards)
    token_precision = token_matches / pred_tokens if pred_tokens else 0.0
    token_recall = token_matches / ref_tokens if ref_tokens else 0.0
    numeric_precision = numeric_matches / pred_numeric if pred_numeric else 0.0
    numeric_recall = numeric_matches / ref_numeric if ref_numeric else 0.0
    return {
        "card_engine_rows": len(text_cards),
        "macro_cer": sum(row["cer"] for row in text_cards) / len(text_cards) if text_cards else None,
        "micro_cer": micro_edit / micro_ref if micro_ref else None,
        "macro_token_f1": sum(row["token_f1"] for row in text_cards) / len(text_cards) if text_cards else None,
        "micro_token_precision": token_precision,
        "micro_token_recall": token_recall,
        "micro_token_f1": 2 * token_precision * token_recall / (token_precision + token_recall) if token_precision + token_recall else 0.0,
        "macro_numeric_token_f1": sum(row["numeric_token_f1"] for row in text_cards) / len(text_cards) if text_cards else None,
        "micro_numeric_token_precision": numeric_precision,
        "micro_numeric_token_recall": numeric_recall,
        "micro_numeric_token_f1": 2 * numeric_precision * numeric_recall / (numeric_precision + numeric_recall) if numeric_precision + numeric_recall else 0.0,
        "numeric_token_exact_cards": sum(row["numeric_token_exact"] for row in text_cards),
    }


STRUCTURED_METRICS = (
    "present", "null", "field_value_exact", "numeric_normalized_value_exact", "numeric_surface_exact",
    "numeric_unit_exact", "table_header_exact", "table_rows_exact",
)


def summarize_structured(rows: list[dict[str, Any]], metrics_available: bool) -> dict[str, Any]:
    summary = {
        key: ratio(rows, key, lambda row, metric=key: metrics_available and metric in row and row[metric] is not None)
        for key in STRUCTURED_METRICS
    }
    cards: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cards[(row["engine"], row["issuer"], row["card_name"])].append(row)
    for metric in STRUCTURED_METRICS:
        card_scores = []
        for items in cards.values():
            eligible = [item for item in items if metrics_available and metric in item and item[metric] is not None]
            if eligible:
                card_scores.append(sum(bool(item[metric]) for item in eligible) / len(eligible))
        summary[f"card_macro_{metric}"] = sum(card_scores) / len(card_scores) if card_scores else None
    return summary


def summarize_critical(rows: list[dict[str, Any]], metrics_available: bool) -> dict[str, Any]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["engine"], row["issuer"], row["card_name"], row["benefit_or_fee_id"])].append(row)
    scorable_groups = []
    if metrics_available:
        for items in groups.values():
            source_supported = [row for row in items if row["source_prediction_supported"]]
            if source_supported and all(row["relation_scoring_supported"] for row in source_supported):
                scorable_groups.append(source_supported)
    table_rows = [row for row in rows if row["group_type"] == "table_row"]
    engine_count = max(1, len({row["engine"] for row in rows}))
    return {
        "audit_fact_rows": len(rows),
        "audit_facts_per_engine": len(rows) // engine_count,
        "source_prediction_supported_fact_rows": sum(row["source_prediction_supported"] for row in rows),
        "relation_scoring_supported_fact_rows": sum(row["relation_scoring_supported"] for row in rows),
        "unscorable_table_row_fact_rows": sum(
            row["source_prediction_supported"] and row["group_type"] == "table_row" and not row["relation_scoring_supported"]
            for row in rows
        ),
        "mappable_audit_fact_rows": sum(row["mapped"] for row in rows),
        "mappable_relation_scoring_supported_fact_rows": sum(row["mapped"] and row["relation_scoring_supported"] for row in rows),
        "atomic_relation_exact_scorable": ratio(rows, "atomic_relation_exact", lambda row: metrics_available and row["relation_scoring_supported"]),
        "numeric_value_accuracy_scorable": ratio(rows, "numeric_value_accuracy", lambda row: metrics_available and row["relation_scoring_supported"] and row["expected_value_is_numeric"]),
        "unit_accuracy_scorable": ratio(rows, "unit_accuracy", lambda row: metrics_available and row["unit_comparison_eligible"]),
        "table_row_relation_accuracy": {
            "status": "not_available", "correct": 0, "denominator": 0, "accuracy": None,
            "reason": "critical v2 table_row facts have no explicit table_id/row_index/column_index locator",
        },
        "diagnostic_heuristic_table_projection": {
            "observed_rows": sum(row["diagnostic_heuristic_table_projection_available"] for row in table_rows),
            "value_match_rows": sum(bool(row["diagnostic_heuristic_table_value_match"]) for row in table_rows),
            "is_pass_metric": False,
        },
        "diagnostic_field_fallback_table_rows": {
            "observed_rows": sum(row["mapped"] for row in table_rows),
            "value_match_rows": sum(bool(row["field_fallback_value_exact"]) for row in table_rows) if metrics_available else None,
            "is_pass_metric": False,
        },
        "relation_group_exact_scorable": {
            "correct": sum(all(row["atomic_relation_exact"] for row in items) for items in scorable_groups),
            "denominator": len(scorable_groups),
            "accuracy": sum(all(row["atomic_relation_exact"] for row in items) for items in scorable_groups) / len(scorable_groups) if scorable_groups else None,
        },
        "missing": sum(row["missing"] for row in rows) if metrics_available else None,
        "unsafe_mismatch": sum(bool(row["unsafe_mismatch"]) for row in rows) if metrics_available else None,
        "metrics_status": "available" if metrics_available else "not_available",
        "denominator_note": "Unit accuracy uses every relation-scorable numeric__/supplementary_numeric__ source fact with an expected-unit contract; a missing prediction unit is incorrect, not excluded. Field-source canonical types do not require a separate unit prediction.",
    }


def summaries(
    integrity: list[dict[str, Any]], text: list[dict[str, Any]], structured: list[dict[str, Any]],
    critical: list[dict[str, Any]], metrics_available: bool,
) -> dict[str, Any]:
    text_summary = {
        "official": text_aggregate(text, "official_aggregate_eligible"),
        "candidate_unapproved_preview": text_aggregate(text, "preview_aggregate_eligible"),
        "per_engine": {
            engine: {
                "official": text_aggregate([row for row in text if row["engine"] == engine], "official_aggregate_eligible"),
                "candidate_unapproved_preview": text_aggregate(
                    [row for row in text if row["engine"] == engine], "preview_aggregate_eligible"
                ),
            }
            for engine in sorted({row["engine"] for row in text})
        },
        "coverage_policy": "Manifest excluded_until_visual_audit rows are preview-only; selected excerpt and ambiguous remain separate.",
    }
    structured_summary = summarize_structured(structured, metrics_available)
    structured_summary["per_engine"] = {
        engine: summarize_structured([row for row in structured if row["engine"] == engine], metrics_available)
        for engine in sorted({row["engine"] for row in structured})
    }
    critical_summary = summarize_critical(critical, metrics_available)
    critical_summary["per_engine"] = {
        engine: summarize_critical([row for row in critical if row["engine"] == engine], metrics_available)
        for engine in sorted({row["engine"] for row in critical})
    }
    return {"integrity": integrity, "text": text_summary, "structured": structured_summary, "critical": critical_summary}


def evaluate_run(run_root: Path, engines: tuple[str, ...], cards: list[dict[str, Any]]) -> dict[str, Any]:
    evaluated_root = run_root / "evaluated"
    manifest = safe_json(run_root / "run_manifest.json") or {}
    integrity = integrity_rows(run_root, engines, cards)
    structured_available = sum(row["structure_completed_documents"] for row in integrity)
    metrics_available = structured_available == len(cards) * len(engines) and all(
        row["actual_pages"] == row["expected_pages"] and row["normalized_schema_valid_documents"] == len(cards)
        for row in integrity
    )
    text = text_metric_rows(run_root, engines, cards, manifest.get("coverage_policy", {}))
    structured = structured_rows(run_root, engines, cards, metrics_available)
    critical = critical_rows(run_root, engines, cards, metrics_available)
    generation_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    for rows in (integrity, text, structured, critical):
        for row in rows:
            row["generation_id"] = generation_id
    summary = {
        "schema_version": "clean_end_to_end_evaluation_v1",
        "status": "complete" if metrics_available else "incomplete",
        "generation_id": generation_id,
        "bundle_complete": True,
        "finished_at": utc_now(),
        "run_id": run_root.name,
        "engines": list(engines),
        "cards": [card["key"] for card in cards],
        "expected_structured_documents": len(cards) * len(engines),
        "available_structured_documents": structured_available,
        "incomplete_reason": None if structured_available == len(cards) * len(engines) else "structured results are missing or invalid",
        "metrics": summaries(integrity, text, structured, critical, metrics_available),
    }
    # Summary is the final commit marker; consumers reject CSV rows with another generation_id.
    atomic_write_csv(evaluated_root / "integrity.csv", integrity)
    atomic_write_csv(evaluated_root / "text_metrics.csv", text)
    atomic_write_csv(evaluated_root / "structured_metrics.csv", structured)
    atomic_write_csv(evaluated_root / "critical_facts.csv", critical)
    atomic_write_json(evaluated_root / "evaluation_summary.json", summary)
    return summary
