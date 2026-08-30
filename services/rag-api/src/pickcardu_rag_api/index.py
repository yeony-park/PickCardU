from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import quote

import numpy as np
from pickcardu_rag import Candidate, Chunk, RagPipeline, SearchConfig, normalized_tokens


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

    def __init__(self, runtime_root: Path, *, reranker: Any = None) -> None:
        self.runtime_root = runtime_root
        self.reranker = reranker

    def load(self) -> ReleaseHandle:
        pointer_path = self.runtime_root / "active-index.json"
        if not pointer_path.is_file() or pointer_path.is_symlink():
            raise RuntimeError("active index pointer is unavailable")
        pointer_bytes = _read_regular_bytes(pointer_path)
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
        required = {"release_id", "strategy", "release_status", "distance_contract", "corpus_hash", "chunk_ids", "document_ids", "catalog", "embedding_dimension", "embedding_model", "chroma_tree_sha256"}
        if not required <= set(manifest) or manifest.get("schema_version") != "rag_index_release_v1" or manifest["release_id"] != release_id or manifest["strategy"] != "benefit_hierarchy" or manifest["release_status"] != "production" or manifest["distance_contract"] != "squared_l2":
            raise RuntimeError("active release manifest contract mismatch")
        if not isinstance(manifest["embedding_dimension"], int) or manifest["embedding_dimension"] < 1 or not isinstance(manifest["embedding_model"], str) or not manifest["embedding_model"]:
            raise RuntimeError("active embedding contract is invalid")
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
            references = metadata.get("evidence_refs") if isinstance(metadata, dict) else None
            pages = [reference.get("page") for reference in references.values()] if isinstance(references, dict) else []
            pages = [page for page in pages if isinstance(page, int) and not isinstance(page, bool) and page >= 0]
            if card is None or not pages:
                raise RuntimeError("chunk catalog or page provenance mismatch")
            chunks.append(Chunk(record["chunk_id"], record["text"], card["card_key"], card["card_name"], card["issuer"], record["level"], min(pages), metadata.get("section")))

        serving_root = self.runtime_root / "serving" / release_id / tree_hash
        marker_path, chroma_root = serving_root / "version.json", serving_root / "chroma"
        with _serving_release_lock(self.runtime_root, release_id):
            _tree_entries(serving_root)
            if marker_path.is_symlink() or not marker_path.is_file() or not chroma_root.is_dir():
                raise RuntimeError("serving version is unavailable")
            marker = json.loads(_read_regular_bytes(marker_path))
            static_marker = {"release_id": release_id, "chroma_tree_sha256": tree_hash, "corpus_hash": manifest["corpus_hash"], "chunk_ids": ids, "embedding_dimension": manifest["embedding_dimension"]}
            serving_tree_hash = marker.get("serving_tree_sha256")
            if {key: marker.get(key) for key in static_marker} != static_marker or not isinstance(serving_tree_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", serving_tree_hash):
                raise RuntimeError("serving marker or content mismatch")
            if _tree_hash(chroma_root) != serving_tree_hash:
                raise RuntimeError("serving Chroma tree hash mismatch")
            import chromadb

            collection = chromadb.PersistentClient(path=str(chroma_root)).get_collection("benefit_hierarchy")
            output = collection.get(include=["embeddings"])
            if sorted(output["ids"]) != ids or np.asarray(output["embeddings"]).shape != (len(ids), manifest["embedding_dimension"]) or collection.metadata.get("corpus_hash") != manifest["corpus_hash"]:
                raise RuntimeError("serving Chroma identity mismatch")
            if _tree_hash(chroma_root) != serving_tree_hash or _read_regular_bytes(pointer_path) != pointer_bytes or _read_regular_bytes(manifest_path) != manifest_bytes:
                raise RuntimeError("active release changed during validation")
            _tree_entries(serving_root)
        pipeline = RagPipeline(chunks, SQLiteFTSSearcher(corpus_path), ChromaVectorSearcher(collection, manifest["embedding_model"]), self.reranker)
        return ReleaseHandle(release_id, manifest_hash, manifest, tuple(chunks), catalog, pipeline)
