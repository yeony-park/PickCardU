import json
from copy import deepcopy
import sys
from pathlib import Path

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts" / "rag_pipeline"
sys.path.insert(0, str(SCRIPT_DIR))

from chunking import CHUNK_CORPUS_FINGERPRINT_VERSION, chunk_corpus_sha256
from hybrid_index import HybridIndex, encode_vector, rrf_fuse


def write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")


def fixture_index(tmp_path):
    parents = [
        {
            "chunk_id": "p1",
            "document_id": "issuer/card-a",
            "issuer": "issuer",
            "card_name": "card-a",
            "source_path": "a.pdf",
            "page_start": 1,
            "page_end": 1,
            "section_path": ["card-a", "주유"],
            "text": "주유 리터당 70원 할인",
        },
        {
            "chunk_id": "p2",
            "document_id": "issuer/card-b",
            "issuer": "issuer",
            "card_name": "card-b",
            "source_path": "b.pdf",
            "page_start": 2,
            "page_end": 2,
            "section_path": ["card-b", "라운지"],
            "text": "공항 휴게 공간 무료 이용",
        },
    ]
    children = [
        {**parents[0], "chunk_id": "c1", "parent_id": "p1", "text": "주유 리터당 70원 할인"},
        {**parents[1], "chunk_id": "c2", "parent_id": "p2", "text": "공항 라운지 연 2회 무료"},
    ]
    parent_path = tmp_path / "parents.jsonl"
    child_path = tmp_path / "children.jsonl"
    write_jsonl(parent_path, parents)
    write_jsonl(child_path, children)
    index = HybridIndex(tmp_path / "index.sqlite3")
    index.rebuild(parent_path, child_path)
    with index.connection:
        for child_id, vector in [("c1", [1.0, 0.0]), ("c2", [0.0, 1.0])]:
            blob, norm = encode_vector(vector)
            index.connection.execute(
                "UPDATE children SET embedding=?, embedding_dim=2, embedding_norm=?, embedding_model='fake' WHERE child_id=?",
                (blob, norm, child_id),
            )
    return index


def test_keyword_and_vector_retrieval_use_child_then_expand_parent(tmp_path):
    index = fixture_index(tmp_path)
    try:
        keyword = index.search("70원 주유", mode="keyword", top_k=1, candidate_k=2)
        vector = index.search("쉬는 곳", mode="vector", top_k=1, candidate_k=2, query_vector=[0.0, 1.0])
    finally:
        index.close()

    assert keyword["parents"][0]["chunk_id"] == "p1"
    assert keyword["parents"][0]["supporting_children"] == ["c1"]
    assert vector["parents"][0]["chunk_id"] == "p2"


def test_rrf_formula_rewards_items_present_in_both_rankings():
    scores = rrf_fuse([["a", "b"], ["b", "c"]], k=60)

    assert scores["b"] == 1 / 62 + 1 / 61
    assert scores["b"] > scores["a"]
    assert scores["b"] > scores["c"]


def test_hybrid_search_is_deterministic(tmp_path):
    index = fixture_index(tmp_path)
    try:
        first = index.search("주유 라운지", mode="hybrid", top_k=2, candidate_k=2, query_vector=[0.0, 1.0])
        second = index.search("주유 라운지", mode="hybrid", top_k=2, candidate_k=2, query_vector=[0.0, 1.0])
    finally:
        index.close()

    assert [row["chunk_id"] for row in first["parents"]] == [row["chunk_id"] for row in second["parents"]]


def test_rebuild_preserves_matching_embeddings_for_resume(tmp_path):
    index = fixture_index(tmp_path)
    try:
        result = index.rebuild(tmp_path / "parents.jsonl", tmp_path / "children.jsonl")
        embedded = index.connection.execute(
            "SELECT count(*) FROM children WHERE embedding IS NOT NULL"
        ).fetchone()[0]
    finally:
        index.close()

    assert result["preserved_embeddings"] == 2
    assert embedded == 2


def test_vector_preflight_requires_full_model_coverage(tmp_path):
    index = fixture_index(tmp_path)
    try:
        assert index.require_embedding_coverage("fake")["embedded_children"] == 2
        with index.connection:
            index.connection.execute("UPDATE children SET embedding=NULL WHERE child_id='c2'")
        with pytest.raises(ValueError, match="100% fake coverage"):
            index.require_embedding_coverage("fake")
    finally:
        index.close()


def test_chunk_corpus_fingerprint_is_order_independent_and_provenance_sensitive(tmp_path):
    index = fixture_index(tmp_path)
    index.close()
    parents = [json.loads(line) for line in (tmp_path / "parents.jsonl").read_text(encoding="utf-8").splitlines()]
    children = [json.loads(line) for line in (tmp_path / "children.jsonl").read_text(encoding="utf-8").splitlines()]
    expected = chunk_corpus_sha256(parents, children)

    assert chunk_corpus_sha256(list(reversed(parents)), list(reversed(children))) == expected

    changed_child = deepcopy(children)
    changed_child[0]["layout_config_sha256"] = "changed"
    assert chunk_corpus_sha256(parents, changed_child) != expected

    changed_parent = deepcopy(parents)
    changed_parent[0]["section_path"] = ["changed"]
    assert chunk_corpus_sha256(changed_parent, children) != expected


def test_rebuild_persists_and_requires_chunk_corpus_metadata(tmp_path):
    index = fixture_index(tmp_path)
    parents = [json.loads(line) for line in (tmp_path / "parents.jsonl").read_text(encoding="utf-8").splitlines()]
    children = [json.loads(line) for line in (tmp_path / "children.jsonl").read_text(encoding="utf-8").splitlines()]
    metadata = {
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": chunk_corpus_sha256(parents, children),
        "index_corpus_sha256": index.corpus_fingerprint(),
    }
    index.rebuild(tmp_path / "parents.jsonl", tmp_path / "children.jsonl", metadata)
    index.close()

    reopened = HybridIndex(tmp_path / "index.sqlite3")
    try:
        assert reopened.build_metadata() == metadata
        assert reopened.require_build_metadata(metadata)["chunk_corpus_sha256"] == metadata["chunk_corpus_sha256"]
    finally:
        reopened.close()


def test_rebuild_rejects_changed_validated_fingerprint_without_mutating_index(tmp_path):
    index = fixture_index(tmp_path)
    parents = [json.loads(line) for line in (tmp_path / "parents.jsonl").read_text(encoding="utf-8").splitlines()]
    children = [json.loads(line) for line in (tmp_path / "children.jsonl").read_text(encoding="utf-8").splitlines()]
    metadata = {
        "chunk_corpus_fingerprint_version": CHUNK_CORPUS_FINGERPRINT_VERSION,
        "chunk_corpus_sha256": chunk_corpus_sha256(parents, children),
        "index_corpus_sha256": index.corpus_fingerprint(),
    }
    index.rebuild(tmp_path / "parents.jsonl", tmp_path / "children.jsonl", metadata)
    before = index.connection.execute("SELECT child_id, text FROM children ORDER BY child_id").fetchall()

    changed = deepcopy(children)
    changed[0]["layout_config_sha256"] = "changed-after-validation"
    write_jsonl(tmp_path / "children.jsonl", changed)
    try:
        with pytest.raises(ValueError, match="fingerprint changed before index rebuild"):
            index.rebuild(tmp_path / "parents.jsonl", tmp_path / "children.jsonl", metadata)
        after = index.connection.execute("SELECT child_id, text FROM children ORDER BY child_id").fetchall()
        assert [tuple(row) for row in after] == [tuple(row) for row in before]
        assert index.build_metadata() == metadata
    finally:
        index.close()


def test_legacy_index_gets_empty_metadata_table_without_losing_rows(tmp_path):
    index = fixture_index(tmp_path)
    with index.connection:
        index.connection.execute("DROP TABLE index_metadata")
    index.close()

    reopened = HybridIndex(tmp_path / "index.sqlite3")
    try:
        assert reopened.connection.execute("SELECT count(*) FROM children").fetchone()[0] == 2
        with pytest.raises(ValueError, match="metadata is missing; rerun build-index"):
            reopened.require_build_metadata({})
    finally:
        reopened.close()
