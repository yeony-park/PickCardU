from __future__ import annotations

import hashlib
import math
import re
from difflib import SequenceMatcher
from typing import Any

from common import value_sha256
from verification import heading_blocks, normalize_text


CHUNKER_VERSION = "layout-parent-child-v2"
CHUNK_SUMMARY_SCHEMA_VERSION = "1.1"
CHUNK_CORPUS_FINGERPRINT_VERSION = "rag-chunk-corpus-v1"
CHUNK_CONFIG_FINGERPRINT_FIELDS = (
    "schema_version",
    "chunker_version",
    "child_max_chars",
    "child_overlap_chars",
    "upstream_primary_config_sha256",
    "upstream_layout_config_sha256",
)
CHUNK_BUILD_FINGERPRINT_FIELDS = (
    "schema_version",
    "chunker_version",
    "document_ids",
    "document_count",
    "expected_document_count",
    "parent_count",
    "child_count",
    "child_max_chars",
    "child_overlap_chars",
    "upstream_primary_config_sha256",
    "upstream_layout_config_sha256",
    "chunk_config_sha256",
    "source_corpus_sha256",
    "parent_corpus_sha256",
    "child_corpus_sha256",
    "chunk_corpus_fingerprint_version",
    "chunk_corpus_sha256",
)


def chunk_config_sha256(summary: dict[str, Any]) -> str:
    return value_sha256({key: summary.get(key) for key in CHUNK_CONFIG_FINGERPRINT_FIELDS})


def chunk_build_sha256(summary: dict[str, Any]) -> str:
    return value_sha256({key: summary.get(key) for key in CHUNK_BUILD_FINGERPRINT_FIELDS})


def chunk_rows_sha256(rows: list[dict[str, Any]]) -> str:
    return value_sha256(sorted(rows, key=lambda row: str(row.get("chunk_id", ""))))


def chunk_corpus_sha256(parents: list[dict[str, Any]], children: list[dict[str, Any]]) -> str:
    return value_sha256(
        {
            "version": CHUNK_CORPUS_FINGERPRINT_VERSION,
            "parents": sorted(parents, key=lambda row: str(row.get("chunk_id", ""))),
            "children": sorted(children, key=lambda row: str(row.get("chunk_id", ""))),
        }
    )


def stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()[:24]}"


def approximate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 2)) if text else 0


def paragraphs(text: str) -> list[str]:
    return [value.strip() for value in re.split(r"\n\s*\n", text) if value.strip()]


def first_line(value: str) -> str:
    return next((line.strip() for line in value.splitlines() if line.strip()), "")


def table_hints(blocks: list[dict[str, Any]]) -> list[str]:
    return [
        str(block.get("text", "")).strip()
        for block in blocks
        if str(block.get("type", "")).casefold() == "table" and str(block.get("text", "")).strip()
    ]


def heading_matches(text: str, layout_page: dict[str, Any]) -> list[tuple[int, int, str, float]]:
    values = paragraphs(text)
    blocks = sorted(layout_page.get("blocks", []), key=lambda block: block.get("reading_order", 0))
    block_positions = {block.get("block_id"): index for index, block in enumerate(blocks)}
    matches: list[tuple[int, int, str, float]] = []
    next_paragraph = 0
    for block in heading_blocks(layout_page):
        heading = normalize_text(str(block.get("text", "")))
        if not heading:
            continue
        candidates = []
        for index in range(next_paragraph, len(values)):
            candidate = normalize_text(first_line(values[index]))
            if not candidate:
                continue
            score = 1.0 if heading in candidate or candidate in heading else SequenceMatcher(None, heading, candidate).ratio()
            candidates.append((score, index))
        if not candidates:
            break
        score, index = max(candidates, key=lambda item: (item[0], -item[1]))
        if score < 0.58:
            continue
        matches.append((index, block_positions.get(block.get("block_id"), 0), first_line(values[index]), score))
        next_paragraph = index + 1
    return matches


def section_records(page: dict[str, Any], card_name: str) -> list[dict[str, Any]]:
    text = str(page.get("resolved_text", ""))
    values = paragraphs(text)
    if not values:
        return []
    layout = page.get("layout", {})
    blocks = sorted(layout.get("blocks", []), key=lambda block: block.get("reading_order", 0))
    matches = heading_matches(text, layout)
    sections = []

    if not matches:
        return [
            {
                "title": f"page {page['page_num']}",
                "text": "\n\n".join(values),
                "source_block_ids": [block.get("block_id") for block in blocks if block.get("block_id")],
                "table_hints": table_hints(blocks),
                "heading_alignment": 0.0,
                "quality_flags": ["page_parent_fallback"],
            }
        ]

    boundaries = []
    if matches[0][0] > 0:
        boundaries.append((0, matches[0][0], 0, matches[0][1], f"page {page['page_num']} preamble", 1.0))
    for match_index, (paragraph_index, block_index, title, score) in enumerate(matches):
        next_paragraph = matches[match_index + 1][0] if match_index + 1 < len(matches) else len(values)
        next_block = matches[match_index + 1][1] if match_index + 1 < len(matches) else len(blocks)
        boundaries.append((paragraph_index, next_paragraph, block_index, next_block, title, score))

    for paragraph_start, paragraph_end, block_start, block_end, title, score in boundaries:
        section_text = "\n\n".join(values[paragraph_start:paragraph_end]).strip()
        if not section_text:
            continue
        sections.append(
            {
                "title": title,
                "text": section_text,
                "source_block_ids": [
                    block.get("block_id") for block in blocks[block_start:block_end] if block.get("block_id")
                ],
                "table_hints": table_hints(blocks[block_start:block_end]),
                "heading_alignment": round(score, 6),
                "quality_flags": [] if score >= 0.7 else ["heading_alignment_low"],
            }
        )
    return sections


def is_markdown_table(unit: str) -> bool:
    lines = [line.strip() for line in unit.splitlines() if line.strip()]
    return len(lines) >= 2 and any(re.fullmatch(r"\|?\s*:?-{3,}.*", line) for line in lines) and all("|" in line for line in lines)


def matches_table_hint(unit: str, hints: list[str]) -> bool:
    normalized_unit = normalize_text(unit)
    if not normalized_unit:
        return False
    for hint in hints:
        normalized_hint = normalize_text(hint)
        if not normalized_hint:
            continue
        if min(len(normalized_unit), len(normalized_hint)) >= 4 and (
            normalized_unit in normalized_hint or normalized_hint in normalized_unit
        ):
            return True
        if SequenceMatcher(None, normalized_unit, normalized_hint).ratio() >= 0.45:
            return True
    return False


def child_texts(
    parent_text: str,
    max_chars: int,
    overlap_chars: int,
    layout_table_hints: list[str] | None = None,
) -> list[tuple[str, bool]]:
    layout_table_hints = layout_table_hints or []
    units = [
        (unit, is_markdown_table(unit) or matches_table_hint(unit, layout_table_hints))
        for unit in paragraphs(parent_text)
    ]
    if not units:
        return []
    children: list[tuple[str, bool]] = []
    current: list[str] = []

    def flush() -> None:
        nonlocal current
        value = "\n\n".join(current).strip()
        if value:
            children.append((value, False))
        current = []

    for unit, table in units:
        if table:
            flush()
            children.append((unit, True))
            continue

        if len(unit) > max_chars:
            flush()
            start = 0
            while start < len(unit):
                end = min(len(unit), start + max_chars)
                value = unit[start:end].strip()
                if value:
                    children.append((value, False))
                if end == len(unit):
                    break
                start = max(start + 1, end - overlap_chars)
            continue

        proposed = "\n\n".join([*current, unit])
        if current and len(proposed) > max_chars:
            previous = "\n\n".join(current).strip()
            flush()
            available_overlap = max(0, max_chars - len(unit) - 2)
            overlap_size = min(overlap_chars, available_overlap)
            overlap = previous[-overlap_size:].lstrip() if overlap_size else ""
            if overlap:
                current.append(overlap)
        current.append(unit)
        if len("\n\n".join(current)) >= max_chars:
            flush()
    flush()
    return children


def chunk_document(
    document: dict[str, Any],
    child_max_chars: int = 1600,
    child_overlap_chars: int = 160,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if child_max_chars < 1 or child_overlap_chars < 0 or child_overlap_chars >= child_max_chars:
        raise ValueError("invalid child chunk size or overlap")
    document_id = document["document_id"]
    source = document["source"]
    source_sha256 = source["sha256"]
    primary_config_sha256 = document.get("primary_parser", {}).get("config_sha256")
    layout_config_sha256 = document.get("layout_parser", {}).get("config_sha256")
    parents = []
    children = []
    parent_ordinal = 0
    child_ordinal = 0

    for page in document.get("pages", []):
        page_num = int(page["page_num"])
        verification = page.get("verification", {})
        page_flags = list(verification.get("issues", []))
        for section in section_records(page, source.get("card_name", document_id.split("/", 1)[-1])):
            parent_ordinal += 1
            parent_id = stable_id("par", source_sha256, CHUNKER_VERSION, page_num, parent_ordinal, section["title"])
            quality_flags = sorted(set([*page_flags, *section["quality_flags"]]))
            parent = {
                "schema_version": "1.0",
                "chunk_id": parent_id,
                "kind": "parent",
                "parent_id": None,
                "document_id": document_id,
                "issuer": source["issuer"],
                "card_name": source["card_name"],
                "source_path": source["path"],
                "source_sha256": source_sha256,
                "page_start": page_num,
                "page_end": page_num,
                "section_path": [source["card_name"], section["title"]],
                "text": section["text"],
                "token_count": approximate_tokens(section["text"]),
                "source_block_ids": section["source_block_ids"],
                "structure_source": "upstage",
                "text_source": "gpt-5.6-luna-200dpi",
                "verification_verdict": verification.get("verdict", "unknown"),
                "quality_flags": quality_flags,
                "chunker_version": CHUNKER_VERSION,
                "child_max_chars": child_max_chars,
                "child_overlap_chars": child_overlap_chars,
                "primary_config_sha256": primary_config_sha256,
                "layout_config_sha256": layout_config_sha256,
            }
            parents.append(parent)

            for child_index, (text, table_atomic) in enumerate(
                child_texts(section["text"], child_max_chars, child_overlap_chars, section["table_hints"]), start=1
            ):
                child_ordinal += 1
                child_id = stable_id("chi", source_sha256, CHUNKER_VERSION, parent_id, child_index, text)
                children.append(
                    {
                        **{key: value for key, value in parent.items() if key not in {"chunk_id", "kind", "parent_id", "text", "token_count"}},
                        "chunk_id": child_id,
                        "kind": "child",
                        "parent_id": parent_id,
                        "text": text,
                        "token_count": approximate_tokens(text),
                        "child_ordinal": child_ordinal,
                        "table_atomic": table_atomic,
                    }
                )

    parent_ids = {parent["chunk_id"] for parent in parents}
    if any(child["parent_id"] not in parent_ids for child in children):
        raise AssertionError("every child must reference a parent in the same corpus")
    return parents, children
