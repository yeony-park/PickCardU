from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import numpy as np
from pickcardu_rag import CHUNKING_PROFILES, Candidate, Chunk, RagPipeline, SearchConfig, normalized_tokens


CHUNKING_CONTRACTS = {
    "card_page_section_benefit": "card_page_section_benefit_v1",
    "parent_child_bundle": "structural_heading_parent_child_v1",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("expected a regular file")
    with os.fdopen(descriptor, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _embedding_sha256(chunk_ids: list[str], embeddings: np.ndarray) -> str:
    array = np.asarray(embeddings, dtype=np.float32)
    if array.ndim != 2 or array.shape[0] != len(chunk_ids) or not np.isfinite(array).all():
        raise RuntimeError("embedding identity shape or finiteness mismatch")
    digest = hashlib.sha256(_canonical(chunk_ids).encode())
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def _read_regular_bytes(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise RuntimeError("expected a regular file")
    with os.fdopen(descriptor, "rb") as stream:
        return stream.read()


def _tree_entries(root: Path) -> list[Path]:
    paths = sorted(root.rglob("*"))
    if root.is_symlink() or not root.is_dir() or any(path.is_symlink() or (not path.is_file() and not path.is_dir()) for path in paths):
        raise RuntimeError("tree contains a symlink or non-regular entry")
    return paths


def _tree_hash(root: Path) -> str:
    paths = _tree_entries(root)
    rows = [{"path": path.relative_to(root).as_posix(), "sha256": _sha256(path)} for path in paths if path.is_file()]
    return hashlib.sha256(_canonical(rows).encode()).hexdigest()


@contextmanager
def _serving_release_lock(runtime_root: Path, release_id: str) -> Iterator[None]:
    import fcntl

    lock_path = runtime_root / "serving" / ".locks" / f"{release_id}.lock"
    if lock_path.is_symlink() or not lock_path.is_file():
        raise RuntimeError("serving release lock is unavailable")
    with lock_path.open("rb") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class SQLiteFTSSearcher:
    def __init__(self, path: Path) -> None:
        self.path = path

    def search(self, query: str, *, limit: int) -> list[Candidate]:
        tokens = normalized_tokens(query)
        if not tokens:
            return []
        expression = " OR ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)
        uri = f"file:{quote(str(self.path))}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        try:
            rows = connection.execute(
                "SELECT chunk_id,bm25(chunks_fts) score FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score,chunk_id LIMIT ?",
                (expression, limit),
            ).fetchall()
        finally:
            connection.close()
        return [Candidate(chunk_id=row[0], score=float(row[1]), rank=rank) for rank, row in enumerate(rows, 1)]


class ChromaVectorSearcher:
    def __init__(self, collection: Any, embedding_model: str) -> None:
        self.collection = collection
        self.embedding_model = embedding_model

    def search(self, query_embedding: np.ndarray, *, limit: int) -> list[Candidate]:
        output = self.collection.query(query_embeddings=[np.asarray(query_embedding, dtype=np.float32).tolist()], n_results=limit, include=["distances"])
        ids, distances = output["ids"][0], output["distances"][0]
        if len(ids) != len(distances) or not np.isfinite(np.asarray(distances, dtype=np.float64)).all():
            raise RuntimeError("Chroma result count or finiteness mismatch")
        return [Candidate(chunk_id=chunk_id, score=float(distance), rank=rank) for rank, (chunk_id, distance) in enumerate(zip(ids, distances, strict=True), 1)]


@dataclass(frozen=True)
class ReleaseHandle:
    release_id: str
    manifest_hash: str
    manifest: dict[str, Any]
    chunks: tuple[Chunk, ...]
    catalog: tuple[dict[str, str], ...]
    pipeline: RagPipeline

    def search(self, query: str, embedding: np.ndarray | None, config: SearchConfig) -> dict[str, Any]:
        vector = None if embedding is None else np.asarray(embedding, dtype=np.float32)
        if vector is not None and vector.shape != (self.manifest["embedding_dimension"],):
            raise ValueError("query embedding dimension mismatch")
        return self.pipeline.search(query, vector, config)


class ActiveIndexLoader:
    """Loads and validates one immutable release handle per request."""

    def __init__(
        self,
        runtime_root: Path,
        *,
        reranker: Any = None,
        allowed_release_statuses: frozenset[str] = frozenset({"production"}),
    ) -> None:
        if not allowed_release_statuses or not allowed_release_statuses <= {"production", "test_only"}:
            raise ValueError("allowed release statuses are invalid")
        self.runtime_root = runtime_root
        self.reranker = reranker
        self.allowed_release_statuses = allowed_release_statuses
        self._cache_lock = threading.Lock()
        self._cached_pointer_bytes: bytes | None = None
        self._cached_handle: ReleaseHandle | None = None

    def load(self) -> ReleaseHandle:
        pointer_path = self.runtime_root / "active-index.json"
        if not pointer_path.is_file() or pointer_path.is_symlink():
            raise RuntimeError("active index pointer is unavailable")
        pointer_bytes = _read_regular_bytes(pointer_path)
        with self._cache_lock:
            if self._cached_pointer_bytes == pointer_bytes and self._cached_handle is not None:
                return self._cached_handle
            handle = self._load_uncached(pointer_path, pointer_bytes)
            self._cached_pointer_bytes = pointer_bytes
            self._cached_handle = handle
            return handle

    def _load_uncached(self, pointer_path: Path, expected_pointer_bytes: bytes) -> ReleaseHandle:
        pointer_bytes = _read_regular_bytes(pointer_path)
        if pointer_bytes != expected_pointer_bytes:
            raise RuntimeError("active index pointer changed during load")
        pointer = json.loads(pointer_bytes)
        if set(pointer) != {"release_id", "manifest_sha256"} or not all(isinstance(pointer[key], str) and pointer[key] for key in pointer):
            raise RuntimeError("active index pointer schema mismatch")
        release_id = pointer["release_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", release_id):
            raise RuntimeError("active release id is invalid")
        release_root = self.runtime_root / "index-release" / release_id
        manifest_path, corpus_path = release_root / "manifest.json", release_root / "corpus.sqlite"
        if not manifest_path.is_file() or not corpus_path.is_file() or manifest_path.is_symlink() or corpus_path.is_symlink():
            raise RuntimeError("active release files are unavailable")
        if stat.S_IMODE(corpus_path.stat().st_mode) & 0o222:
            raise RuntimeError("active corpus must be read-only")
        manifest_bytes = _read_regular_bytes(manifest_path)
        manifest_hash = _sha256_bytes(manifest_bytes)
        if manifest_hash != pointer["manifest_sha256"]:
            raise RuntimeError("active pointer manifest hash mismatch")
        manifest = json.loads(manifest_bytes)
        required = {"release_id", "strategy", "chunking_contract", "release_status", "distance_contract", "corpus_hash", "corpus_sqlite_sha256", "chunk_ids", "document_ids", "catalog", "embedding_dimension", "embedding_model", "embedding_sha256", "chroma_tree_sha256"}
        if not required <= set(manifest) or manifest.get("schema_version") != "rag_index_release_v1" or manifest["release_id"] != release_id or manifest["strategy"] not in CHUNKING_PROFILES or manifest["release_status"] not in self.allowed_release_statuses or manifest["distance_contract"] != "squared_l2":
            raise RuntimeError("active release manifest contract mismatch")
        if manifest["chunking_contract"] != CHUNKING_CONTRACTS[manifest["strategy"]]:
            raise RuntimeError("active release chunking contract mismatch")
        if not isinstance(manifest["embedding_dimension"], int) or manifest["embedding_dimension"] < 1 or not isinstance(manifest["embedding_model"], str) or not manifest["embedding_model"] or not isinstance(manifest["embedding_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["embedding_sha256"]):
            raise RuntimeError("active embedding contract is invalid")
        if not isinstance(manifest["corpus_sqlite_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["corpus_sqlite_sha256"]) or _sha256(corpus_path) != manifest["corpus_sqlite_sha256"]:
            raise RuntimeError("active SQLite corpus hash mismatch")
        tree_hash = manifest["chroma_tree_sha256"]
        if not isinstance(tree_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", tree_hash) or _tree_hash(release_root / "chroma") != tree_hash:
            raise RuntimeError("release Chroma tree hash mismatch")

        uri = f"file:{quote(str(corpus_path))}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT chunk_id,document_id,level,text,metadata_json FROM chunks ORDER BY chunk_id").fetchall()
            fts_count = connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
        finally:
            connection.close()
        records = [{"chunk_id": row["chunk_id"], "document_id": row["document_id"], "level": row["level"], "text": row["text"], "metadata": json.loads(row["metadata_json"])} for row in rows]
        ids = [row["chunk_id"] for row in records]
        if ids != manifest["chunk_ids"] or fts_count != len(ids) or hashlib.sha256(_canonical(records).encode()).hexdigest() != manifest["corpus_hash"]:
            raise RuntimeError("corpus identity or hash mismatch")
        catalog = tuple(sorted(({"card_key": row["document_id"], "card_name": row["card_name"], "issuer": row["issuer_name"]} for row in manifest["catalog"]), key=lambda row: row["card_key"]))
        catalog_by_key = {row["card_key"]: row for row in catalog}
        if len(catalog_by_key) != len(catalog) or set(catalog_by_key) != set(manifest["document_ids"]):
            raise RuntimeError("release catalog identity mismatch")
        chunks: list[Chunk] = []
        for record in records:
            metadata = record["metadata"]
            card = catalog_by_key.get(record["document_id"])
            pages = metadata.get("source_pages") if isinstance(metadata, dict) else None
            child_ids = metadata.get("child_ids") if isinstance(metadata, dict) else None
            related_ids = metadata.get("related_chunk_ids") if isinstance(metadata, dict) else None
            heading_path = metadata.get("heading_path") if isinstance(metadata, dict) else None
            retrieval_text = metadata.get("retrieval_text") if isinstance(metadata, dict) else None
            reranker_text = metadata.get("reranker_text") if isinstance(metadata, dict) else None
            if (
                card is None
                or not isinstance(pages, list)
                or not pages
                or any(isinstance(page, bool) or not isinstance(page, int) or page < 1 for page in pages)
                or not isinstance(child_ids, list)
                or any(not isinstance(child_id, str) or not child_id for child_id in child_ids)
                or not isinstance(related_ids, list)
                or any(not isinstance(chunk_id, str) or not chunk_id for chunk_id in related_ids)
                or not isinstance(retrieval_text, str)
                or not retrieval_text.strip()
                or not isinstance(reranker_text, str)
                or not reranker_text.strip()
            ):
                raise RuntimeError("chunk catalog, search text, or page provenance mismatch")
            chunks.append(Chunk(
                record["chunk_id"],
                record["text"],
                card["card_key"],
                card["card_name"],
                card["issuer"],
                record["level"],
                min(pages),
                metadata.get("section"),
                reranker_text,
                metadata.get("parent_id"),
                tuple(child_ids),
                metadata.get("node_id"),
                tuple(heading_path or ()),
                metadata.get("part_index", 1),
                tuple(related_ids),
                metadata.get("optional_parent_heading"),
            ))
        chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        if len(chunks_by_id) != len(chunks):
            raise RuntimeError("chunk IDs are duplicated")
        child_owners: dict[str, str] = {}
        expected_aggregate_level = "section"
        for chunk in chunks:
            if manifest["strategy"] == "parent_child_bundle":
                if chunk.level != "structural" or chunk.node_id is None or chunk.child_ids:
                    raise RuntimeError("parent-child structural chunk contract mismatch")
                if len(set(chunk.related_chunk_ids)) != len(chunk.related_chunk_ids) or chunk.chunk_id in chunk.related_chunk_ids:
                    raise RuntimeError("parent-child related chunk list is invalid")
                continue
            if chunk.level in {"section", "bundle"}:
                if chunk.level != expected_aggregate_level:
                    raise RuntimeError("aggregate level does not match the chunking profile")
                if not chunk.child_ids:
                    raise RuntimeError("aggregate chunk has no benefit children")
                for child_id in chunk.child_ids:
                    child = chunks_by_id.get(child_id)
                    if (
                        child is None
                        or child.level != "benefit"
                        or child.parent_id != chunk.chunk_id
                        or child.card_key != chunk.card_key
                        or child_id in child_owners
                    ):
                        raise RuntimeError("aggregate child graph mismatch")
                    child_owners[child_id] = chunk.chunk_id
            elif chunk.child_ids:
                raise RuntimeError("only aggregate chunks may declare children")
            if chunk.level == "benefit" and chunk.parent_id is not None:
                parent = chunks_by_id.get(chunk.parent_id)
                if parent is None or parent.level not in {"section", "bundle"} or parent.card_key != chunk.card_key:
                    raise RuntimeError("benefit parent graph mismatch")
        for chunk in chunks:
            if chunk.level == "benefit" and chunk.parent_id is not None and child_owners.get(chunk.chunk_id) != chunk.parent_id:
                raise RuntimeError("benefit is missing from its parent's child graph")
            if manifest["strategy"] == "parent_child_bundle" and any(
                related not in chunks_by_id
                or chunks_by_id[related].card_key != chunk.card_key
                or chunks_by_id[related].level != "structural"
                for related in chunk.related_chunk_ids
            ):
                raise RuntimeError("parent-child related chunk graph mismatch")

        serving_root = self.runtime_root / "serving" / release_id / tree_hash
        marker_path, chroma_root = serving_root / "version.json", serving_root / "chroma"
        with _serving_release_lock(self.runtime_root, release_id):
            _tree_entries(serving_root)
            if marker_path.is_symlink() or not marker_path.is_file() or not chroma_root.is_dir():
                raise RuntimeError("serving version is unavailable")
            marker = json.loads(_read_regular_bytes(marker_path))
            static_marker = {"release_id": release_id, "chroma_tree_sha256": tree_hash, "corpus_hash": manifest["corpus_hash"], "chunk_ids": ids, "embedding_dimension": manifest["embedding_dimension"], "embedding_sha256": manifest["embedding_sha256"]}
            if marker != static_marker:
                raise RuntimeError("serving marker or content mismatch")
            import chromadb

            collection = chromadb.PersistentClient(path=str(chroma_root)).get_collection(manifest["strategy"])
            output = collection.get(include=["embeddings"])
            output_by_id = {
                chunk_id: embedding
                for chunk_id, embedding in zip(output["ids"], output["embeddings"], strict=True)
            }
            stored_embeddings = np.asarray(
                [output_by_id[chunk_id] for chunk_id in ids if chunk_id in output_by_id],
                dtype=np.float32,
            )
            if set(output_by_id) != set(ids) or stored_embeddings.shape != (len(ids), manifest["embedding_dimension"]) or collection.metadata.get("corpus_hash") != manifest["corpus_hash"] or _embedding_sha256(ids, stored_embeddings) != manifest["embedding_sha256"]:
                raise RuntimeError("serving Chroma identity mismatch")
            if _read_regular_bytes(pointer_path) != pointer_bytes or _read_regular_bytes(manifest_path) != manifest_bytes:
                raise RuntimeError("active release changed during validation")
            _tree_entries(serving_root)
        pipeline = RagPipeline(
            chunks,
            SQLiteFTSSearcher(corpus_path),
            ChromaVectorSearcher(collection, manifest["embedding_model"]),
            self.reranker,
            profile=CHUNKING_PROFILES[manifest["strategy"]],
        )
        return ReleaseHandle(release_id, manifest_hash, manifest, tuple(chunks), catalog, pipeline)
