from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from chunking import (
    CHUNK_CORPUS_FINGERPRINT_VERSION,
    CHUNKER_VERSION,
    CHUNK_SUMMARY_SCHEMA_VERSION,
    chunk_build_sha256,
    chunk_config_sha256,
    chunk_corpus_sha256,
    chunk_rows_sha256,
)
from common import RAG_DIR, RUNTIME_DIR, discover_documents, read_json, read_jsonl, value_sha256, write_json
from generation import GENERATION_MODEL, answer_question
from hybrid_index import EMBEDDING_MODEL, HybridIndex, child_index_fingerprint
from openai_client import OpenAIClient
from run_luna_parse import config as luna_config
from run_upstage_validation import config as upstage_config


INDEX_PATH = RUNTIME_DIR / "hybrid_index.sqlite3"
PARENTS_PATH = RUNTIME_DIR / "chunks" / "parents.jsonl"
CHILDREN_PATH = RUNTIME_DIR / "chunks" / "children.jsonl"
CHUNK_SUMMARY_PATH = RAG_DIR / "reports" / "chunk_summary.json"
CANONICAL_DIR = RUNTIME_DIR / "canonical"
DEFAULT_QUERY_PATH = RAG_DIR / "eval" / "gold_queries.jsonl"
INDEX_METADATA_SCHEMA_VERSION = "1.0"


def validate_chunk_corpus(
    parents_path: Path,
    children_path: Path,
    allow_partial: bool,
    summary_path: Path = CHUNK_SUMMARY_PATH,
) -> dict[str, Any]:
    if not parents_path.is_file() or not children_path.is_file():
        raise ValueError("chunk JSONL files are missing; rerun build_chunks.py")
    if not summary_path.is_file():
        raise ValueError("chunk summary is missing; rerun build_chunks.py")
    parents = read_jsonl(parents_path)
    children = read_jsonl(children_path)
    if not parents or not children:
        raise ValueError("chunk JSONL files must contain parents and children")
    try:
        summary = read_json(summary_path)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("chunk summary is unreadable; rerun build_chunks.py") from error
    if not isinstance(summary, dict):
        raise ValueError("chunk summary is invalid; rerun build_chunks.py")

    documents = discover_documents()
    expected = {document.document_id: document for document in documents}
    document_ids = summary.get("document_ids")
    if (
        not isinstance(document_ids, list)
        or not document_ids
        or any(not isinstance(document_id, str) or document_id not in expected for document_id in document_ids)
        or document_ids != sorted(set(document_ids))
    ):
        raise ValueError("chunk summary contains invalid document_ids; rerun build_chunks.py")
    summary_documents = {document_id: expected[document_id] for document_id in document_ids}
    if not allow_partial and set(document_ids) != set(expected):
        raise ValueError(
            f"full index requires {len(expected)} documents; chunk summary covers {len(document_ids)}"
        )

    child_max_chars = summary.get("child_max_chars")
    child_overlap_chars = summary.get("child_overlap_chars")
    if (
        type(child_max_chars) is not int
        or type(child_overlap_chars) is not int
        or child_max_chars < 1
        or child_overlap_chars < 0
        or child_overlap_chars >= child_max_chars
    ):
        raise ValueError("chunk summary contains invalid child chunk sizes; rerun build_chunks.py")

    current_layout_config_sha256 = value_sha256(upstage_config())
    upstream_configs: dict[str, tuple[str, str]] = {}
    for document_id, source_document in summary_documents.items():
        canonical_path = CANONICAL_DIR / source_document.issuer / f"{source_document.card_name}.json"
        try:
            canonical = read_json(canonical_path)
            primary_parser = canonical.get("primary_parser", {})
            batch_pages = int(primary_parser.get("batch_pages", 0))
        except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as error:
            raise ValueError(f"current canonical artifact is missing or invalid: {document_id}") from error
        current_primary_config_sha256 = value_sha256(luna_config(batch_pages)) if batch_pages > 0 else ""
        if (
            canonical.get("document_id") != document_id
            or canonical.get("source", {}).get("sha256") != source_document.sha256
            or primary_parser.get("config_sha256") != current_primary_config_sha256
            or canonical.get("layout_parser", {}).get("config_sha256") != current_layout_config_sha256
        ):
            raise ValueError(f"canonical artifact does not match current source/config: {document_id}")
        upstream_configs[document_id] = (current_primary_config_sha256, current_layout_config_sha256)

    parent_ids = [row.get("chunk_id") for row in parents]
    child_ids = [row.get("chunk_id") for row in children]
    if any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in [*parent_ids, *child_ids]):
        raise ValueError("chunks contain missing chunk IDs")
    if len(set(parent_ids)) != len(parent_ids) or len(set(child_ids)) != len(child_ids):
        raise ValueError("chunks contain duplicate chunk IDs")
    parent_id_set = set(parent_ids)
    invalid = []
    for row in [*parents, *children]:
        document_id = row.get("document_id")
        source_document = summary_documents.get(document_id)
        if (
            source_document is None
            or row.get("source_sha256") != source_document.sha256
            or row.get("chunker_version") != CHUNKER_VERSION
            or row.get("child_max_chars") != child_max_chars
            or row.get("child_overlap_chars") != child_overlap_chars
            or (row.get("primary_config_sha256"), row.get("layout_config_sha256"))
            != upstream_configs.get(document_id)
        ):
            invalid.append(row.get("chunk_id"))
    if invalid:
        raise ValueError(f"chunks contain stale source/config metadata: {invalid[:3]}")
    if any(row.get("kind") != "parent" or row.get("parent_id") is not None for row in parents):
        raise ValueError("parent chunks contain invalid kind or parent_id")
    if any(row.get("kind") != "child" or row.get("parent_id") not in parent_id_set for row in children):
        raise ValueError("child chunks contain invalid or missing parent references")
    if any(not row.get("table_atomic") and len(str(row.get("text", ""))) > child_max_chars for row in children):
        raise ValueError("non-table child chunks exceed child_max_chars")

    parent_documents = {row["document_id"] for row in parents}
    child_documents = {row["document_id"] for row in children}
    if parent_documents != set(document_ids) or child_documents != set(document_ids):
        raise ValueError(
            f"chunk summary covers {len(document_ids)} documents; "
            f"parents cover {len(parent_documents)}, children cover {len(child_documents)}"
        )

    expected_primary_configs = sorted({value[0] for value in upstream_configs.values()})
    expected_layout_configs = sorted({value[1] for value in upstream_configs.values()})
    source_corpus_sha256 = value_sha256(
        [summary_documents[document_id].as_dict() for document_id in sorted(summary_documents)]
    )
    parent_corpus_sha256 = chunk_rows_sha256(parents)
    child_corpus_sha256 = chunk_rows_sha256(children)
    current_chunk_corpus_sha256 = chunk_corpus_sha256(parents, children)
    expected_document_count = summary.get("expected_document_count")
    valid_expected_document_count = (
        type(expected_document_count) is int
        and len(document_ids) <= expected_document_count <= len(expected)
        and (allow_partial or expected_document_count == len(expected))
    )
    if (
        summary.get("schema_version") != CHUNK_SUMMARY_SCHEMA_VERSION
        or summary.get("chunker_version") != CHUNKER_VERSION
        or summary.get("document_count") != len(document_ids)
        or not valid_expected_document_count
        or summary.get("parent_count") != len(parents)
        or summary.get("child_count") != len(children)
        or summary.get("upstream_primary_config_sha256") != expected_primary_configs
        or summary.get("upstream_layout_config_sha256") != expected_layout_configs
        or summary.get("source_corpus_sha256") != source_corpus_sha256
        or summary.get("parent_corpus_sha256") != parent_corpus_sha256
        or summary.get("child_corpus_sha256") != child_corpus_sha256
        or summary.get("chunk_corpus_fingerprint_version") != CHUNK_CORPUS_FINGERPRINT_VERSION
        or summary.get("chunk_corpus_sha256") != current_chunk_corpus_sha256
        or summary.get("chunk_config_sha256") != chunk_config_sha256(summary)
        or summary.get("chunk_build_sha256") != chunk_build_sha256(summary)
    ):
        raise ValueError("chunk summary does not match current source/config/JSONL; rerun build_chunks.py")

    index_metadata = {
        "schema_version": INDEX_METADATA_SCHEMA_VERSION,
        "chunk_summary_sha256": value_sha256(summary),
        "chunk_build_sha256": summary["chunk_build_sha256"],
        "chunk_config_sha256": summary["chunk_config_sha256"],
        "chunker_version": CHUNKER_VERSION,
        "child_max_chars": child_max_chars,
        "child_overlap_chars": child_overlap_chars,
        "upstream_primary_config_sha256": expected_primary_configs,
        "upstream_layout_config_sha256": expected_layout_configs,
        "source_corpus_sha256": source_corpus_sha256,
        "parent_corpus_sha256": parent_corpus_sha256,
        "child_corpus_sha256": child_corpus_sha256,
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": current_chunk_corpus_sha256,
        "index_corpus_sha256": child_index_fingerprint(children),
    }
    return {
        "expected_documents": len(expected),
        "indexed_documents": len(parent_documents),
        "index_metadata": index_metadata,
    }


def validate_query_corpus(queries: list[dict[str, Any]]) -> str:
    if not queries:
        raise ValueError("evaluation query set is empty; rerun build_eval_queries.py")
    documents = {document.document_id: document for document in discover_documents()}
    seen = set()
    for query in queries:
        query_id = query.get("query_id")
        document = documents.get(query.get("expected_document_id"))
        try:
            expected_page = int(query.get("expected_page"))
        except (TypeError, ValueError) as error:
            raise ValueError(f"evaluation query has an invalid expected_page: {query_id}") from error
        if (
            not isinstance(query_id, str)
            or not query_id
            or query_id in seen
            or not isinstance(query.get("question"), str)
            or not query["question"].strip()
            or document is None
            or not 1 <= expected_page <= document.page_count
            or not isinstance(query.get("expected_terms"), list)
            or not query["expected_terms"]
        ):
            raise ValueError(f"evaluation query is stale or invalid: {query_id}")
        seen.add(query_id)
    return value_sha256(queries)


def query_embedding(client: OpenAIClient, query: str, model: str) -> list[float]:
    embeddings, _ = client.embeddings([query], model=model)
    return embeddings[0]


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(fraction * len(ordered)) - 1))
    return ordered[index]


def relevant_rank(result: dict[str, Any], query: dict[str, Any]) -> int | None:
    expected_document = query["expected_document_id"]
    expected_page = int(query["expected_page"])
    expected_terms = [
        "".join(str(term).casefold().split())
        for term in query.get("expected_terms", [])
        if str(term).strip()
    ]
    for parent in result["parents"]:
        parent_text = "".join(str(parent.get("text", "")).casefold().split())
        if (
            parent["document_id"] == expected_document
            and int(parent["page_start"]) <= expected_page <= int(parent["page_end"])
            and (not expected_terms or any(term in parent_text for term in expected_terms))
        ):
            return int(parent["rank"])
    return None


def evaluate(
    index: HybridIndex,
    queries: list[dict[str, Any]],
    client: OpenAIClient | None,
    modes: list[str],
    alphas: list[float],
    embedding_model: str,
    top_k: int,
    candidate_k: int,
) -> dict[str, Any]:
    if any(not isinstance(query.get("expected_terms"), list) or not query["expected_terms"] for query in queries):
        raise ValueError("every evaluation query requires non-empty expected_terms; rebuild the query set")
    needs_vectors = any(mode != "keyword" for mode in modes)
    vectors: dict[str, list[float]] = {}
    embedding_usage = 0
    query_embedding_batch_latency_ms = 0.0
    embedding_status = None
    if needs_vectors:
        if client is None:
            raise ValueError("vector evaluation requires OPENAI_API_KEY")
        embedding_status = index.require_embedding_coverage(embedding_model)
        questions = [query["question"] for query in queries]
        embedding_started = time.perf_counter()
        for offset in range(0, len(questions), 128):
            batch = questions[offset : offset + 128]
            values, usage = client.embeddings(batch, model=embedding_model)
            vectors.update({queries[offset + index]["query_id"]: value for index, value in enumerate(values)})
            embedding_usage += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
        query_embedding_batch_latency_ms = round((time.perf_counter() - embedding_started) * 1000, 3)

    conditions = []
    for mode in modes:
        if mode == "weighted":
            conditions.extend((mode, alpha) for alpha in alphas)
        else:
            conditions.append((mode, 0.5))

    scores: dict[str, dict[str, Any]] = {}
    for mode, alpha in conditions:
        name = f"weighted_alpha_{alpha:g}" if mode == "weighted" else mode
        ranks = []
        latencies = []
        details = []
        for query in queries:
            result = index.search(
                query["question"],
                mode=mode,
                top_k=top_k,
                candidate_k=candidate_k,
                query_vector=vectors.get(query["query_id"]),
                alpha=alpha,
            )
            rank = relevant_rank(result, query)
            ranks.append(rank)
            latencies.append(float(result["latency_ms"]))
            details.append({"query_id": query["query_id"], "rank": rank})
        count = len(queries)
        scores[name] = {
            "query_count": count,
            "recall_at_1": sum(rank is not None and rank <= 1 for rank in ranks) / count if count else 0.0,
            "recall_at_3": sum(rank is not None and rank <= 3 for rank in ranks) / count if count else 0.0,
            "recall_at_5": sum(rank is not None and rank <= 5 for rank in ranks) / count if count else 0.0,
            "mrr_at_10": sum(1 / rank for rank in ranks if rank is not None and rank <= 10) / count if count else 0.0,
            "ndcg_at_10": sum(1 / math.log2(rank + 1) for rank in ranks if rank is not None and rank <= 10) / count if count else 0.0,
            "latency_ms_p50": statistics.median(latencies) if latencies else None,
            "latency_ms_p95": percentile(latencies, 0.95),
            "details": details,
        }
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "embedding_model": embedding_model,
        "embedding_status": embedding_status,
        "index_corpus_sha256": index.corpus_fingerprint(),
        "query_set_sha256": value_sha256(queries),
        "query_embedding_tokens": embedding_usage,
        "query_embedding_batch_latency_ms": query_embedding_batch_latency_ms,
        "condition_latency_scope": "local index search only; query embedding batch latency is reported separately",
        "conditions": scores,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build, search, evaluate, and generate with the hybrid RAG index.")
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build-index")
    build.add_argument("--parents", type=Path, default=PARENTS_PATH)
    build.add_argument("--children", type=Path, default=CHILDREN_PATH)
    build.add_argument("--chunk-summary", type=Path, default=CHUNK_SUMMARY_PATH)
    build.add_argument("--embed", action="store_true", help="Upload child text to the OpenAI Embeddings API.")
    build.add_argument("--confirm-external-upload", action="store_true")
    build.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    build.add_argument("--embedding-batch-size", type=int, default=64)
    build.add_argument("--allow-partial", action="store_true")

    search = subparsers.add_parser("search")
    search.add_argument("query")
    search.add_argument("--mode", choices=["keyword", "vector", "hybrid", "weighted"], default="hybrid")
    search.add_argument("--top-k", type=int, default=5)
    search.add_argument("--candidate-k", type=int, default=50)
    search.add_argument("--alpha", type=float, default=0.5)
    search.add_argument("--issuer")
    search.add_argument("--card-name")
    search.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    search.add_argument("--confirm-external-upload", action="store_true")
    search.add_argument("--parents", type=Path, default=PARENTS_PATH)
    search.add_argument("--children", type=Path, default=CHILDREN_PATH)
    search.add_argument("--chunk-summary", type=Path, default=CHUNK_SUMMARY_PATH)

    evaluation = subparsers.add_parser("evaluate")
    evaluation.add_argument("--queries", type=Path, default=DEFAULT_QUERY_PATH)
    evaluation.add_argument("--parents", type=Path, default=PARENTS_PATH)
    evaluation.add_argument("--children", type=Path, default=CHILDREN_PATH)
    evaluation.add_argument("--chunk-summary", type=Path, default=CHUNK_SUMMARY_PATH)
    evaluation.add_argument("--mode", action="append", choices=["keyword", "vector", "hybrid", "weighted"], dest="modes")
    evaluation.add_argument("--alpha", action="append", type=float, dest="alphas")
    evaluation.add_argument("--top-k", type=int, default=10)
    evaluation.add_argument("--candidate-k", type=int, default=50)
    evaluation.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    evaluation.add_argument("--output", type=Path, default=RAG_DIR / "reports" / "retrieval_evaluation.json")
    evaluation.add_argument("--confirm-external-upload", action="store_true")

    answer = subparsers.add_parser("answer")
    answer.add_argument("query")
    answer.add_argument("--mode", choices=["keyword", "vector", "hybrid", "weighted"], default="hybrid")
    answer.add_argument("--top-k", type=int, default=5)
    answer.add_argument("--candidate-k", type=int, default=50)
    answer.add_argument("--alpha", type=float, default=0.5)
    answer.add_argument("--issuer")
    answer.add_argument("--card-name")
    answer.add_argument("--embedding-model", default=EMBEDDING_MODEL)
    answer.add_argument("--model", default=GENERATION_MODEL)
    answer.add_argument("--reasoning", default="medium")
    answer.add_argument("--parents", type=Path, default=PARENTS_PATH)
    answer.add_argument("--children", type=Path, default=CHILDREN_PATH)
    answer.add_argument("--chunk-summary", type=Path, default=CHUNK_SUMMARY_PATH)
    answer.add_argument(
        "--confirm-external-upload",
        action="store_true",
        help="Upload the question and retrieved parent text to the OpenAI Responses API.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "build-index":
        validation = validate_chunk_corpus(
            args.parents,
            args.children,
            args.allow_partial,
            args.chunk_summary,
        )
        build_metadata = validation["index_metadata"]
        result = {key: value for key, value in validation.items() if key != "index_metadata"}
        index = HybridIndex(args.index)
        try:
            result.update(index.rebuild(args.parents, args.children, build_metadata))
            result.update(index.require_build_metadata(build_metadata))
            if args.embed:
                if not args.confirm_external_upload:
                    raise SystemExit("--embed requires --confirm-external-upload")
                result.update(index.embed_missing(OpenAIClient(), args.embedding_model, args.embedding_batch_size))
            print(json.dumps(result, ensure_ascii=False))
        finally:
            index.close()
        return

    if args.command == "evaluate":
        validation = validate_chunk_corpus(
            args.parents,
            args.children,
            False,
            args.chunk_summary,
        )
        queries = read_jsonl(args.queries)
        query_set_sha256 = validate_query_corpus(queries)
        modes = args.modes or ["keyword", "vector", "hybrid", "weighted"]
        alphas = args.alphas or [0.2, 0.5, 0.8]
        index = HybridIndex(args.index)
        try:
            metadata_status = index.require_build_metadata(validation["index_metadata"])
            if any(mode != "keyword" for mode in modes) and not args.confirm_external_upload:
                raise SystemExit("vector evaluation requires --confirm-external-upload")
            client = OpenAIClient() if any(mode != "keyword" for mode in modes) else None
            report = evaluate(index, queries, client, modes, alphas, args.embedding_model, args.top_k, args.candidate_k)
            if report["query_set_sha256"] != query_set_sha256:
                raise ValueError("evaluation query fingerprint changed during evaluation")
            report.update(metadata_status)
            report["chunk_build_sha256"] = validation["index_metadata"]["chunk_build_sha256"]
            report["chunk_config_sha256"] = validation["index_metadata"]["chunk_config_sha256"]
            write_json(args.output, report)
            print(json.dumps(report["conditions"], ensure_ascii=False, indent=2))
            print(args.output)
        finally:
            index.close()
        return

    validation = validate_chunk_corpus(
        args.parents,
        args.children,
        False,
        args.chunk_summary,
    )
    index = HybridIndex(args.index)
    try:
        index.require_build_metadata(validation["index_metadata"])
        needs_external_upload = args.mode != "keyword" or args.command == "answer"
        if needs_external_upload and not args.confirm_external_upload:
            raise SystemExit(f"{args.command} requires --confirm-external-upload")
        if args.mode != "keyword":
            index.require_embedding_coverage(args.embedding_model)
        client = OpenAIClient() if needs_external_upload else None
        vector = query_embedding(client, args.query, args.embedding_model) if args.mode != "keyword" else None
        result = index.search(
            args.query,
            mode=args.mode,
            top_k=args.top_k,
            candidate_k=args.candidate_k,
            query_vector=vector,
            issuer=args.issuer,
            card_name=args.card_name,
            alpha=args.alpha,
        )
        if args.command == "search":
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        generated = answer_question(client, args.query, result["parents"], args.model, args.reasoning)
        generated["retrieval"] = {"mode": args.mode, "latency_ms": result["latency_ms"], "parent_count": len(result["parents"])}
        print(json.dumps(generated, ensure_ascii=False, indent=2))
    finally:
        index.close()


if __name__ == "__main__":
    main()
