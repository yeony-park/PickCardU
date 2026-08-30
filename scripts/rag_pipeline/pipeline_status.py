from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from chunking import CHUNKER_VERSION
from common import RAG_DIR, RUNTIME_DIR, discover_documents, read_json, read_jsonl, value_sha256
from run_luna_parse import complete_artifact as complete_luna_artifact
from run_luna_parse import config as luna_config
from run_upstage_validation import complete_artifact as complete_upstage_artifact
from run_upstage_validation import config as upstage_config


def current_luna_artifacts(documents) -> tuple[int, int]:
    document_count = 0
    page_count = 0
    for document in documents:
        path = RUNTIME_DIR / "luna_200dpi" / document.issuer / f"{document.card_name}.json"
        if not path.exists():
            continue
        try:
            artifact = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        batch_pages = int(artifact.get("parser", {}).get("batch_pages", 0))
        config_sha256 = value_sha256(luna_config(batch_pages)) if batch_pages > 0 else ""
        if complete_luna_artifact(path, document, config_sha256):
            document_count += 1
            page_count += document.page_count
    return document_count, page_count


def current_luna_batches(batch_pages: int = 6) -> dict[str, int]:
    config_sha256 = value_sha256(luna_config(batch_pages))
    completed_batches = 0
    failed_ranges = []
    completed_pages = set()
    for path in (RUNTIME_DIR / "luna_200dpi_batches").glob("*/*/*.json"):
        try:
            artifact = read_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        if artifact.get("config_sha256") != config_sha256:
            continue
        if artifact.get("run_status") == "completed":
            completed_batches += 1
            completed_pages.update(
                (artifact.get("document_id"), int(page["page_num"]))
                for page in artifact.get("pages", [])
            )
        else:
            failed_ranges.append(
                (
                    artifact.get("document_id"),
                    int(artifact.get("page_start", 0)),
                    int(artifact.get("page_end", 0)),
                )
            )
    unresolved_failed_batches = sum(
        any((document_id, page_num) not in completed_pages for page_num in range(page_start, page_end + 1))
        for document_id, page_start, page_end in failed_ranges
    )
    return {
        "completed_batches": completed_batches,
        "checkpoint_pages": len(completed_pages),
        "failed_batches": unresolved_failed_batches,
    }


def current_upstage_artifacts(documents) -> tuple[int, int]:
    document_count = 0
    page_count = 0
    config_sha256 = value_sha256(upstage_config())
    for document in documents:
        path = RUNTIME_DIR / "upstage" / document.issuer / f"{document.card_name}.json"
        if complete_upstage_artifact(path, document, config_sha256):
            document_count += 1
            page_count += document.page_count
    return document_count, page_count


def index_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {"parents": 0, "children": 0, "embedded_children": 0}
    connection = sqlite3.connect(path)
    try:
        return {
            "parents": connection.execute("SELECT count(*) FROM parents").fetchone()[0],
            "children": connection.execute("SELECT count(*) FROM children").fetchone()[0],
            "embedded_children": connection.execute("SELECT count(*) FROM children WHERE embedding IS NOT NULL").fetchone()[0],
        }
    except sqlite3.DatabaseError:
        return {"parents": 0, "children": 0, "embedded_children": 0}
    finally:
        connection.close()


def chunk_status(
    source_hashes: dict[str, str],
    upstream_configs: dict[str, tuple[str, str]],
) -> tuple[dict[str, Any], str | None]:
    parents = read_jsonl(RUNTIME_DIR / "chunks" / "parents.jsonl")
    children = read_jsonl(RUNTIME_DIR / "chunks" / "children.jsonl")
    expected_documents = set(source_hashes)
    valid_rows = all(
        row.get("source_sha256") == source_hashes.get(row.get("document_id"))
        and row.get("chunker_version") == CHUNKER_VERSION
        and (
            row.get("primary_config_sha256"),
            row.get("layout_config_sha256"),
        )
        == upstream_configs.get(row.get("document_id"))
        for row in [*parents, *children]
    )
    covered_parents = {row.get("document_id") for row in parents}
    covered_children = {row.get("document_id") for row in children}
    current = valid_rows and covered_parents == expected_documents and covered_children == expected_documents
    result = {
        "parents": len(parents) if current else 0,
        "children": len(children) if current else 0,
        "stale": not current and bool(parents or children),
        "stored_parents": len(parents),
        "stored_children": len(children),
    }
    fingerprint = None
    if current:
        fingerprint = value_sha256(
            [
                (
                    row["chunk_id"],
                    row["parent_id"],
                    row["document_id"],
                    row["page_start"],
                    row["page_end"],
                    row["text"],
                )
                for row in sorted(children, key=lambda item: item["chunk_id"])
            ]
        )
    return result, fingerprint


def current_index_status(path: Path, chunk_fingerprint: str | None) -> dict[str, Any]:
    stored = index_counts(path)
    result = {
        "parents": 0,
        "children": 0,
        "embedded_children": 0,
        "stale": bool(stored["parents"] or stored["children"]),
        "stored_parents": stored["parents"],
        "stored_children": stored["children"],
        "stored_embedded_children": stored["embedded_children"],
    }
    if not path.exists() or chunk_fingerprint is None:
        return result
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT child_id, parent_id, document_id, page_start, page_end, text FROM children ORDER BY child_id"
        ).fetchall()
    except sqlite3.DatabaseError:
        return result
    finally:
        connection.close()
    if value_sha256(rows) == chunk_fingerprint:
        result.update({**stored, "stale": False})
    return result


def status() -> dict[str, Any]:
    documents = discover_documents()
    total_pages = sum(document.page_count for document in documents)
    luna_documents, luna_pages = current_luna_artifacts(documents)
    luna_batches = current_luna_batches()
    upstage_documents, upstage_pages = current_upstage_artifacts(documents)
    source_hashes = {document.document_id: document.sha256 for document in documents}
    canonical_paths = []
    canonical_configs = {}
    current_upstage_sha256 = value_sha256(upstage_config())
    for path in (RUNTIME_DIR / "canonical").glob("*/*.json"):
        artifact = read_json(path)
        primary_parser = artifact.get("primary_parser", {})
        batch_pages = int(primary_parser.get("batch_pages", 0))
        current_luna_sha256 = value_sha256(luna_config(batch_pages)) if batch_pages > 0 else ""
        if (
            artifact.get("source", {}).get("sha256") == source_hashes.get(artifact.get("document_id"))
            and primary_parser.get("config_sha256") == current_luna_sha256
            and artifact.get("layout_parser", {}).get("config_sha256") == current_upstage_sha256
        ):
            canonical_paths.append(path)
            canonical_configs[artifact["document_id"]] = (
                primary_parser["config_sha256"],
                artifact["layout_parser"]["config_sha256"],
            )
    pp_pages = 0
    review_documents = 0
    for path in canonical_paths:
        artifact = read_json(path)
        pp_pages += len(artifact.get("pp_structure_v3", {}).get("pages", []))
        review_documents += artifact.get("verdict") == "review_required"
    chunks, chunk_fingerprint = chunk_status(source_hashes, canonical_configs)
    return {
        "source": {"documents": len(documents), "pages": total_pages},
        "luna_200dpi": {
            "completed_documents": luna_documents,
            "completed_pages": luna_pages,
            **luna_batches,
            "remaining_documents": len(documents) - luna_documents,
            "remaining_pages": total_pages - luna_pages,
        },
        "upstage": {
            "completed_documents": upstage_documents,
            "completed_pages": upstage_pages,
            "remaining_documents": len(documents) - upstage_documents,
            "remaining_pages": total_pages - upstage_pages,
        },
        "canonical": {
            "documents": len(canonical_paths),
            "review_required_documents": review_documents,
            "pp_structure_v3_deferred_pages": pp_pages,
        },
        "chunks": chunks,
        "index": current_index_status(RUNTIME_DIR / "hybrid_index.sqlite3", chunk_fingerprint),
        "reports": sorted(path.relative_to(RAG_DIR).as_posix() for path in (RAG_DIR / "reports").glob("*.json")),
    }


if __name__ == "__main__":
    print(json.dumps(status(), ensure_ascii=False, indent=2))
