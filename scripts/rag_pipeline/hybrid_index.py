from __future__ import annotations

import json
import math
import sqlite3
import time
from array import array
from pathlib import Path
from typing import Any, Iterable

from chunking import CHUNK_CORPUS_FINGERPRINT_VERSION, chunk_corpus_sha256
from common import read_jsonl, value_sha256
from openai_client import OpenAIClient


EMBEDDING_MODEL = "text-embedding-3-small"


def child_index_fingerprint(children: list[dict[str, Any]]) -> str:
    try:
        rows = [
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
    except (KeyError, TypeError) as error:
        raise ValueError("children contain malformed index fields") from error
    return value_sha256(rows)


def encode_vector(values: list[float]) -> tuple[bytes, float]:
    vector = array("f", values)
    norm = math.sqrt(sum(value * value for value in vector))
    return vector.tobytes(), norm


def decode_vector(value: bytes) -> array[float]:
    vector: array[float] = array("f")
    vector.frombytes(value)
    return vector


def cosine_similarity(left: Iterable[float], right: Iterable[float], left_norm: float | None = None) -> float:
    left_values = list(left)
    right_values = list(right)
    if len(left_values) != len(right_values):
        raise ValueError("vector dimensions do not match")
    left_norm = left_norm if left_norm is not None else math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left_values, right_values)) / (left_norm * right_norm)


def minmax(scores: dict[str, float]) -> dict[str, float]:
    if not scores:
        return {}
    minimum = min(scores.values())
    maximum = max(scores.values())
    if maximum == minimum:
        return {key: 1.0 for key in scores}
    return {key: (value - minimum) / (maximum - minimum) for key, value in scores.items()}


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridIndex:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.create_schema()

    def close(self) -> None:
        self.connection.close()

    def create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS parents (
                parent_id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                issuer TEXT NOT NULL,
                card_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                section_path TEXT NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS children (
                child_id TEXT PRIMARY KEY,
                parent_id TEXT NOT NULL REFERENCES parents(parent_id),
                document_id TEXT NOT NULL,
                issuer TEXT NOT NULL,
                card_name TEXT NOT NULL,
                source_path TEXT NOT NULL,
                page_start INTEGER NOT NULL,
                page_end INTEGER NOT NULL,
                text TEXT NOT NULL,
                metadata TEXT NOT NULL,
                embedding BLOB,
                embedding_dim INTEGER,
                embedding_norm REAL,
                embedding_model TEXT
            );
            CREATE INDEX IF NOT EXISTS children_parent_idx ON children(parent_id);
            CREATE INDEX IF NOT EXISTS children_document_idx ON children(document_id, page_start);
            CREATE TABLE IF NOT EXISTS index_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS children_fts USING fts5(
                child_id UNINDEXED,
                text,
                document_id UNINDEXED,
                issuer UNINDEXED,
                card_name UNINDEXED,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        self.connection.commit()

    def rebuild(
        self,
        parents_path: Path,
        children_path: Path,
        build_metadata: dict[str, Any] | None = None,
    ) -> dict[str, int]:
        parents = read_jsonl(parents_path)
        children = read_jsonl(children_path)
        if build_metadata is not None:
            actual_chunk_corpus_sha256 = chunk_corpus_sha256(parents, children)
            if (
                build_metadata.get("chunk_corpus_fingerprint_version") != CHUNK_CORPUS_FINGERPRINT_VERSION
                or build_metadata.get("chunk_corpus_sha256") != actual_chunk_corpus_sha256
                or build_metadata.get("index_corpus_sha256") != child_index_fingerprint(children)
            ):
                raise ValueError("validated chunk fingerprint changed before index rebuild")
        parent_ids = {parent["chunk_id"] for parent in parents}
        missing = sorted({child["parent_id"] for child in children} - parent_ids)
        if missing:
            raise ValueError(f"children reference missing parents: {missing[:3]}")
        existing_embeddings = {
            row["child_id"]: row
            for row in self.connection.execute(
                """
                SELECT child_id, text, embedding, embedding_dim, embedding_norm, embedding_model
                FROM children
                WHERE embedding IS NOT NULL
                """
            ).fetchall()
        }
        preserved_embeddings = 0
        child_rows = []
        for child in children:
            existing = existing_embeddings.get(child["chunk_id"])
            preserve = existing is not None and existing["text"] == child["text"]
            preserved_embeddings += int(preserve)
            child_rows.append(
                (
                    child["chunk_id"],
                    child["parent_id"],
                    child["document_id"],
                    child["issuer"],
                    child["card_name"],
                    child["source_path"],
                    child["page_start"],
                    child["page_end"],
                    child["text"],
                    json.dumps(child, ensure_ascii=False),
                    existing["embedding"] if preserve else None,
                    existing["embedding_dim"] if preserve else None,
                    existing["embedding_norm"] if preserve else None,
                    existing["embedding_model"] if preserve else None,
                )
            )

        with self.connection:
            self.connection.execute("DELETE FROM children_fts")
            self.connection.execute("DELETE FROM children")
            self.connection.execute("DELETE FROM parents")
            self.connection.execute("DELETE FROM index_metadata")
            self.connection.executemany(
                """
                INSERT INTO parents(parent_id, document_id, issuer, card_name, source_path, page_start, page_end, section_path, text, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        parent["chunk_id"],
                        parent["document_id"],
                        parent["issuer"],
                        parent["card_name"],
                        parent["source_path"],
                        parent["page_start"],
                        parent["page_end"],
                        json.dumps(parent["section_path"], ensure_ascii=False),
                        parent["text"],
                        json.dumps(parent, ensure_ascii=False),
                    )
                    for parent in parents
                ],
            )
            self.connection.executemany(
                """
                INSERT INTO children(
                    child_id, parent_id, document_id, issuer, card_name, source_path,
                    page_start, page_end, text, metadata,
                    embedding, embedding_dim, embedding_norm, embedding_model
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                child_rows,
            )
            self.connection.executemany(
                "INSERT INTO children_fts(child_id, text, document_id, issuer, card_name) VALUES (?, ?, ?, ?, ?)",
                [(child["chunk_id"], child["text"], child["document_id"], child["issuer"], child["card_name"]) for child in children],
            )
            if build_metadata is not None:
                self.connection.execute(
                    "INSERT INTO index_metadata(key, value) VALUES ('build', ?)",
                    (json.dumps(build_metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
                )
        return {"parents": len(parents), "children": len(children), "preserved_embeddings": preserved_embeddings}

    def build_metadata(self) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT value FROM index_metadata WHERE key = 'build'").fetchone()
        if row is None:
            return None
        try:
            value = json.loads(row["value"])
        except (TypeError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def chunk_corpus_fingerprint(self) -> str:
        parents = [
            json.loads(row["metadata"])
            for row in self.connection.execute("SELECT metadata FROM parents ORDER BY parent_id").fetchall()
        ]
        children = [
            json.loads(row["metadata"])
            for row in self.connection.execute("SELECT metadata FROM children ORDER BY child_id").fetchall()
        ]
        return chunk_corpus_sha256(parents, children)

    def require_build_metadata(self, expected: dict[str, Any]) -> dict[str, Any]:
        stored = self.build_metadata()
        if stored is None:
            raise ValueError("index build metadata is missing; rerun build-index")
        if stored != expected:
            raise ValueError("index build metadata does not match current chunks; rerun build-index")
        if self.chunk_corpus_fingerprint() != expected.get("chunk_corpus_sha256"):
            raise ValueError("indexed chunk corpus does not match index metadata; rerun build-index")
        if self.corpus_fingerprint() != expected.get("index_corpus_sha256"):
            raise ValueError("indexed child corpus does not match index metadata; rerun build-index")
        return {
            "index_metadata_sha256": value_sha256(stored),
            "chunk_corpus_sha256": stored["chunk_corpus_sha256"],
        }

    def embed_missing(
        self,
        client: OpenAIClient,
        model: str = EMBEDDING_MODEL,
        batch_size: int = 64,
    ) -> dict[str, int]:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        rows = self.connection.execute(
            "SELECT child_id, text FROM children WHERE embedding IS NULL OR embedding_model != ? ORDER BY child_id",
            (model,),
        ).fetchall()
        embedded = 0
        input_tokens = 0
        for offset in range(0, len(rows), batch_size):
            batch = rows[offset : offset + batch_size]
            vectors, usage = client.embeddings([row["text"] for row in batch], model=model)
            values = []
            for row, vector in zip(batch, vectors):
                blob, norm = encode_vector(vector)
                values.append((blob, len(vector), norm, model, row["child_id"]))
            with self.connection:
                self.connection.executemany(
                    "UPDATE children SET embedding = ?, embedding_dim = ?, embedding_norm = ?, embedding_model = ? WHERE child_id = ?",
                    values,
                )
            embedded += len(batch)
            input_tokens += int(usage.get("prompt_tokens") or usage.get("total_tokens") or 0)
            print(json.dumps({"embedded": embedded, "total": len(rows)}, ensure_ascii=False), flush=True)
        return {"embedded": embedded, "input_tokens": input_tokens}

    def embedding_status(self, model: str) -> dict[str, Any]:
        total = int(self.connection.execute("SELECT count(*) FROM children").fetchone()[0])
        matching = int(
            self.connection.execute(
                "SELECT count(*) FROM children WHERE embedding IS NOT NULL AND embedding_model = ?",
                (model,),
            ).fetchone()[0]
        )
        dimensions = [
            int(row[0])
            for row in self.connection.execute(
                """
                SELECT DISTINCT embedding_dim
                FROM children
                WHERE embedding IS NOT NULL AND embedding_model = ?
                ORDER BY embedding_dim
                """,
                (model,),
            ).fetchall()
            if row[0] is not None
        ]
        return {
            "total_children": total,
            "embedded_children": matching,
            "embedding_model": model,
            "embedding_dimensions": dimensions,
        }

    def require_embedding_coverage(self, model: str) -> dict[str, Any]:
        status = self.embedding_status(model)
        if status["total_children"] == 0:
            raise ValueError("vector retrieval requires a non-empty child index")
        if status["embedded_children"] != status["total_children"]:
            raise ValueError(
                f"vector retrieval requires 100% {model} coverage: "
                f"{status['embedded_children']}/{status['total_children']} children embedded"
            )
        if len(status["embedding_dimensions"]) != 1:
            raise ValueError(f"vector retrieval requires one embedding dimension: {status['embedding_dimensions']}")
        return status

    def corpus_fingerprint(self) -> str:
        rows = self.connection.execute(
            "SELECT child_id, parent_id, document_id, page_start, page_end, text FROM children ORDER BY child_id"
        ).fetchall()
        return value_sha256([tuple(row) for row in rows])

    @staticmethod
    def fts_query(query: str) -> str:
        tokens = [token for token in __import__("re").findall(r"[0-9A-Za-z가-힣]+", query.casefold()) if token]
        return " OR ".join(f'"{token}"' for token in dict.fromkeys(tokens))

    def keyword_candidates(
        self,
        query: str,
        limit: int,
        issuer: str | None = None,
        card_name: str | None = None,
    ) -> list[tuple[str, float]]:
        match = self.fts_query(query)
        if not match:
            return []
        clauses = ["children_fts MATCH ?"]
        parameters: list[Any] = [match]
        if issuer:
            clauses.append("issuer = ?")
            parameters.append(issuer)
        if card_name:
            clauses.append("card_name = ?")
            parameters.append(card_name)
        parameters.append(limit)
        rows = self.connection.execute(
            f"SELECT child_id, bm25(children_fts) AS distance FROM children_fts WHERE {' AND '.join(clauses)} ORDER BY distance, child_id LIMIT ?",
            parameters,
        ).fetchall()
        return [(row["child_id"], -float(row["distance"])) for row in rows]

    def vector_candidates(
        self,
        query_vector: list[float],
        limit: int,
        issuer: str | None = None,
        card_name: str | None = None,
    ) -> list[tuple[str, float]]:
        clauses = ["embedding IS NOT NULL"]
        parameters: list[Any] = []
        if issuer:
            clauses.append("issuer = ?")
            parameters.append(issuer)
        if card_name:
            clauses.append("card_name = ?")
            parameters.append(card_name)
        rows = self.connection.execute(
            f"SELECT child_id, embedding, embedding_norm FROM children WHERE {' AND '.join(clauses)}",
            parameters,
        ).fetchall()
        scores = [
            (row["child_id"], cosine_similarity(decode_vector(row["embedding"]), query_vector, row["embedding_norm"]))
            for row in rows
        ]
        return sorted(scores, key=lambda item: (-item[1], item[0]))[:limit]

    def search_children(
        self,
        query: str,
        mode: str,
        top_k: int,
        candidate_k: int,
        query_vector: list[float] | None = None,
        issuer: str | None = None,
        card_name: str | None = None,
        alpha: float = 0.5,
    ) -> list[dict[str, Any]]:
        if mode not in {"keyword", "vector", "hybrid", "weighted"}:
            raise ValueError(f"unknown retrieval mode: {mode}")
        if mode in {"vector", "hybrid", "weighted"} and query_vector is None:
            raise ValueError(f"{mode} retrieval requires a query vector")
        keyword = self.keyword_candidates(query, candidate_k, issuer, card_name) if mode != "vector" else []
        vector = self.vector_candidates(query_vector or [], candidate_k, issuer, card_name) if mode != "keyword" else []
        keyword_scores = dict(keyword)
        vector_scores = dict(vector)
        if mode == "keyword":
            fused = keyword_scores
        elif mode == "vector":
            fused = vector_scores
        elif mode == "hybrid":
            fused = rrf_fuse([[item[0] for item in vector], [item[0] for item in keyword]])
        else:
            normalized_vector = minmax(vector_scores)
            normalized_keyword = minmax(keyword_scores)
            fused = {
                chunk_id: alpha * normalized_vector.get(chunk_id, 0.0) + (1 - alpha) * normalized_keyword.get(chunk_id, 0.0)
                for chunk_id in set(normalized_vector) | set(normalized_keyword)
            }
        ranked = sorted(fused.items(), key=lambda item: (-item[1], item[0]))[:top_k]
        if not ranked:
            return []
        placeholders = ",".join("?" for _ in ranked)
        rows = self.connection.execute(
            f"SELECT * FROM children WHERE child_id IN ({placeholders})",
            [chunk_id for chunk_id, _ in ranked],
        ).fetchall()
        by_id = {row["child_id"]: row for row in rows}
        return [
            {
                "rank": rank,
                "score": score,
                "keyword_score": keyword_scores.get(chunk_id),
                "vector_score": vector_scores.get(chunk_id),
                **json.loads(by_id[chunk_id]["metadata"]),
            }
            for rank, (chunk_id, score) in enumerate(ranked, start=1)
            if chunk_id in by_id
        ]

    def expand_parents(self, child_hits: list[dict[str, Any]], top_k: int) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for child in child_hits:
            group = grouped.setdefault(
                child["parent_id"],
                {"score": child["score"], "best_child_rank": child["rank"], "supporting_children": []},
            )
            group["score"] = max(group["score"], child["score"])
            group["best_child_rank"] = min(group["best_child_rank"], child["rank"])
            if len(group["supporting_children"]) < 2:
                group["supporting_children"].append(child["chunk_id"])
        ranked = sorted(grouped.items(), key=lambda item: (item[1]["best_child_rank"], -item[1]["score"], item[0]))[:top_k]
        if not ranked:
            return []
        placeholders = ",".join("?" for _ in ranked)
        rows = self.connection.execute(
            f"SELECT * FROM parents WHERE parent_id IN ({placeholders})",
            [parent_id for parent_id, _ in ranked],
        ).fetchall()
        by_id = {row["parent_id"]: row for row in rows}
        return [
            {
                "rank": rank,
                **group,
                **json.loads(by_id[parent_id]["metadata"]),
            }
            for rank, (parent_id, group) in enumerate(ranked, start=1)
            if parent_id in by_id
        ]

    def search(
        self,
        query: str,
        mode: str = "hybrid",
        top_k: int = 5,
        candidate_k: int = 50,
        query_vector: list[float] | None = None,
        issuer: str | None = None,
        card_name: str | None = None,
        alpha: float = 0.5,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        children = self.search_children(query, mode, candidate_k, candidate_k, query_vector, issuer, card_name, alpha)
        parents = self.expand_parents(children, top_k)
        return {
            "query": query,
            "mode": mode,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "parents": parents,
            "child_hits": children[:top_k],
        }
