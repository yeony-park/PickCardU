"""Card/page/section/benefit chunks derived from OCR paragraphs."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any


BENEFIT_CHUNKING_CONTRACT = "ocr-paragraph-label-window-v1"
CARD_MAX_CHARS = 2_000
PAGE_MAX_CHARS = 6_000
SECTION_MAX_CHARS = 4_000
BENEFIT_MAX_CHARS = 3_000
PAGE_MARKER = re.compile(
    r"(?im)^(?:\[page\s*(\d+)\]|===\s*page\s*(\d+)\s*===)\s*$"
)
HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
LINE_ID = re.compile(r"^P(\d{4})-L\d{4}$")
FACT_FIELDS = (
    "benefit_type", "action", "target", "condition", "value", "unit",
    "cap", "frequency", "period", "exceptions",
)


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_pages(raw: str) -> dict[int, str]:
    matches = list(PAGE_MARKER.finditer(raw))
    if not matches:
        return {1: raw.strip()}
    pages: dict[int, str] = {}
    for index, match in enumerate(matches):
        page = int(match.group(1) or match.group(2))
        if page in pages:
            raise ValueError("OCR text contains duplicate page markers")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        pages[page] = raw[match.end():end].strip()
    return pages


def _split_sections(text: str) -> list[tuple[str, str]]:
    matches = list(HEADING.finditer(text))
    if not matches:
        return [("page_body", text.strip())]
    sections: list[tuple[str, str]] = []
    if text[:matches[0].start()].strip():
        sections.append(("page_intro", text[:matches[0].start()].strip()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        sections.append((match.group(1).strip(), text[match.start():end].strip()))
    return sections


def _bounded_parts(text: str, limit: int) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    parts: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(paragraph) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.extend(paragraph[index:index + limit] for index in range(0, len(paragraph), limit))
        elif current and len(current) + len(paragraph) + 2 > limit:
            parts.append(current)
            current = paragraph
        else:
            current = f"{current}\n\n{paragraph}".strip()
    if current:
        parts.append(current)
    return parts or ([text[:limit]] if text else [])


def _provider_fact(item: dict[str, Any], provider: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if isinstance(item.get("fact"), dict):
        return (
            item["fact"],
            item.get("field_evidence_refs", {}).get(provider, {}),
            item.get("relation_scope_refs", {}).get(provider, {}),
        )
    return item, item.get("field_evidence", {}), item.get("relation_scope", {})


def _label(item: dict[str, Any], provider: str) -> tuple[list[str], set[int]]:
    fact, fields, scope = _provider_fact(item, provider)
    values = [fact.get(field, "") for field in FACT_FIELDS]
    value, unit = fact.get("value", ""), fact.get("unit", "")
    if value and unit:
        values.extend((f"{value}{unit}", f"{value} {unit}"))
    line_ids: set[str] = set()
    for references in fields.values() if isinstance(fields, dict) else ():
        if not isinstance(references, list):
            continue
        for reference in references:
            if not isinstance(reference, dict):
                continue
            values.append(reference.get("fragment", ""))
            if isinstance(reference.get("line_id"), str):
                line_ids.add(reference["line_id"])
    if isinstance(scope, dict):
        line_ids.update(value for value in scope.get("line_ids", []) if isinstance(value, str))
    needles = list(dict.fromkeys(_normalized(value) for value in values if isinstance(value, str) and value.strip()))
    pages = {int(match.group(1)) for line_id in line_ids if (match := LINE_ID.fullmatch(line_id))}
    return needles, pages


def _record(
    *,
    document_id: str,
    issuer: str,
    card_name: str,
    level: str,
    local_key: str,
    text: str,
    source_pages: list[int],
    section: str | None = None,
    parent_id: str | None = None,
    child_ids: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not text.strip() or not source_pages:
        raise ValueError("benefit chunk requires OCR text and source page provenance")
    chunk_id = _sha256_text(f"{document_id}:{level}:{local_key}:{text}")[:32]
    path = " > ".join(value for value in (issuer, card_name, level, section) if value)
    metadata = {
        "document_id": document_id,
        "level": level,
        "issuer_name": issuer,
        "card_name": card_name,
        "section": section,
        "parent_id": parent_id,
        "child_ids": child_ids or [],
        "source_pages": sorted(set(source_pages)),
        "retrieval_text": text,
        "reranker_text": f"[문서 경로] {path}\n[본문]\n{text}",
        "evidence_refs": {},
        "related_chunk_ids": [],
    }
    metadata.update(extra or {})
    return {"chunk_id": chunk_id, "document_id": document_id, "level": level, "text": text, "metadata": metadata}


def build_benefit_chunks(
    raw: str,
    *,
    document_id: str,
    issuer: str,
    card_name: str,
    facts: list[dict[str, Any]],
    provider: str = "luna",
    ocr_text_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Build notebook-13-style OCR chunks without using JSON as searchable text."""
    if not all(isinstance(value, str) and value.strip() for value in (raw, document_id, issuer, card_name)):
        raise ValueError("benefit chunking requires OCR text and card identity")
    if provider not in {"luna", "upstage"} or not isinstance(facts, list):
        raise ValueError("benefit chunking provider or facts are invalid")
    pages = _parse_pages(raw)
    source_hash = ocr_text_sha256 or _sha256_text(raw)
    chunks: list[dict[str, Any]] = []
    first_lines = [
        line.strip() for line in raw.splitlines()
        if line.strip() and not PAGE_MARKER.fullmatch(line)
    ][:12]
    headings = [match.group(1).strip() for match in HEADING.finditer(raw)]
    card_text = "\n".join(dict.fromkeys([*first_lines, *headings]))[:CARD_MAX_CHARS]
    card = _record(
        document_id=document_id, issuer=issuer, card_name=card_name,
        level="card", local_key="card", text=card_text,
        source_pages=sorted(pages), extra={"ocr_text_sha256": source_hash},
    )
    chunks.append(card)

    section_records: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    section_texts: dict[int, list[tuple[str, str]]] = {}
    for page, page_text in pages.items():
        page_parts = _bounded_parts(page_text, PAGE_MAX_CHARS)
        for part_index, text in enumerate(page_parts, 1):
            chunks.append(_record(
                document_id=document_id, issuer=issuer, card_name=card_name,
                level="page", local_key=f"{page}:{part_index}", text=text,
                source_pages=[page], parent_id=card["chunk_id"],
                extra={"part_index": part_index, "part_count": len(page_parts), "ocr_text_sha256": source_hash},
            ))
        sections = _split_sections(page_text)
        section_texts[page] = sections
        for section_index, (section, section_text) in enumerate(sections, 1):
            for part_index, text in enumerate(_bounded_parts(section_text, SECTION_MAX_CHARS), 1):
                if not HEADING.sub("", text).strip():
                    continue
                record = _record(
                    document_id=document_id, issuer=issuer, card_name=card_name,
                    level="section", local_key=f"{page}:{section_index}:{part_index}", text=text,
                    source_pages=[page], section=section,
                    extra={"section_index": section_index, "part_index": part_index, "ocr_text_sha256": source_hash},
                )
                section_records[(page, section_index)].append(record)
                chunks.append(record)

    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    unmapped: list[int] = []
    ambiguous: list[int] = []
    truncated = 0
    for fact_index, item in enumerate(facts):
        if not isinstance(item, dict):
            unmapped.append(fact_index)
            continue
        needles, hinted_pages = _label(item, provider)
        candidates: list[tuple[int, int, str, int]] = []
        for page in sorted(hinted_pages or pages.keys()):
            if page not in pages:
                continue
            paragraphs = [part.strip() for part in re.split(r"\n\s*\n", pages[page]) if part.strip()]
            for paragraph_index, paragraph in enumerate(paragraphs):
                score = sum(needle in _normalized(paragraph) for needle in needles)
                candidates.append((score, page, paragraph, paragraph_index))
        best_score = max((row[0] for row in candidates), default=0)
        winners = [row for row in candidates if row[0] == best_score and best_score > 0]
        if not winners:
            unmapped.append(fact_index)
            continue
        if len(winners) != 1:
            ambiguous.append(fact_index)
            continue
        score, page, core, paragraph_index = winners[0]
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", pages[page]) if part.strip()]
        full_window = "\n\n".join(paragraphs[max(0, paragraph_index - 1):paragraph_index + 2])
        if len(full_window) > BENEFIT_MAX_CHARS:
            truncated += 1
        text = full_window[:BENEFIT_MAX_CHARS]
        section_index = next(
            (index for index, (_section, section_text) in enumerate(section_texts[page], 1) if _normalized(core) in _normalized(section_text)),
            1,
        )
        key = (page, _sha256_text(_normalized(text)))
        group = grouped.setdefault(key, {
            "text": text, "core": core, "page": page, "section_index": section_index,
            "fact_indices": [], "selector_scores": [],
        })
        group["fact_indices"].append(fact_index)
        group["selector_scores"].append(score)

    for (page, window_hash), group in sorted(grouped.items()):
        parents = section_records.get((page, group["section_index"]), [])
        parent = next(
            (row for row in parents if _normalized(group["core"]) in _normalized(row["text"])),
            parents[0] if parents else None,
        )
        section = parent["metadata"]["section"] if parent else None
        benefit = _record(
            document_id=document_id, issuer=issuer, card_name=card_name,
            level="benefit", local_key=f"{page}:{window_hash}", text=group["text"],
            source_pages=[page], section=section,
            parent_id=parent["chunk_id"] if parent else None,
            extra={
                "fact_indices": group["fact_indices"],
                "selector_scores": group["selector_scores"],
                "ocr_text_sha256": source_hash,
                "selection_method": "unique_positive_label_paragraph_with_page_neighbors",
            },
        )
        chunks.append(benefit)
        if parent:
            parent["metadata"]["child_ids"].append(benefit["chunk_id"])

    chunks.sort(key=lambda row: row["chunk_id"])
    if len({row["chunk_id"] for row in chunks}) != len(chunks):
        raise RuntimeError("benefit chunk IDs are not unique")
    audit = {
        "chunking_contract": BENEFIT_CHUNKING_CONTRACT,
        "ocr_text_sha256": source_hash,
        "pages": len(pages),
        "facts": len(facts),
        "mapped_facts": len(facts) - len(unmapped) - len(ambiguous),
        "unmapped_fact_indices": unmapped,
        "ambiguous_fact_indices": ambiguous,
        "merged_benefit_windows": sum(max(0, len(group["fact_indices"]) - 1) for group in grouped.values()),
        "truncated_benefit_windows": truncated,
        "level_counts": dict(Counter(row["level"] for row in chunks)),
    }
    for chunk in chunks:
        chunk["metadata"]["ocr_provider"] = provider
    card["metadata"]["chunking_audit"] = audit
    return chunks, audit
