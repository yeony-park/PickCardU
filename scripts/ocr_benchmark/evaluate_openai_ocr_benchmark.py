from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from run_openai_ocr_benchmark import CONDITIONS, OUTPUT_DIR, ROOT, TARGETS


GOLD_DIR = ROOT / "data" / "ocr_benchmark" / "gold" / "structured"
REPORT_JSON_PATH = ROOT / "data" / "ocr_benchmark" / "reports" / "openai_surface_comparison.json"
REPORT_MD_PATH = ROOT / "data" / "ocr_benchmark" / "reports" / "openai_surface_comparison.md"


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def plain_text(markdown: str) -> str:
    text = re.sub(r"(?m)^\[(?:left|right)_panel\]\s*$", "", markdown)
    text = re.sub(r"!\[[^]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*+]\s+", "", text)
    return re.sub(r"\s+", " ", text.replace("|", " ").replace("**", "")).strip()


def numeric_text(text: str) -> str:
    pattern = r"\d[\d,.:~%]*(?:영업일|개월|년|월|일|시|분|원|만원|천원|만점|포인트|회|대보험)?"
    return " ".join(re.findall(pattern, plain_text(text)))


def levenshtein_distance(left: list[str] | str, right: list[str] | str) -> int:
    if not left:
        return len(right)
    if not right:
        return len(left)
    if len(left) > len(right):
        left, right = right, left
    bit_masks: dict[str, int] = {}
    for index, value in enumerate(left):
        bit_masks[value] = bit_masks.get(value, 0) | (1 << index)
    mask = (1 << len(left)) - 1
    high_bit = 1 << (len(left) - 1)
    positive, negative, score = mask, 0, len(left)
    for value in right:
        equality = bit_masks.get(value, 0)
        vertical = equality | negative
        horizontal = (((equality & positive) + positive) ^ positive) | equality
        positive_horizontal = negative | ~(horizontal | positive)
        negative_horizontal = positive & horizontal
        if positive_horizontal & high_bit:
            score += 1
        elif negative_horizontal & high_bit:
            score -= 1
        positive_horizontal = ((positive_horizontal << 1) | 1) & mask
        negative_horizontal = (negative_horizontal << 1) & mask
        positive = (negative_horizontal | ~(vertical | positive_horizontal)) & mask
        negative = positive_horizontal & vertical
    return score


def raw_page_texts(raw: str) -> dict[int, str]:
    markers = list(re.finditer(r"(?mi)^\[page\s*(\d+)\]\s*$", raw))
    return {
        int(marker.group(1)): raw[marker.end() : markers[index + 1].start() if index + 1 < len(markers) else None].strip()
        for index, marker in enumerate(markers)
    }


def selected_reference(raw: str, label: dict[str, Any], pages: dict[int, str]) -> str:
    if label["page_num"] in pages:
        return pages[label["page_num"]]
    marker = label["raw_start_marker"]
    if marker not in raw:
        raise ValueError(f"missing raw marker: {marker}")
    return raw.split(marker, maxsplit=1)[1].strip()


def positions(text: str, needle: str) -> list[int]:
    found, start = [], 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return found
        found.append(index)
        start = index + len(needle)


def markdown_rows(lines: list[str]) -> list[list[str]]:
    rows = []
    for line in lines:
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
            continue
        rows.append(cells)
    return rows


def markdown_tables(text: str) -> list[list[list[str]]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines() + [""]:
        if "|" in line:
            current.append(line)
        elif current:
            if any(re.search(r"\|?\s*:?-{3,}:?\s*\|", item) for item in current):
                groups.append(current)
            current = []
    return [rows for group in groups if len(rows := markdown_rows(group)) >= 2]


def table_similarity(reference: list[list[str]], candidate: list[list[str]]) -> dict[str, float]:
    reference_text = compact(" ".join(cell for row in reference for cell in row))
    candidate_text = compact(" ".join(cell for row in candidate for cell in row))
    content = 1.0 - levenshtein_distance(reference_text, candidate_text) / max(len(reference_text), len(candidate_text), 1)

    max_rows = max(len(reference), len(candidate), 1)
    row_score = 1.0 - abs(len(reference) - len(candidate)) / max_rows
    max_columns = max(max((len(row) for row in reference), default=0), max((len(row) for row in candidate), default=0), 1)
    paired = min(len(reference), len(candidate))
    column_penalty = sum(abs(len(reference[index]) - len(candidate[index])) for index in range(paired))
    column_penalty += abs(len(reference) - len(candidate)) * max_columns
    column_score = 1.0 - column_penalty / (max_rows * max_columns)
    return {"content": max(0.0, content), "structure": max(0.0, (row_score + column_score) / 2)}


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def available_conditions() -> list[str]:
    return [
        condition.key
        for condition in CONDITIONS
        if (OUTPUT_DIR / condition.key).exists() and any((OUTPUT_DIR / condition.key).glob("*/*.json"))
    ]


def documents(condition: str) -> list[dict[str, Any]]:
    return [read_json(path) for path in sorted((OUTPUT_DIR / condition).glob("*/*.json")) if read_json(path).get("run_status") == "completed"]


def page_map(document: dict[str, Any]) -> dict[int, str]:
    return {page["page_num"]: page["markdown"] for page in document["pages"]}


def operational_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    page_count = sum(item["page_count"] for item in items)
    total_tokens = sum(item.get("usage", {}).get("total_tokens") or 0 for item in items)
    token_coverage = sum(item["page_count"] for item in items if item.get("usage", {}).get("total_tokens") is not None)
    return {
        "document_count": len(items),
        "page_count": page_count,
        "processing_success_rate": sum(
            page["status"] == "success" for item in items for page in item["pages"]
        )
        / page_count,
        "page_coverage": sum(bool(page["markdown"].strip()) for item in items for page in item["pages"]) / page_count,
        "seconds_per_page": sum(item["elapsed_seconds"] for item in items) / page_count,
        "tokens_per_page": total_tokens / token_coverage if token_coverage else None,
        "total_elapsed_seconds": sum(item["elapsed_seconds"] for item in items),
        "total_tokens": total_tokens if token_coverage else None,
    }


def quality_metrics(condition: str) -> dict[str, Any]:
    candidates = {(item["issuer"], item["card_name"]): item for item in documents(condition)}
    text_totals = {"char_distance": 0, "reference_chars": 0, "max_chars": 0, "word_distance": 0, "reference_words": 0, "numeric_distance": 0, "reference_numeric": 0}
    numeric_details = []
    table_details = []
    section_scores = []
    gold_documents = 0
    full_page_labels = 0

    for gold_path in sorted(GOLD_DIR.glob("*/*.json")):
        gold = read_json(gold_path)
        candidate = candidates.get((gold["issuer"], gold["card_name"]))
        if not candidate:
            continue
        gold_documents += 1
        pages = page_map(candidate)
        raw = (ROOT / gold["raw_annotation"]).read_text(encoding="utf-8")
        raw_pages = raw_page_texts(raw)

        for label in gold.get("text_labels", []):
            reference = selected_reference(raw, label, raw_pages)
            hypothesis = pages.get(label["page_num"], "")
            reference_text, hypothesis_text = plain_text(reference), plain_text(hypothesis)
            reference_chars, hypothesis_chars = compact(reference_text), compact(hypothesis_text)
            text_totals["char_distance"] += levenshtein_distance(reference_chars, hypothesis_chars)
            text_totals["reference_chars"] += len(reference_chars)
            text_totals["max_chars"] += max(len(reference_chars), len(hypothesis_chars))
            reference_words, hypothesis_words = reference_text.split(), hypothesis_text.split()
            text_totals["word_distance"] += levenshtein_distance(reference_words, hypothesis_words)
            text_totals["reference_words"] += len(reference_words)
            reference_numeric, hypothesis_numeric = numeric_text(reference), numeric_text(hypothesis)
            text_totals["numeric_distance"] += levenshtein_distance(reference_numeric, hypothesis_numeric)
            text_totals["reference_numeric"] += len(reference_numeric)
            full_page_labels += 1

        for label in gold.get("numeric_labels", []):
            source = compact(pages.get(label["page_num"], ""))
            token = compact(label["surface_text"])
            matches = positions(source, token)
            expected = label.get("expected_occurrences", 1)
            context_terms = [compact(term) for term in label.get("context_terms", [])]
            contextual = [
                position
                for position in matches
                if all(term in source[max(0, position - 180) : position + len(token) + 180] for term in context_terms)
            ]
            numeric_details.append(
                {
                    "document": gold["card_name"],
                    "id": label["id"],
                    "surface_exact": bool(matches),
                    "relation_context": len(contextual) == expected,
                    "critical": label.get("critical", False),
                }
            )

        for label in gold.get("table_labels", []):
            reference = [label["headers"], *label["rows"]]
            candidates_on_page = markdown_tables(pages.get(label["page_num"], ""))
            scores = [table_similarity(reference, table) for table in candidates_on_page]
            best = max(scores, default={"content": 0.0, "structure": 0.0}, key=lambda score: score["content"] + score["structure"])
            table_details.append({"document": gold["card_name"], "id": label["id"], **best})

        headings = gold.get("section_order_labels") or []
        if headings:
            source = compact(plain_text("\n".join(pages.values())))
            positions_by_heading = [source.find(compact(heading)) for heading in headings]
            pairs = [
                positions_by_heading[left] >= 0
                and positions_by_heading[right] >= 0
                and positions_by_heading[left] < positions_by_heading[right]
                for left in range(len(headings))
                for right in range(left + 1, len(headings))
            ]
            section_scores.append(
                {
                    "coverage": sum(position >= 0 for position in positions_by_heading) / len(headings),
                    "order": sum(pairs) / len(pairs),
                }
            )

    critical_numeric = [item for item in numeric_details if item["critical"]]
    average = lambda values: sum(values) / len(values) if values else None
    cer = text_totals["char_distance"] / text_totals["reference_chars"]
    normalized_distance = text_totals["char_distance"] / text_totals["max_chars"]
    metrics = {
        "gold_document_count": gold_documents,
        "text_label_count": full_page_labels,
        "cer": cer,
        "wer": text_totals["word_distance"] / text_totals["reference_words"],
        "numeric_cer": text_totals["numeric_distance"] / text_totals["reference_numeric"],
        "normalized_edit_similarity": max(0.0, 1.0 - normalized_distance),
        "numeric_label_count": len(numeric_details),
        "numeric_exact_match_rate": average([item["surface_exact"] for item in numeric_details]),
        "numeric_relation_match_rate": average([item["relation_context"] for item in numeric_details]),
        "critical_numeric_exact_match_rate": average([item["surface_exact"] for item in critical_numeric]),
        "table_label_count": len(table_details),
        "table_content_similarity": average([item["content"] for item in table_details]),
        "table_structure_similarity": average([item["structure"] for item in table_details]),
        "section_coverage": average([item["coverage"] for item in section_scores]),
        "section_order_accuracy": average([item["order"] for item in section_scores]),
        "numeric_details": numeric_details,
        "table_details": table_details,
    }
    metrics["composite_quality_score"] = (
        0.40 * metrics["normalized_edit_similarity"]
        + 0.20 * metrics["numeric_exact_match_rate"]
        + 0.15 * metrics["numeric_relation_match_rate"]
        + 0.125 * metrics["table_content_similarity"]
        + 0.125 * metrics["table_structure_similarity"]
    )
    return metrics


def build_report() -> dict[str, Any]:
    conditions = available_conditions()
    rows = {}
    for condition in conditions:
        items = documents(condition)
        rows[condition] = {
            "condition": next(item for item in CONDITIONS if item.key == condition).__dict__,
            "operational": operational_metrics(items),
            "quality": quality_metrics(condition),
        }
    ranking = sorted(conditions, key=lambda key: rows[key]["quality"]["composite_quality_score"], reverse=True)
    return {
        "schema_version": "1.0",
        "scope": {
            "documents": TARGETS,
            "cli_dpi": 200,
            "api_input": "PDF direct with detail=high",
            "quality_gold_documents": 6,
            "composite_weights": {
                "normalized_edit_similarity": 0.40,
                "numeric_exact_match_rate": 0.20,
                "numeric_relation_match_rate": 0.15,
                "table_content_similarity": 0.125,
                "table_structure_similarity": 0.125,
            },
        },
        "conditions": rows,
        "quality_ranking": ranking,
        "complete_matrix": "api_luna_max_high" in conditions and all(rows[key]["operational"]["document_count"] == 10 for key in rows),
    }


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# OpenAI 문서 파싱 비교",
        "",
        f"- 비교 완결 여부: {'완료' if report['complete_matrix'] else 'API 조건 대기 중'}",
        "- CLI 입력: PDF 페이지를 200 DPI PNG로 렌더링",
        "- API 입력: 원본 PDF 직접 입력, `detail=high` (DPI 미적용)",
        "- 품질 평가는 골드셋이 있는 6문서에서 수행",
        "",
        "| 순위 | 조건 | 문서/페이지 | 성공률 | 초/페이지 | 토큰/페이지 | 텍스트 유사도 | CER↓ | 숫자 정확도 | 숫자-문맥 | 표 내용 | 표 구조 | 종합 점수 |",
        "| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for rank, key in enumerate(report["quality_ranking"], start=1):
        row = report["conditions"][key]
        operational, quality = row["operational"], row["quality"]
        lines.append(
            f"| {rank} | `{key}` | {operational['document_count']}/{operational['page_count']} | "
            f"{percent(operational['processing_success_rate'])} | {operational['seconds_per_page']:.2f} | "
            f"{operational['tokens_per_page']:.0f} | {percent(quality['normalized_edit_similarity'])} | "
            f"{percent(quality['cer'])} | {percent(quality['numeric_exact_match_rate'])} | "
            f"{percent(quality['numeric_relation_match_rate'])} | {percent(quality['table_content_similarity'])} | "
            f"{percent(quality['table_structure_similarity'])} | {quality['composite_quality_score']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## 해석 주의사항",
            "",
            "- 종합 점수는 텍스트 유사도 40%, 숫자 정확도 20%, 숫자-문맥 15%, 표 내용 12.5%, 표 구조 12.5%의 명시적 가중 평균입니다.",
            "- CER·WER은 사용자 골드 전사와의 편집거리이며 낮을수록 좋습니다.",
            "- NH, Hana, Hyundai, IBK는 골드 전사가 없어 처리 성공률·시간·토큰에만 반영됩니다.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    report = build_report()
    REPORT_JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD_PATH.write_text(render_markdown(report), encoding="utf-8")
    print(REPORT_MD_PATH)


if __name__ == "__main__":
    main()
