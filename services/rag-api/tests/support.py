from __future__ import annotations

import gc
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import chromadb
import numpy as np
from pickcardu_rag import AnswerOutput, AtomicClaim, Recommendation

from pickcardu_rag_api.config import Settings
from pickcardu_rag_api.index import _canonical, _embedding_sha256, _sha256, _tree_hash


def build_release(runtime: Path) -> dict[str, Any]:
    release_id = "release_fixture"
    release = runtime / "index-release" / release_id
    release.mkdir(parents=True)
    records = [
        {
            "chunk_id": "chunk-cafe",
            "document_id": "issuer/card-a",
            "level": "benefit",
            "text": "카페 10% 할인",
            "metadata": {"document_id": "issuer/card-a", "level": "benefit", "issuer_name": "Issuer", "card_name": "Card A", "section": "카페", "parent_id": None, "child_ids": [], "source_pages": [2], "retrieval_text": "Issuer | Card A | 카페\n카페 10% 할인", "reranker_text": "Issuer | Card A | 카페\n카페 10% 할인", "evidence_refs": {"luna": {"provider": "luna", "page": 2, "quote": "카페 10% 할인"}, "upstage": {"provider": "upstage", "page": 2, "quote": "카페 10% 할인"}}},
        },
        {
            "chunk_id": "chunk-fuel",
            "document_id": "issuer/card-b",
            "level": "benefit",
            "text": "주유 리터당 100원 할인",
            "metadata": {"document_id": "issuer/card-b", "level": "benefit", "issuer_name": "Issuer", "card_name": "Card B", "section": "주유", "parent_id": None, "child_ids": [], "source_pages": [3], "retrieval_text": "Issuer | Card B | 주유\n주유 리터당 100원 할인", "reranker_text": "Issuer | Card B | 주유\n주유 리터당 100원 할인", "evidence_refs": {"luna": {"provider": "luna", "page": 3, "quote": "주유 리터당 100원 할인"}, "upstage": {"provider": "upstage", "page": 3, "quote": "주유 리터당 100원 할인"}}},
        },
    ]
    import sqlite3

    connection = sqlite3.connect(release / "corpus.sqlite")
    connection.executescript("CREATE TABLE chunks(chunk_id TEXT PRIMARY KEY,document_id TEXT,level TEXT,text TEXT,metadata_json TEXT); CREATE VIRTUAL TABLE chunks_fts USING fts5(chunk_id UNINDEXED,text,tokenize='unicode61');")
    for record in records:
        connection.execute("INSERT INTO chunks VALUES(?,?,?,?,?)", (record["chunk_id"], record["document_id"], record["level"], record["text"], _canonical(record["metadata"])))
        connection.execute("INSERT INTO chunks_fts VALUES(?,?)", (record["chunk_id"], record["metadata"]["retrieval_text"]))
    connection.commit()
    connection.close()
    corpus_hash = hashlib.sha256(_canonical(records).encode()).hexdigest()
    source = release / "chroma"
    client = chromadb.PersistentClient(path=str(source))
    collection = client.get_or_create_collection("card_page_section_benefit", metadata={"hnsw:space": "l2", "corpus_hash": corpus_hash})
    fixture_embeddings = np.asarray([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)
    collection.add(ids=[row["chunk_id"] for row in records], documents=[row["metadata"]["retrieval_text"] for row in records], embeddings=fixture_embeddings.tolist(), metadatas=[{"document_id": row["document_id"], "level": row["level"]} for row in records])
    del collection, client
    gc.collect()
    tree_hash = _tree_hash(source)
    manifest = {
        "schema_version": "rag_index_release_v1",
        "release_id": release_id,
        "strategy": "card_page_section_benefit",
        "release_status": "production",
        "distance_contract": "squared_l2",
        "embedding_model": "text-embedding-3-small",
        "embedding_dimension": 2,
        "embedding_sha256": _embedding_sha256(
            [row["chunk_id"] for row in records], fixture_embeddings
        ),
        "corpus_sqlite_sha256": _sha256(release / "corpus.sqlite"),
        "corpus_hash": corpus_hash,
        "chunk_ids": [row["chunk_id"] for row in records],
        "document_ids": ["issuer/card-a", "issuer/card-b"],
        "catalog": [{"document_id": "issuer/card-a", "issuer_name": "Issuer", "card_name": "Card A"}, {"document_id": "issuer/card-b", "issuer_name": "Issuer", "card_name": "Card B"}],
        "chroma_tree_sha256": tree_hash,
        "created_at": "2026-08-31T00:00:00Z",
    }
    (release / "manifest.json").write_text(_canonical(manifest) + "\n", encoding="utf-8")
    os.chmod(release / "corpus.sqlite", 0o444)
    serving = runtime / "serving" / release_id / tree_hash
    shutil.copytree(source, serving / "chroma")
    # Match indexer materialization: its identity verification opens the copied
    # Chroma once before computing the immutable serving hash.
    verification = chromadb.PersistentClient(path=str(serving / "chroma")).get_collection("card_page_section_benefit")
    verification.get(include=["embeddings"])
    del verification
    gc.collect()
    marker = {"release_id": release_id, "chroma_tree_sha256": tree_hash, "corpus_hash": corpus_hash, "chunk_ids": manifest["chunk_ids"], "embedding_dimension": 2, "embedding_sha256": manifest["embedding_sha256"]}
    (serving / "version.json").write_text(_canonical(marker) + "\n", encoding="utf-8")
    lock_root = runtime / "serving/.locks"
    lock_root.mkdir(parents=True)
    (lock_root / f"{release_id}.lock").touch()
    pointer = {"release_id": release_id, "manifest_sha256": _sha256(release / "manifest.json")}
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / "active-index.json").write_text(_canonical(pointer) + "\n", encoding="utf-8")
    return manifest


def settings(root: Path) -> Settings:
    bge = root / "bge"
    bge.mkdir(exist_ok=True)
    return Settings(
        "test",
        root / "runtime",
        ("http://testserver",),
        None,
        "text-embedding-3-small",
        "gpt-5.6-luna",
        bge,
    )


class FakeProvider:
    embedding_model = "text-embedding-3-small"
    llm_model = "gpt-5.6-luna"

    def __init__(self) -> None:
        self.embedding_queries: list[str] = []
        self.answer_inputs: list[tuple[str, list[dict[str, Any]]]] = []

    def embed(self, query: str):
        self.embedding_queries.append(query)
        return np.asarray([0.0, 0.0], dtype=np.float32), {"model": self.embedding_model, "usage": {"total_tokens": 1}}

    def rewrite(self, context: list[dict[str, str]]):
        return context[-1]["content"], {"model": self.llm_model}

    def answer(self, query: str, evidence: list[dict[str, Any]]):
        self.answer_inputs.append((query, evidence))
        item = evidence[0]
        answer = AnswerOutput(answer_text="Card A를 검토하세요. 공식 상품설명서를 재확인하세요.", recommendations=[Recommendation(card_key=item["card_key"], reason="카페 혜택", citations=[item["chunk_id"]])], claims=[AtomicClaim(card_key=item["card_key"], text="카페 10% 할인", value=10, unit="%", citations=[item["chunk_id"]])])
        return answer, {"attempt_count": 1, "usage_complete": True, "usage_scope": "all_attempts"}


class FakeReranker:
    def artifact_contract(self, mode: str) -> dict[str, Any]:
        return {"artifact_fingerprint": f"{mode}-fixture"}

    def score(self, mode: str, query: str, documents: list[str]):
        return [float(len(documents) - index) for index in range(len(documents))], {"mode": mode, "fixture": True}
