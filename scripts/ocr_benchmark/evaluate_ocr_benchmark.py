from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from apted import APTED, Config

from run_pymupdf import ROOT


BENCHMARK_DIR = ROOT / "data" / "ocr_benchmark"
GOLD_PATH = BENCHMARK_DIR / "gold" / "structured" / "BC" / "BC_Biz_AirMoney.json"
REPORT_JSON_PATH = BENCHMARK_DIR / "reports" / "bc_biz_airmoney_comparison.json"
REPORT_MD_PATH = BENCHMARK_DIR / "reports" / "bc_biz_airmoney_comparison.md"
MODEL_DIRS = {
    "pymupdf": BENCHMARK_DIR / "pymupdf",
    "mistral": BENCHMARK_DIR / "normalized" / "mistral",
    "upstage": BENCHMARK_DIR / "normalized" / "upstage",
}
VISION_DIR = BENCHMARK_DIR / "vision" / "vision_raw_text"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def levenshtein_distance(left: list[str] | str, right: list[str] | str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for left_index, left_value in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_value in enumerate(right, start=1):
            current.append(min(current[-1] + 1, previous[right_index] + 1, previous[right_index - 1] + (left_value != right_value)))
        previous = current
    return previous[-1]


def plain_text(markdown: str) -> str:
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", "", markdown)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    return re.sub(r"\s+", " ", text.replace("|", " ").replace("**", "")).strip()


def numeric_text(text: str) -> str:
    token_pattern = r"\d[\d,.:~%]*(?:영업일|개월|년|월|일|시|분|원|만원|천원|만점|포인트|회|대보험)?"
    return " ".join(re.findall(token_pattern, plain_text(text)))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_documents(model: str) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted(MODEL_DIRS[model].glob("*/*.json"))]


def weighted_metric(documents: list[dict[str, Any]], name: str) -> float | None:
    values = [document["metrics"].get(name) for document in documents]
    if any(value is None for value in values):
        return None
    page_count = sum(document["page_count"] for document in documents)
    return sum(document["page_count"] * value for document, value in zip(documents, values)) / page_count


def structured_operational_metrics(model: str) -> dict[str, Any]:
    documents = load_documents(model)
    page_count = sum(document["page_count"] for document in documents)
    elapsed_seconds = sum(document["elapsed_seconds"] for document in documents)
    return {
        "document_count": len(documents),
        "page_count": page_count,
        "processing_success_rate": weighted_metric(documents, "processing_success_rate"),
        "page_coverage": weighted_metric(documents, "page_coverage"),
        "empty_output_rate": weighted_metric(documents, "empty_output_rate"),
        "duplicate_output_rate": weighted_metric(documents, "duplicate_output_rate"),
        "schema_valid_rate": sum(bool(document["metrics"].get("schema_valid")) for document in documents) / len(documents),
        "seconds_per_page": elapsed_seconds / page_count,
        "cost_per_page_usd": weighted_metric(documents, "cost_per_page_usd"),
    }


def vision_pages(text: str) -> list[str]:
    markers = list(re.finditer(r"^\[PAGE \d+\]\s*$", text, flags=re.MULTILINE))
    return [text[marker.end() : markers[index + 1].start() if index + 1 < len(markers) else None].strip() for index, marker in enumerate(markers)]


def vision_operational_metrics() -> dict[str, Any]:
    expected_documents = load_documents("pymupdf")
    expected_pages = sum(document["page_count"] for document in expected_documents)
    files = [VISION_DIR / document["issuer"] / f"{document['card_name']}.txt" for document in expected_documents]
    texts = [path.read_text(encoding="utf-8") if path.exists() else "" for path in files]
    pages = [page for text in texts for page in vision_pages(text)]
    return {
        "document_count": len(expected_documents),
        "page_count": expected_pages,
        "artifact_success_rate": sum(bool(text) for text in texts) / len(texts),
        "page_coverage": len(pages) / expected_pages,
        "empty_output_rate": sum(not page for page in pages) / expected_pages,
        "duplicate_output_rate": None,
        "schema_valid_rate": None,
        "seconds_per_page": None,
        "cost_per_page_usd": None,
        "note": "Existing Vision raw-text files do not retain block schema, elapsed time, or cost metadata.",
    }


def marked_pages(text: str) -> dict[int, str]:
    pattern = r"^<!-- page (\d+) -->\s*$"
    markers = list(re.finditer(pattern, text, flags=re.MULTILINE))
    return {
        int(marker.group(1)): text[marker.end() : markers[index + 1].start() if index + 1 < len(markers) else None]
        for index, marker in enumerate(markers)
    }


def model_page_texts(model: str, issuer: str, card_name: str) -> dict[int, str]:
    if model == "vision":
        text = (VISION_DIR / issuer / f"{card_name}.txt").read_text(encoding="utf-8")
        return {index: page for index, page in enumerate(vision_pages(text), start=1)}
    if model == "pymupdf":
        document = read_json(MODEL_DIRS[model] / issuer / f"{card_name}.json")
        return {page["page_num"]: page["text"] for page in document["pages"]}
    if model == "mistral":
        text = (BENCHMARK_DIR / "text" / model / issuer / f"{card_name}.md").read_text(encoding="utf-8")
        return marked_pages(text)

    document = read_json(MODEL_DIRS[model] / issuer / f"{card_name}.json")
    return {
        page["page_num"]: "\n".join(
            [block.get("text", "") for block in page["blocks"]]
            + [table["content"] for table in page["tables"]]
        )
        for page in document["pages"]
    }


def positions(text: str, needle: str) -> list[int]:
    result, start = [], 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return result
        result.append(index)
        start = index + len(needle)


def score_numeric_labels(page_texts: dict[int, str], labels: list[dict[str, Any]]) -> dict[str, Any]:
    details = []
    for label in labels:
        source = compact(page_texts.get(label["page_num"], ""))
        token = compact(label["surface_text"])
        matches = positions(source, token)
        expected_count = label.get("expected_occurrences", 1)
        context_terms = [compact(term) for term in label.get("context_terms", [])]
        contextual_matches = [
            position
            for position in matches
            if all(term in source[max(0, position - 180) : position + len(token) + 180] for term in context_terms)
        ]
        relation_match = len(contextual_matches) == expected_count
        details.append(
            {
                "id": label["id"],
                "expected": label["surface_text"],
                "expected_occurrences": expected_count,
                "found_occurrences": len(matches),
                "contextual_occurrences": len(contextual_matches),
                "surface_exact_match": bool(matches),
                "relation_context_match": relation_match,
                "critical": label["critical"],
            }
        )

    def rate(items: list[dict[str, Any]], field: str) -> float:
        return sum(item[field] for item in items) / len(items)

    critical = [item for item in details if item["critical"]]
    return {
        "label_count": len(details),
        "numeric_exact_match_rate": rate(details, "surface_exact_match"),
        "relation_context_match_rate": rate(details, "relation_context_match"),
        "critical_numeric_exact_match_rate": rate(critical, "surface_exact_match"),
        "details": details,
    }


def selected_gold_text(gold: dict[str, Any], label: dict[str, Any]) -> str:
    raw_path = ROOT / gold["raw_annotation"]
    raw = raw_path.read_text(encoding="utf-8")
    return raw.split(label["raw_start_marker"], maxsplit=1)[1]


def text_metrics(reference: str, hypothesis: str) -> dict[str, float]:
    reference_text = plain_text(reference)
    hypothesis_text = plain_text(hypothesis)
    reference_chars = compact(reference_text)
    hypothesis_chars = compact(hypothesis_text)
    character_distance = levenshtein_distance(reference_chars, hypothesis_chars)
    reference_words = reference_text.split()
    hypothesis_words = hypothesis_text.split()
    word_distance = levenshtein_distance(reference_words, hypothesis_words)
    reference_numeric = numeric_text(reference)
    hypothesis_numeric = numeric_text(hypothesis)
    numeric_distance = levenshtein_distance(reference_numeric, hypothesis_numeric)
    return {
        "cer": character_distance / len(reference_chars),
        "wer": word_distance / len(reference_words),
        "numeric_cer": numeric_distance / len(reference_numeric),
        "normalized_edit_distance": character_distance / max(len(reference_chars), len(hypothesis_chars)),
    }


def section_order_metrics(text: str, headings: list[str]) -> dict[str, float]:
    source = compact(plain_text(text))
    positions_by_heading = [source.find(compact(heading)) for heading in headings]
    correct_pairs = 0
    total_pairs = len(headings) * (len(headings) - 1) // 2
    for left in range(len(headings)):
        for right in range(left + 1, len(headings)):
            if positions_by_heading[left] >= 0 and positions_by_heading[right] >= 0 and positions_by_heading[left] < positions_by_heading[right]:
                correct_pairs += 1
    return {
        "section_coverage": sum(position >= 0 for position in positions_by_heading) / len(headings),
        "section_order_accuracy": correct_pairs / total_pairs,
    }


class TableNode:
    def __init__(self, name: str, children: list["TableNode"] | None = None, content: str = "") -> None:
        self.name = name
        self.children = children or []
        self.content = content


class TableConfig(Config):
    def rename(self, left: TableNode, right: TableNode) -> float:
        if left.name != right.name:
            return 1.0
        if left.name != "td":
            return 0.0
        left_text, right_text = compact(left.content), compact(right.content)
        return levenshtein_distance(left_text, right_text) / max(len(left_text), len(right_text), 1)


class StructureOnlyTableConfig(TableConfig):
    def rename(self, left: TableNode, right: TableNode) -> float:
        return 0.0 if left.name == right.name else 1.0


def table_tree(rows: list[list[str]]) -> TableNode:
    return TableNode("table", [TableNode("tr", [TableNode("td", content=str(cell)) for cell in row]) for row in rows])


def tree_size(node: TableNode) -> int:
    return 1 + sum(tree_size(child) for child in node.children)


def markdown_rows(content: str) -> list[list[str]]:
    rows = []
    for line in content.splitlines():
        if "|" not in line:
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def candidate_table_rows(table: dict[str, Any]) -> list[list[str]]:
    if "cells" in table:
        return [[str(cell or "") for cell in row] for row in table["cells"]]
    return markdown_rows(table["content"])


def teds_score(reference_rows: list[list[str]], candidate_rows: list[list[str]], config: Config) -> float:
    reference, candidate = table_tree(reference_rows), table_tree(candidate_rows)
    distance = APTED(reference, candidate, config).compute_edit_distance()
    return max(0.0, 1.0 - distance / max(tree_size(reference), tree_size(candidate)))


def table_metrics(model: str, gold: dict[str, Any]) -> dict[str, float]:
    if model == "vision":
        return {"teds": None, "teds_s": None, "table_detection_recall": None}
    document = read_json(MODEL_DIRS[model] / gold["issuer"] / f"{gold['card_name']}.json")
    candidate_tables = [table for page in document["pages"] for table in page["tables"] if page["page_num"] == 2]
    candidates = [candidate_table_rows(table) for table in candidate_tables]
    scores, structure_scores = [], []
    for label in gold["table_labels"]:
        reference_rows = [label["headers"], *label["rows"]]
        pairs = [(teds_score(reference_rows, candidate, TableConfig()), teds_score(reference_rows, candidate, StructureOnlyTableConfig())) for candidate in candidates]
        best = max(pairs, default=(0.0, 0.0), key=lambda pair: pair[0])
        scores.append(best[0])
        structure_scores.append(best[1])
    return {
        "teds": sum(scores) / len(scores),
        "teds_s": sum(structure_scores) / len(structure_scores),
        "table_detection_recall": sum(score >= 0.5 for score in structure_scores) / len(structure_scores),
    }


def build_report() -> dict[str, Any]:
    gold = read_json(GOLD_PATH)
    models = ["pymupdf", "mistral", "upstage", "vision"]
    operational = {model: structured_operational_metrics(model) for model in models[:-1]}
    operational["vision"] = vision_operational_metrics()
    numeric = {
        model: score_numeric_labels(model_page_texts(model, gold["issuer"], gold["card_name"]), gold["numeric_labels"])
        for model in models
    }
    text_label = gold["text_labels"][0]
    reference_text = selected_gold_text(gold, text_label)
    text = {
        model: text_metrics(reference_text, model_page_texts(model, gold["issuer"], gold["card_name"])[text_label["page_num"]])
        for model in models
    }
    tables = {model: table_metrics(model, gold) for model in models}
    section_order = {
        model: section_order_metrics(model_page_texts(model, gold["issuer"], gold["card_name"])[text_label["page_num"]], gold["section_order_labels"])
        for model in models
    }
    return {
        "schema_version": "1.0",
        "gold_file": str(GOLD_PATH.relative_to(ROOT)),
        "scope": {
            "operational": "10 cards / 50 pages",
            "key_number_and_relation": "BC Biz Air Money selected excerpts / 27 labels",
            "text": "BC Biz Air Money page 2 user-provided transcription",
            "tables": "BC Biz Air Money page 2 airport-lounge and card-issue tables",
            "not_evaluated": ["reading_order_error", "block_detection_f1", "bbox_iou", "structured_card_fields", "QA_accuracy"],
            "reason": "Block bounding boxes, block order annotations, and card-field extraction ground truth are not labeled yet.",
        },
        "operational_metrics": operational,
        "key_number_metrics": numeric,
        "text_metrics": text,
        "table_metrics": tables,
        "section_order_metrics": section_order,
    }


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# OCR benchmark comparison", "", "## Operational metrics (10 cards / 50 pages)", "", "| Model | Success | Coverage | Empty | Duplicate | Schema | sec/page | $/page |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for model, metrics in report["operational_metrics"].items():
        success = metrics.get("processing_success_rate", metrics.get("artifact_success_rate"))
        values = [success, metrics["page_coverage"], metrics["empty_output_rate"], metrics["duplicate_output_rate"], metrics["schema_valid_rate"], metrics["seconds_per_page"], metrics["cost_per_page_usd"]]
        formatted = ["-" if value is None else f"{value:.3f}" for value in values]
        lines.append(f"| {model} | " + " | ".join(formatted) + " |")
    lines.extend(["", "## BC key-number metrics (27 labels)", "", "| Model | Numeric exact match | Relation context | Critical numeric exact match |", "| --- | ---: | ---: | ---: |"])
    for model, metrics in report["key_number_metrics"].items():
        lines.append(f"| {model} | {metrics['numeric_exact_match_rate']:.3f} | {metrics['relation_context_match_rate']:.3f} | {metrics['critical_numeric_exact_match_rate']:.3f} |")
    lines.extend(["", "## BC page 2 text metrics", "", "| Model | CER | WER | Numeric CER | Normalized edit distance |", "| --- | ---: | ---: | ---: | ---: |"])
    for model, metrics in report["text_metrics"].items():
        lines.append(f"| {model} | {metrics['cer']:.3f} | {metrics['wer']:.3f} | {metrics['numeric_cer']:.3f} | {metrics['normalized_edit_distance']:.3f} |")
    lines.extend(["", "## BC page 2 table metrics", "", "| Model | TEDS | TEDS-S | Table detection recall |", "| --- | ---: | ---: | ---: |"])
    for model, metrics in report["table_metrics"].items():
        values = [metrics["teds"], metrics["teds_s"], metrics["table_detection_recall"]]
        lines.append(f"| {model} | " + " | ".join("-" if value is None else f"{value:.3f}" for value in values) + " |")
    lines.extend(["", "## BC page 2 section order", "", "| Model | Section coverage | Section-order accuracy |", "| --- | ---: | ---: |"])
    for model, metrics in report["section_order_metrics"].items():
        lines.append(f"| {model} | {metrics['section_coverage']:.3f} | {metrics['section_order_accuracy']:.3f} |")
    lines.extend(["", "Block F1, bbox IoU, block-level reading order, card-field extraction, and QA require additional ground-truth annotations.", ""])
    return "\n".join(lines)


def main() -> None:
    report = build_report()
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(markdown_report(report), encoding="utf-8")
    print(markdown_report(report))


if __name__ == "__main__":
    main()
