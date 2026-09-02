"""Heading-tree chunks used by the historical parent-child retrieval package."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from typing import Any


STRUCTURAL_CONTRACT = "structural_heading_parent_child_v1"
MAX_BODY_CHARS = 4_000
PAGE_RE = re.compile(r"^\[page\s*(\d+)\]\s*$", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def render_pages(pages: list[dict[str, Any]]) -> str:
    """Render validated OCR pages in the historical parser's page-marker format."""
    rendered: list[str] = []
    seen: set[int] = set()
    for row in sorted(pages, key=lambda item: item.get("page", 0)):
        page, text = row.get("page"), row.get("text")
        if isinstance(page, bool) or not isinstance(page, int) or page < 1 or page in seen:
            raise ValueError("structural OCR pages require unique positive page numbers")
        if not isinstance(text, str):
            raise ValueError("structural OCR page text must be a string")
        seen.add(page)
        rendered.append(f"[page {page}]\n{text}")
    if not rendered:
        raise ValueError("structural OCR pages cannot be empty")
    return "\n".join(rendered)


def _rendered_chars(records: list[dict[str, Any]]) -> int:
    return len("\n".join(record["text"] for record in records))


def _split_records(records: list[dict[str, Any]]) -> tuple[list[list[dict[str, Any]]], str]:
    if not records or not any(record["text"].strip() for record in records):
        raise ValueError("structural body must contain visible text")
    paragraphs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for record in records:
        current.append(record)
        if not record["text"].strip():
            paragraphs.append(current)
            current = []
    if current:
        paragraphs.append(current)

    units: list[list[dict[str, Any]]] = []
    line_fallback = False
    for paragraph in paragraphs:
        if _rendered_chars(paragraph) <= MAX_BODY_CHARS:
            units.append(paragraph)
            continue
        line_fallback = True
        group: list[dict[str, Any]] = []
        for record in paragraph:
            if len(record["text"]) > MAX_BODY_CHARS:
                raise ValueError("a structural source line exceeds 4000 characters")
            proposed = [*group, record]
            if group and _rendered_chars(proposed) > MAX_BODY_CHARS:
                units.append(group)
                group = [record]
            else:
                group = proposed
        if group:
            units.append(group)

    parts: list[list[dict[str, Any]]] = []
    packed: list[dict[str, Any]] = []
    for unit in units:
        proposed = [*packed, *unit]
        if packed and _rendered_chars(proposed) > MAX_BODY_CHARS:
            parts.append(packed)
            packed = list(unit)
        else:
            packed = proposed
    if packed:
        parts.append(packed)
    if any(_rendered_chars(part) > MAX_BODY_CHARS for part in parts):
        raise RuntimeError("structural chunk size contract failed")
    return parts, "line_boundary_fallback" if line_fallback else "paragraph_boundary"


def build_structural_chunks(
    raw: str,
    *,
    document_id: str,
    issuer: str,
    card_name: str,
    source_sha256: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Reproduce notebook 22's direct-body heading hierarchy and chunk contract."""
    if not all(isinstance(value, str) and value.strip() for value in (raw, document_id, issuer, card_name)):
        raise ValueError("structural chunking requires raw text and card identity")
    source_hash = source_sha256 or _sha256_text(raw)
    root_id = f"{document_id}::node0000"
    nodes: list[dict[str, Any]] = [{
        "node_id": root_id,
        "parent_id": None,
        "heading_text": None,
        "heading_level": 0,
        "heading_path": [],
        "heading_line_number": None,
        "heading_page": None,
        "records": [],
    }]
    stack: list[dict[str, Any]] = []
    current_node = nodes[0]
    current_page = 1
    assignments: dict[int, str] = {}

    lines = raw.splitlines()
    for line_number, line in enumerate(lines, 1):
        page_match = PAGE_RE.fullmatch(line)
        if page_match:
            current_page = int(page_match.group(1))
            assignments[line_number] = "page_marker"
            continue
        heading_match = HEADING_RE.fullmatch(line)
        if heading_match:
            level = len(heading_match.group(1))
            while stack and stack[-1]["heading_level"] >= level:
                stack.pop()
            parent = stack[-1] if stack else nodes[0]
            heading_text = heading_match.group(2).strip()
            current_node = {
                "node_id": f"{document_id}::node{len(nodes):04d}",
                "parent_id": parent["node_id"],
                "heading_text": heading_text,
                "heading_level": level,
                "heading_path": [*parent["heading_path"], heading_text],
                "heading_line_number": line_number,
                "heading_page": current_page,
                "records": [],
            }
            nodes.append(current_node)
            stack.append(current_node)
            assignments[line_number] = "heading"
            continue
        current_node["records"].append({"line_number": line_number, "page": current_page, "text": line})
        assignments[line_number] = "direct_body"

    if len(assignments) != len(lines):
        raise RuntimeError("structural parser did not assign every source line")

    chunks: list[dict[str, Any]] = []
    hierarchy: list[dict[str, Any]] = []
    covered: list[int] = []
    for node in nodes:
        records = node["records"]
        substantive = bool(records and any(record["text"].strip() for record in records))
        parts, split_method = (_split_records(records) if substantive else ([], "not_applicable"))
        chunk_ids: list[str] = []
        for part_index, part in enumerate(parts, 1):
            body = "\n".join(record["text"] for record in part)
            path_text = " > ".join(node["heading_path"])
            retrieval_text = "\n".join([issuer, card_name, *([path_text] if path_text else []), body])
            evidence_text = "\n".join([*([path_text] if path_text else []), body])
            pages = sorted({record["page"] for record in part})
            body_hash = _sha256_text(body)
            seed = _canonical({
                "experiment": "구조 기반 청킹 + 제목 경로 검색문",
                "source_hash": source_hash,
                "node_id": node["node_id"],
                "part_index": part_index,
                "body_sha256": body_hash,
            })
            chunk_id = "shc_" + _sha256_text(seed)[:24]
            chunk_ids.append(chunk_id)
            covered.extend(record["line_number"] for record in part)
            chunks.append({
                "chunk_id": chunk_id,
                "document_id": document_id,
                "level": "structural",
                "text": evidence_text,
                "metadata": {
                    "document_id": document_id,
                    "level": "structural",
                    "issuer_name": issuer,
                    "card_name": card_name,
                    "section": path_text or None,
                    "node_id": node["node_id"],
                    "parent_id": node["parent_id"],
                    "heading_path": list(node["heading_path"]),
                    "heading_line_number": node["heading_line_number"],
                    "part_index": part_index,
                    "part_count": len(parts),
                    "source_pages": pages,
                    "retrieval_text": retrieval_text,
                    "reranker_text": retrieval_text,
                    "body": body,
                    "body_sha256": body_hash,
                    "source_sha256": source_hash,
                    "split_method": split_method,
                    "related_chunk_ids": [],
                    "optional_parent_heading": None,
                    "child_ids": [],
                },
            })

        node_pages = sorted(
            ({node["heading_page"]} if node["heading_page"] is not None else set())
            | {record["page"] for record in records}
        )
        hierarchy.append({
            "document_id": document_id,
            "node_id": node["node_id"],
            "parent_id": node["parent_id"],
            "heading_text": node["heading_text"],
            "heading_level": node["heading_level"],
            "heading_path": list(node["heading_path"]),
            "heading_line_number": node["heading_line_number"],
            "heading_page": node["heading_page"],
            "page_numbers": node_pages,
            "heading_only": not substantive,
            "search_chunk_ids": chunk_ids,
        })

    expected = sorted(
        record["line_number"]
        for node in nodes
        if node["records"] and any(record["text"].strip() for record in node["records"])
        for record in node["records"]
    )
    if sorted(covered) != expected or len(covered) != len(set(covered)):
        raise RuntimeError("structural direct-body lines are not covered exactly once")
    _attach_one_hop_neighbors(chunks, hierarchy)
    counts = Counter(assignments.values())
    return chunks, hierarchy, {
        "contract": STRUCTURAL_CONTRACT,
        "source_sha256": source_hash,
        "raw_lines": len(lines),
        "page_marker_lines": counts["page_marker"],
        "heading_lines": counts["heading"],
        "direct_body_lines": counts["direct_body"],
        "hierarchy_nodes": len(hierarchy),
        "heading_only_nodes": sum(row["heading_only"] for row in hierarchy),
        "search_chunks": len(chunks),
    }


def _attach_one_hop_neighbors(chunks: list[dict[str, Any]], hierarchy: list[dict[str, Any]]) -> None:
    """Precompute the historical deterministic same-card 1-hop expansion."""
    chunks_by_id = {chunk["chunk_id"]: chunk for chunk in chunks}
    nodes = {node["node_id"]: node for node in hierarchy}
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in hierarchy:
        if node["parent_id"] is not None:
            children[node["parent_id"]].append(node)

    for chunk in chunks:
        metadata = chunk["metadata"]
        node = nodes[metadata["node_id"]]
        related: list[str] = []

        def add(chunk_id: str) -> None:
            if chunk_id != chunk["chunk_id"] and chunk_id not in related:
                related.append(chunk_id)

        same = [chunks_by_id[item] for item in node["search_chunk_ids"] if item != chunk["chunk_id"]]
        same.sort(key=lambda item: (
            abs(item["metadata"]["part_index"] - metadata["part_index"]),
            item["metadata"]["part_index"],
            item["chunk_id"],
        ))
        for item in same:
            add(item["chunk_id"])

        parent = nodes.get(node["parent_id"])
        if parent is not None and parent["parent_id"] is not None:
            if not parent["heading_only"]:
                for chunk_id in parent["search_chunk_ids"]:
                    add(chunk_id)
            else:
                metadata["optional_parent_heading"] = parent["heading_text"]
                seed_line = node["heading_line_number"] if node["heading_line_number"] is not None else 10**12
                siblings = [
                    item for item in children[parent["node_id"]]
                    if not item["heading_only"] and item["search_chunk_ids"]
                ]
                siblings.sort(key=lambda item: (
                    abs((item["heading_line_number"] if item["heading_line_number"] is not None else 10**12) - seed_line),
                    item["heading_line_number"] if item["heading_line_number"] is not None else 10**12,
                    item["node_id"],
                ))
                for sibling in siblings:
                    for chunk_id in sibling["search_chunk_ids"]:
                        add(chunk_id)

        if node["parent_id"] is not None:
            for child in children[node["node_id"]]:
                if not child["heading_only"]:
                    for chunk_id in child["search_chunk_ids"]:
                        add(chunk_id)
        metadata["related_chunk_ids"] = related
