from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from chunking import (
    CHUNK_CORPUS_FINGERPRINT_VERSION,
    CHUNKER_VERSION,
    CHUNK_SUMMARY_SCHEMA_VERSION,
    chunk_build_sha256,
    chunk_config_sha256,
    chunk_corpus_sha256,
    chunk_document,
    chunk_rows_sha256,
)
from common import RAG_DIR, RUNTIME_DIR, discover_documents, read_json, value_sha256, write_json, write_jsonl
from run_luna_parse import config as luna_config
from run_upstage_validation import config as upstage_config


CANONICAL_DIR = RUNTIME_DIR / "canonical"
DEFAULT_OUTPUT_DIR = RUNTIME_DIR / "chunks"


def main() -> None:
    parser = argparse.ArgumentParser(description="Build layout-aware parent and child chunks.")
    parser.add_argument("--issuer", action="append", dest="issuers")
    parser.add_argument("--document", action="append", dest="documents")
    parser.add_argument("--child-max-chars", type=int, default=1600)
    parser.add_argument("--child-overlap-chars", type=int, default=160)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if (args.issuers or args.documents) and args.output_dir == DEFAULT_OUTPUT_DIR:
        raise SystemExit("filtered chunk builds require a separate --output-dir")
    source_documents = discover_documents(args.issuers, args.documents)

    parents = []
    children = []
    document_count = 0
    missing = []
    invalid = []
    current_documents = []
    for source_document in source_documents:
        path = CANONICAL_DIR / source_document.issuer / f"{source_document.card_name}.json"
        if not path.exists():
            missing.append(source_document.document_id)
            continue
        document = read_json(path)
        primary_parser = document.get("primary_parser", {})
        batch_pages = int(primary_parser.get("batch_pages", 0))
        luna_config_sha256 = value_sha256(luna_config(batch_pages)) if batch_pages > 0 else ""
        if (
            document.get("document_id") != source_document.document_id
            or document.get("source", {}).get("sha256") != source_document.sha256
            or primary_parser.get("config_sha256") != luna_config_sha256
            or document.get("layout_parser", {}).get("config_sha256") != value_sha256(upstage_config())
        ):
            invalid.append(source_document.document_id)
            continue
        current_documents.append((source_document, document))

    if (missing or invalid) and not args.allow_partial:
        raise SystemExit(
            f"full chunking requires all {len(source_documents)} current canonical documents: "
            f"{len(missing)} missing, {len(invalid)} stale or invalid"
        )

    for source_document, document in current_documents:
        document_parents, document_children = chunk_document(
            document,
            args.child_max_chars,
            args.child_overlap_chars,
        )
        parents.extend(document_parents)
        children.extend(document_children)
        document_count += 1
        print(
            json.dumps(
                {
                    "document_id": source_document.document_id,
                    "parents": len(document_parents),
                    "children": len(document_children),
                },
                ensure_ascii=False,
            )
        )

    chunk_dir = args.output_dir
    parent_count = write_jsonl(chunk_dir / "parents.jsonl", parents)
    child_count = write_jsonl(chunk_dir / "children.jsonl", children)
    document_ids = sorted(source_document.document_id for source_document, _ in current_documents)
    report = {
        "schema_version": CHUNK_SUMMARY_SCHEMA_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "chunker_version": CHUNKER_VERSION,
        "document_ids": document_ids,
        "document_count": document_count,
        "expected_document_count": len(source_documents),
        "missing_documents": missing,
        "invalid_documents": invalid,
        "parent_count": parent_count,
        "child_count": child_count,
        "child_max_chars": args.child_max_chars,
        "child_overlap_chars": args.child_overlap_chars,
        "upstream_primary_config_sha256": sorted(
            {document.get("primary_parser", {}).get("config_sha256") for _, document in current_documents}
        ),
        "upstream_layout_config_sha256": sorted(
            {document.get("layout_parser", {}).get("config_sha256") for _, document in current_documents}
        ),
        "source_corpus_sha256": value_sha256(
            [
                source_document.as_dict()
                for source_document, _ in sorted(current_documents, key=lambda item: item[0].document_id)
            ]
        ),
        "parent_corpus_sha256": chunk_rows_sha256(parents),
        "child_corpus_sha256": chunk_rows_sha256(children),
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": chunk_corpus_sha256(parents, children),
    }
    report["chunk_config_sha256"] = chunk_config_sha256(report)
    report["chunk_build_sha256"] = chunk_build_sha256(report)
    destination = RAG_DIR / "reports" / "chunk_summary.json"
    write_json(destination, report)
    print(f"{destination}: {parent_count} parents / {child_count} children")


if __name__ == "__main__":
    main()
