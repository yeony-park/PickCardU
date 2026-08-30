from __future__ import annotations

import html
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any


STRUCTURAL_ISSUES = {
    "table_count_mismatch",
    "table_structure_mismatch",
    "heading_alignment_low",
    "bbox_coverage_low",
    "blank_primary_nonempty_layout",
    "empty_layout_page",
}
DERIVED_BLANK_METHODS = {
    "dominant_rendered_rgb",
    "native_empty_no_images_low_drawings",
}

NUMBER_PATTERN = re.compile(
    r"(?:\d{2,4}[-./]\d{1,2}(?:[-./]\d{1,2})?|\d{2,4}-\d{3,4}-\d{4}|"
    r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?:%|원|만원|천원|포인트|점|회|개월|년|월|일|시|분|마일|리터|L|건)?"
)


def normalize_text(value: str) -> str:
    value = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    value = re.sub(r"!\[([^]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"[`*_#|>\-]+", " ", value)
    return re.sub(r"\s+", "", value).casefold()


def extract_numeric_tokens(value: str) -> Counter[str]:
    return Counter(re.sub(r"\s+", "", match.group(0)).casefold() for match in NUMBER_PATTERN.finditer(value))


def multiset_scores(reference: Counter[str], candidate: Counter[str]) -> dict[str, float]:
    if not reference and not candidate:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    overlap = sum((reference & candidate).values())
    precision = overlap / sum(candidate.values()) if candidate else 0.0
    recall = overlap / sum(reference.values()) if reference else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def block_text(page: dict[str, Any]) -> str:
    blocks = sorted(page.get("blocks", []), key=lambda block: block.get("reading_order", 0))
    return "\n\n".join(str(block.get("text", "")).strip() for block in blocks if str(block.get("text", "")).strip())


def heading_blocks(page: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        block
        for block in sorted(page.get("blocks", []), key=lambda block: block.get("reading_order", 0))
        if str(block.get("type", "")).startswith(("heading", "title")) and str(block.get("text", "")).strip()
    ]


def heading_coverage(primary_text: str, layout_page: dict[str, Any]) -> float:
    headings = heading_blocks(layout_page)
    if not headings:
        return 1.0
    lines = [normalize_text(line) for line in primary_text.splitlines() if normalize_text(line)]
    matched = 0
    for block in headings:
        heading = normalize_text(str(block.get("text", "")))
        if not heading:
            continue
        best = max(
            (
                1.0
                if heading in line or line in heading
                else SequenceMatcher(None, heading, line).ratio()
                for line in lines
            ),
            default=0.0,
        )
        if best >= 0.6:
            matched += 1
    return matched / len(headings)


def markdown_table_shapes(value: str) -> list[tuple[int, int]]:
    shapes: list[tuple[int, int]] = []
    current: list[list[str]] = []
    for line in [*value.splitlines(), ""]:
        stripped = line.strip()
        if "|" in stripped:
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells if cell):
                continue
            current.append(cells)
        elif current:
            shapes.append((len(current), max(len(row) for row in current)))
            current = []
    return shapes


def layout_table_shapes(page: dict[str, Any]) -> list[tuple[int, int]]:
    shapes = []
    for table in page.get("tables", []):
        content = str(table.get("content", ""))
        markdown_shapes = markdown_table_shapes(content)
        if markdown_shapes:
            shapes.extend(markdown_shapes)
            continue
        row_count = len(re.findall(r"<tr\b", content, flags=re.IGNORECASE))
        column_counts = [
            len(re.findall(r"<t[dh]\b", row, flags=re.IGNORECASE))
            for row in re.findall(r"<tr\b.*?</tr>", content, flags=re.IGNORECASE | re.DOTALL)
        ]
        if row_count:
            shapes.append((row_count, max(column_counts, default=0)))
    return shapes


def compare_page(primary_page: dict[str, Any], layout_page: dict[str, Any]) -> dict[str, Any]:
    primary_text = str(primary_page.get("markdown", ""))
    primary_is_blank = primary_page.get("is_blank") is True
    blank_provenance = layout_page.get("blank_provenance")
    layout_derived_blank = (
        layout_page.get("is_blank") is True
        and isinstance(blank_provenance, dict)
        and blank_provenance.get("method") in DERIVED_BLANK_METHODS
    )
    effective_primary_blank = primary_is_blank or (
        not primary_text.strip() and layout_derived_blank
    )
    validator_text = block_text(layout_page)
    primary_normalized = normalize_text(primary_text)
    validator_normalized = normalize_text(validator_text)
    similarity = (
        SequenceMatcher(None, primary_normalized, validator_normalized).ratio()
        if primary_normalized and validator_normalized
        else float(primary_normalized == validator_normalized)
    )
    numeric = multiset_scores(extract_numeric_tokens(primary_text), extract_numeric_tokens(validator_text))
    headings = heading_coverage(primary_text, layout_page)
    primary_tables = markdown_table_shapes(primary_text)
    validator_tables = layout_table_shapes(layout_page)
    nonempty_blocks = [block for block in layout_page.get("blocks", []) if str(block.get("text", "")).strip()]
    bbox_coverage = (
        sum(block.get("bbox") is not None for block in nonempty_blocks) / len(nonempty_blocks) if nonempty_blocks else 1.0
    )

    issues = []
    if effective_primary_blank and validator_text.strip():
        issues.append("blank_primary_nonempty_layout")
    if not effective_primary_blank and not primary_text.strip():
        issues.append("unexpected_empty_primary_page")
    if not effective_primary_blank and not validator_text.strip():
        issues.append("empty_layout_page")
    if primary_text.strip() and validator_text.strip() and similarity < 0.45:
        issues.append("text_similarity_low")
    if extract_numeric_tokens(primary_text) and numeric["f1"] < 0.75:
        issues.append("numeric_mismatch")
    if heading_blocks(layout_page) and headings < 0.6:
        issues.append("heading_alignment_low")
    if len(primary_tables) != len(validator_tables):
        issues.append("table_count_mismatch")
    elif primary_tables and validator_tables and primary_tables != validator_tables:
        issues.append("table_structure_mismatch")
    if nonempty_blocks and bbox_coverage < 0.8:
        issues.append("bbox_coverage_low")
    if primary_page.get("uncertain_spans"):
        issues.append("uncertain_primary_text")

    return {
        "verdict": "pass" if not issues else "review_required",
        "metrics": {
            "text_similarity": round(similarity, 6),
            "numeric_precision": round(numeric["precision"], 6),
            "numeric_recall": round(numeric["recall"], 6),
            "numeric_f1": round(numeric["f1"], 6),
            "heading_coverage": round(headings, 6),
            "primary_table_shapes": [list(shape) for shape in primary_tables],
            "layout_table_shapes": [list(shape) for shape in validator_tables],
            "bbox_coverage": round(bbox_coverage, 6),
            "layout_derived_blank": layout_derived_blank,
        },
        "issues": issues,
        "requires_structural_review": any(issue in STRUCTURAL_ISSUES for issue in issues),
    }


def verify_document(primary: dict[str, Any], layout: dict[str, Any]) -> dict[str, Any]:
    if primary.get("document_id") != layout.get("document_id"):
        raise ValueError("primary and layout document IDs do not match")
    if primary.get("source", {}).get("sha256") != layout.get("source", {}).get("sha256"):
        raise ValueError("primary and layout source hashes do not match")
    if primary.get("run_status") != "completed" or layout.get("run_status") != "completed":
        raise ValueError("both primary and layout artifacts must be completed")

    primary_pages = {page["page_num"]: page for page in primary.get("pages", [])}
    layout_pages = {page["page_num"]: page for page in layout.get("pages", [])}
    expected_count = int(primary.get("source", {}).get("page_count", 0))
    if set(primary_pages) != set(layout_pages) or set(primary_pages) != set(range(1, expected_count + 1)):
        raise ValueError("primary and layout page sets do not match the source")

    pages = []
    structural_review_pages = []
    for page_num in range(1, expected_count + 1):
        comparison = compare_page(primary_pages[page_num], layout_pages[page_num])
        if comparison["requires_structural_review"]:
            structural_review_pages.append(page_num)
        pages.append(
            {
                "page_num": page_num,
                "resolved_text": primary_pages[page_num].get("markdown", ""),
                "primary": {
                    "status": primary_pages[page_num].get("status"),
                    "is_blank": primary_pages[page_num].get("is_blank", False),
                    "uncertain_spans": primary_pages[page_num].get("uncertain_spans", []),
                },
                "layout": {
                    "is_blank": layout_pages[page_num].get("is_blank", False),
                    "blank_provenance": layout_pages[page_num].get("blank_provenance"),
                    "coordinate_space": layout_pages[page_num].get("coordinate_space"),
                    "blocks": layout_pages[page_num].get("blocks", []),
                    "tables": layout_pages[page_num].get("tables", []),
                },
                "verification": comparison,
            }
        )

    issue_counts = Counter(issue for page in pages for issue in page["verification"]["issues"])
    review_pages = [page["page_num"] for page in pages if page["verification"]["verdict"] != "pass"]
    return {
        "schema_version": "1.0",
        "document_id": primary["document_id"],
        "source": primary["source"],
        "primary_parser": primary.get("parser", {}),
        "layout_parser": layout.get("parser", {}),
        "verdict": "pass" if not review_pages else "review_required",
        "review_pages": review_pages,
        "issue_counts": dict(sorted(issue_counts.items())),
        "pages": pages,
        "pp_structure_v3": {
            "status": "deferred" if structural_review_pages else "not_required",
            "pages": structural_review_pages,
            "reason": (
                "Local runtime is unavailable; provision the optional remote layout verifier for flagged pages."
                if structural_review_pages
                else "Upstage layout checks did not trigger a structural-review threshold."
            ),
        },
    }
