import json
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from build_eval_queries import scalar_terms
from chunking import (
    CHUNK_CORPUS_FINGERPRINT_VERSION,
    CHUNKER_VERSION,
    CHUNK_SUMMARY_SCHEMA_VERSION,
    chunk_build_sha256,
    chunk_config_sha256,
    chunk_corpus_sha256,
    chunk_rows_sha256,
)
from common import SourceDocument, value_sha256
from hybrid_index import HybridIndex
import hybrid_rag
from hybrid_rag import relevant_rank


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def current_chunk_corpus(tmp_path, monkeypatch):
    source = SourceDocument(
        document_id="issuer/card",
        issuer="issuer",
        card_name="card",
        path=tmp_path / "card.pdf",
        relative_path="data/raw/issuer/card.pdf",
        sha256="source-sha",
        page_count=1,
    )
    monkeypatch.setattr(hybrid_rag, "discover_documents", lambda: [source])
    canonical_dir = tmp_path / "canonical"
    monkeypatch.setattr(hybrid_rag, "CANONICAL_DIR", canonical_dir)
    primary_config_sha256 = value_sha256(hybrid_rag.luna_config(6))
    layout_config_sha256 = value_sha256(hybrid_rag.upstage_config())
    write_json(
        canonical_dir / "issuer" / "card.json",
        {
            "document_id": source.document_id,
            "source": {"sha256": source.sha256},
            "primary_parser": {"batch_pages": 6, "config_sha256": primary_config_sha256},
            "layout_parser": {"config_sha256": layout_config_sha256},
        },
    )
    shared = {
        "schema_version": "1.0",
        "document_id": source.document_id,
        "issuer": source.issuer,
        "card_name": source.card_name,
        "source_path": source.relative_path,
        "source_sha256": source.sha256,
        "page_start": 1,
        "page_end": 1,
        "section_path": ["card", "주유"],
        "chunker_version": CHUNKER_VERSION,
        "child_max_chars": 1600,
        "child_overlap_chars": 160,
        "primary_config_sha256": primary_config_sha256,
        "layout_config_sha256": layout_config_sha256,
    }
    parents = [
        {
            **shared,
            "chunk_id": "p1",
            "kind": "parent",
            "parent_id": None,
            "text": "주유 리터당 70원 할인",
        }
    ]
    children = [
        {
            **shared,
            "chunk_id": "c1",
            "kind": "child",
            "parent_id": "p1",
            "text": "주유 리터당 70원 할인",
            "table_atomic": False,
        }
    ]
    parents_path = tmp_path / "chunks" / "parents.jsonl"
    children_path = tmp_path / "chunks" / "children.jsonl"
    summary_path = tmp_path / "chunk_summary.json"
    write_jsonl(parents_path, parents)
    write_jsonl(children_path, children)
    summary = {
        "schema_version": CHUNK_SUMMARY_SCHEMA_VERSION,
        "generated_at": "2026-08-19T00:00:00+09:00",
        "chunker_version": CHUNKER_VERSION,
        "document_ids": [source.document_id],
        "document_count": 1,
        "expected_document_count": 1,
        "missing_documents": [],
        "invalid_documents": [],
        "parent_count": 1,
        "child_count": 1,
        "child_max_chars": 1600,
        "child_overlap_chars": 160,
        "upstream_primary_config_sha256": [primary_config_sha256],
        "upstream_layout_config_sha256": [layout_config_sha256],
        "source_corpus_sha256": value_sha256([source.as_dict()]),
        "parent_corpus_sha256": chunk_rows_sha256(parents),
        "child_corpus_sha256": chunk_rows_sha256(children),
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": chunk_corpus_sha256(parents, children),
    }
    summary["chunk_config_sha256"] = chunk_config_sha256(summary)
    summary["chunk_build_sha256"] = chunk_build_sha256(summary)
    write_json(summary_path, summary)
    return {
        "source": source,
        "parents": parents,
        "children": children,
        "parents_path": parents_path,
        "children_path": children_path,
        "summary_path": summary_path,
    }


def query_for(source):
    return {
        "query_id": "q1",
        "question": "주유 할인은 얼마인가요?",
        "expected_document_id": source.document_id,
        "expected_page": 1,
        "expected_answer": "70원",
        "expected_terms": ["주유", "70원"],
    }


def build_current_index(corpus, index_path):
    validation = hybrid_rag.validate_chunk_corpus(
        corpus["parents_path"],
        corpus["children_path"],
        False,
        corpus["summary_path"],
    )
    index = HybridIndex(index_path)
    try:
        index.rebuild(corpus["parents_path"], corpus["children_path"], validation["index_metadata"])
    finally:
        index.close()
    return validation


def test_nested_expected_answer_produces_searchable_scalar_terms():
    assert scalar_terms({"rate": 0.6, "target": "음식점", "types": ["결제일 할인"]}) == [
        "0.6",
        "음식점",
        "결제일 할인",
    ]


def test_relevance_requires_expected_evidence_within_the_expected_page():
    result = {
        "parents": [
            {"rank": 1, "document_id": "issuer/card", "page_start": 2, "page_end": 2, "text": "연회비 안내"},
            {"rank": 2, "document_id": "issuer/card", "page_start": 2, "page_end": 2, "text": "음식점 60% 결제일 할인"},
        ]
    }
    query = {
        "expected_document_id": "issuer/card",
        "expected_page": 2,
        "expected_terms": ["음식점", "결제일 할인"],
    }

    assert relevant_rank(result, query) == 2


def test_build_index_validates_summary_before_opening_database(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    summary = json.loads(corpus["summary_path"].read_text(encoding="utf-8"))
    summary["child_count"] = 2
    write_json(corpus["summary_path"], summary)

    class UnexpectedIndex:
        def __init__(self, _path):
            raise AssertionError("index must not open before chunk validation")

    monkeypatch.setattr(hybrid_rag, "HybridIndex", UnexpectedIndex)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(tmp_path / "index.sqlite3"),
            "build-index",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
        ],
    )

    with pytest.raises(ValueError, match="chunk summary does not match"):
        hybrid_rag.main()


def test_build_index_persists_validated_metadata_without_external_client(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"

    def unexpected_client():
        raise AssertionError("keyword-only index build must not create an external client")

    monkeypatch.setattr(hybrid_rag, "OpenAIClient", unexpected_client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "build-index",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
        ],
    )

    hybrid_rag.main()

    index = HybridIndex(index_path)
    try:
        metadata = index.build_metadata()
        assert metadata["chunk_corpus_sha256"] == chunk_corpus_sha256(corpus["parents"], corpus["children"])
        assert index.require_build_metadata(metadata)["index_metadata_sha256"] == value_sha256(metadata)
        assert index.embedding_status(hybrid_rag.EMBEDDING_MODEL)["embedded_children"] == 0
    finally:
        index.close()


def test_keyword_evaluate_rejects_changed_chunks_before_report_write(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"
    build_current_index(corpus, index_path)
    changed = [dict(corpus["children"][0], layout_config_sha256="changed")]
    write_jsonl(corpus["children_path"], changed)
    queries_path = tmp_path / "queries.jsonl"
    write_jsonl(queries_path, [query_for(corpus["source"])])
    output_path = tmp_path / "evaluation.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "evaluate",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
            "--queries",
            str(queries_path),
            "--mode",
            "keyword",
            "--output",
            str(output_path),
        ],
    )

    with pytest.raises(ValueError, match="stale source/config metadata"):
        hybrid_rag.main()
    assert not output_path.exists()


def test_keyword_evaluate_uses_fingerprints_without_embeddings(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"
    validation = build_current_index(corpus, index_path)
    queries = [query_for(corpus["source"])]
    queries_path = tmp_path / "queries.jsonl"
    write_jsonl(queries_path, queries)
    output_path = tmp_path / "evaluation.json"

    def unexpected_client():
        raise AssertionError("keyword-only evaluation must not create an external client")

    monkeypatch.setattr(hybrid_rag, "OpenAIClient", unexpected_client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "evaluate",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
            "--queries",
            str(queries_path),
            "--mode",
            "keyword",
            "--output",
            str(output_path),
        ],
    )

    hybrid_rag.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["embedding_status"] is None
    assert report["query_embedding_tokens"] == 0
    assert report["query_set_sha256"] == value_sha256(queries)
    assert report["chunk_corpus_sha256"] == validation["index_metadata"]["chunk_corpus_sha256"]
    assert report["chunk_build_sha256"] == validation["index_metadata"]["chunk_build_sha256"]
    assert report["chunk_config_sha256"] == validation["index_metadata"]["chunk_config_sha256"]
    assert "keyword" in report["conditions"]


def test_vector_evaluate_checks_model_coverage_before_embedding_call(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"
    build_current_index(corpus, index_path)
    queries_path = tmp_path / "queries.jsonl"
    write_jsonl(queries_path, [query_for(corpus["source"])])
    embedding_called = False

    class FakeClient:
        def embeddings(self, _texts, model):
            nonlocal embedding_called
            embedding_called = True
            raise AssertionError(model)

    monkeypatch.setattr(hybrid_rag, "OpenAIClient", FakeClient)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "evaluate",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
            "--queries",
            str(queries_path),
            "--mode",
            "vector",
            "--confirm-external-upload",
        ],
    )

    with pytest.raises(ValueError, match="100% text-embedding-3-small coverage"):
        hybrid_rag.main()
    assert embedding_called is False


def test_keyword_search_rejects_changed_chunks_before_opening_index(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"
    build_current_index(corpus, index_path)
    changed = [dict(corpus["children"][0], layout_config_sha256="changed")]
    write_jsonl(corpus["children_path"], changed)

    class UnexpectedIndex:
        def __init__(self, _path):
            raise AssertionError("index must not open before current chunk validation")

    def unexpected_client():
        raise AssertionError("keyword search must not create an external client")

    monkeypatch.setattr(hybrid_rag, "HybridIndex", UnexpectedIndex)
    monkeypatch.setattr(hybrid_rag, "OpenAIClient", unexpected_client)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "search",
            "주유 할인",
            "--mode",
            "keyword",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
        ],
    )

    with pytest.raises(ValueError, match="stale source/config metadata"):
        hybrid_rag.main()


def test_answer_rejects_stale_index_metadata_before_generation_client(tmp_path, monkeypatch):
    corpus = current_chunk_corpus(tmp_path, monkeypatch)
    index_path = tmp_path / "index.sqlite3"
    build_current_index(corpus, index_path)
    index = HybridIndex(index_path)
    try:
        with index.connection:
            index.connection.execute(
                "UPDATE index_metadata SET value = ? WHERE key = 'build'",
                ('{"stale":true}',),
            )
    finally:
        index.close()

    def unexpected_client():
        raise AssertionError("generation client must not be created for a stale index")

    def unexpected_generation(*_args, **_kwargs):
        raise AssertionError("generation must not run for a stale index")

    monkeypatch.setattr(hybrid_rag, "OpenAIClient", unexpected_client)
    monkeypatch.setattr(hybrid_rag, "answer_question", unexpected_generation)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "hybrid_rag.py",
            "--index",
            str(index_path),
            "answer",
            "주유 할인은 얼마인가요?",
            "--mode",
            "keyword",
            "--confirm-external-upload",
            "--parents",
            str(corpus["parents_path"]),
            "--children",
            str(corpus["children_path"]),
            "--chunk-summary",
            str(corpus["summary_path"]),
        ],
    )

    with pytest.raises(ValueError, match="metadata does not match current chunks"):
        hybrid_rag.main()
