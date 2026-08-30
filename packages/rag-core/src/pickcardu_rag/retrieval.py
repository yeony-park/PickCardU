"""Deterministic retrieval pipeline over injected lexical and vector adapters."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

import numpy as np

from .answering import ANSWER_PAYLOAD_UNIT_LIMIT, measure_answer_payload
from .errors import RerankerUnavailable


TOKEN_PATTERN = re.compile(r"[\w]+(?:[./+-][\w]+)*%?", re.UNICODE)
GTE_REVISION = "40ced75c3017eb27626c9d4ea981bde21a2662f4"
RERANKER_BATCH_SIZE = 2
RERANKER_REQUESTED_MAX_LENGTH = 8192
PROPER_QUESTION_PATTERN = re.compile(
    r"(?:어느\s*(?:카드사|은행|회사)\s*상품(?:인가|이야)|발급사(?:는|가)?|어떤\s*상품(?:인가|이야))\s*[?？]?$"
)
NUMERIC_QUESTION_PATTERN = re.compile(
    r"(?:(?:할인율|적립률|연회비|(?:할인\s*)?금액|한도|적립\s*기준)(?:은|는|이|가)?"
    r"(?:\s*(?:얼마(?:인가|야)?|어떻게\s*(?:되나|돼)|알려\s*줘))?"
    r"|얼마(?:인가|야)?|몇\s*(?:원|회|개월)(?:인가|야)?)\s*[?？]?$"
)
_ARTIFACT_CACHE: dict[str, tuple[tuple[tuple[str, int, int, int], ...], str]] = {}
_ARTIFACT_LOCK = threading.Lock()


@dataclass(frozen=True)
class Chunk:
    chunk_id: str
    text: str
    card_key: str
    card_name: str
    issuer: str
    level: str
    page_num: int
    section: str | None = None

    def __post_init__(self) -> None:
        for name in ("chunk_id", "text", "card_key", "card_name", "issuer", "level"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(self.page_num, bool) or not isinstance(self.page_num, int) or self.page_num < 0:
            raise ValueError("page_num must be a non-negative integer")
        if self.section is not None and not isinstance(self.section, str):
            raise ValueError("section must be a string or None")


@dataclass(frozen=True)
class Candidate:
    chunk_id: str
    score: float
    rank: int
    component_ranks: Mapping[str, int | None] = field(default_factory=dict)
    prior_rank: int | None = None


class LexicalSearcher(Protocol):
    def search(self, query: str, *, limit: int) -> list[Candidate]: ...


class VectorSearcher(Protocol):
    embedding_model: str | None

    def search(self, query_embedding: np.ndarray, *, limit: int) -> list[Candidate]: ...


class Reranker(Protocol):
    def score(self, mode: str, query: str, documents: list[str]) -> tuple[list[float], dict[str, Any]]: ...


@dataclass(frozen=True)
class ChunkingProfile:
    identifier: str
    eligible_levels: frozenset[str]


BENEFIT_HIERARCHY = ChunkingProfile("benefit_hierarchy", frozenset({"section", "benefit"}))


@dataclass(frozen=True)
class SearchConfig:
    profile: str = "benefit_hierarchy"
    vector_weight: float = 0.4
    component_depth: int = 50
    candidate_depth: int = 20
    top_k: int = 3
    reranker: Literal["off", "bge", "gte"] = "bge"
    reranker_route: Literal["selective", "all"] = "selective"

    def __post_init__(self) -> None:
        if not isinstance(self.profile, str) or not self.profile.strip() or not 0.0 <= self.vector_weight <= 1.0:
            raise ValueError("invalid profile or vector weight")
        if self.reranker not in {"off", "bge", "gte"}:
            raise ValueError("unsupported reranker")
        if self.reranker_route not in {"selective", "all"}:
            raise ValueError("unsupported reranker route")
        if min(self.component_depth, self.candidate_depth, self.top_k) < 1:
            raise ValueError("search depths and top_k must be positive")


def normalize_text(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def normalized_tokens(text: str) -> list[str]:
    return [token for token in TOKEN_PATTERN.findall(normalize_text(text)) if token != "_"]


class BM25:
    def __init__(self, documents: Iterable[str], tokenizer_fn: Callable[[str], list[str]] = normalized_tokens) -> None:
        self.tokens = [tokenizer_fn(document) for document in documents]
        self.lengths = np.asarray([len(tokens) for tokens in self.tokens], dtype=np.float64)
        self.avg_length = float(self.lengths.mean()) if len(self.lengths) else 0.0
        self.term_frequencies = [Counter(tokens) for tokens in self.tokens]
        document_frequency = Counter(token for tokens in self.tokens for token in set(tokens))
        count = len(self.tokens)
        self.idf = {
            token: math.log(1.0 + (count - frequency + 0.5) / (frequency + 0.5))
            for token, frequency in document_frequency.items()
        }

    def scores(self, query_tokens: Iterable[str], *, k1: float = 1.5, b: float = 0.75) -> np.ndarray:
        result = np.zeros(len(self.tokens), dtype=np.float64)
        if not self.avg_length:
            return result
        for token in set(query_tokens):
            idf = self.idf.get(token)
            if idf is None:
                continue
            frequencies = np.asarray([terms.get(token, 0) for terms in self.term_frequencies], dtype=np.float64)
            denominator = frequencies + k1 * (1.0 - b + b * self.lengths / self.avg_length)
            result += idf * frequencies * (k1 + 1.0) / np.where(denominator == 0.0, 1.0, denominator)
        return result


def rank_scores(
    scores: np.ndarray, chunk_ids: Sequence[str], *, descending: bool, limit: int | None = None
) -> list[Candidate]:
    ordered = sorted(
        range(len(chunk_ids)), key=lambda index: ((-scores[index] if descending else scores[index]), chunk_ids[index])
    )
    return [
        Candidate(chunk_id=chunk_ids[index], score=float(scores[index]), rank=rank)
        for rank, index in enumerate(ordered[:limit], 1)
    ]


def squared_l2_rank(
    query_embedding: np.ndarray, embeddings: np.ndarray, chunk_ids: Sequence[str], limit: int | None = None
) -> list[Candidate]:
    query = np.asarray(query_embedding, dtype=np.float32)
    if (
        embeddings.ndim != 2
        or embeddings.shape[0] != len(chunk_ids)
        or query.shape != (embeddings.shape[1],)
        or not np.isfinite(query).all()
        or not np.isfinite(embeddings).all()
    ):
        raise ValueError("query embedding dimension or finiteness mismatch")
    distances = np.sum((embeddings - query) ** 2, axis=1, dtype=np.float64)
    return rank_scores(distances, chunk_ids, descending=False, limit=limit)


class InMemoryBM25Searcher:
    def __init__(self, chunks: Sequence[Chunk]) -> None:
        self.chunks = tuple(chunks)
        self.chunk_ids = tuple(chunk.chunk_id for chunk in chunks)
        self.index = BM25(chunk.text for chunk in chunks)

    def search(self, query: str, *, limit: int) -> list[Candidate]:
        scores = self.index.scores(normalized_tokens(query))
        return rank_scores(scores, self.chunk_ids, descending=True, limit=limit)


class InMemorySquaredL2Searcher:
    def __init__(self, chunk_ids: Sequence[str], embeddings: np.ndarray, *, embedding_model: str | None = None) -> None:
        self.chunk_ids = tuple(chunk_ids)
        self.embeddings = np.asarray(embeddings, dtype=np.float32)
        if self.embeddings.ndim != 2 or self.embeddings.shape[0] != len(self.chunk_ids):
            raise ValueError("embedding rows must match chunk ids")
        if not np.isfinite(self.embeddings).all():
            raise ValueError("embeddings must be finite")
        self.embedding_model = embedding_model

    def search(self, query_embedding: np.ndarray, *, limit: int) -> list[Candidate]:
        return squared_l2_rank(query_embedding, self.embeddings, self.chunk_ids, limit)

def weighted_rrf(
    ranked_components: Mapping[str, Sequence[Candidate]], weights: Mapping[str, float], *, k: int = 60
) -> list[Candidate]:
    scores: dict[str, float] = {}
    component_ranks: dict[str, dict[str, int]] = {}
    for component, rows in ranked_components.items():
        component_ranks[component] = {}
        if weights[component] <= 0.0:
            continue
        for row in rows:
            component_ranks[component][row.chunk_id] = row.rank
            scores[row.chunk_id] = scores.get(row.chunk_id, 0.0) + weights[component] / (k + row.rank)
    ordered = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
    return [
        Candidate(
            chunk_id=chunk_id,
            score=scores[chunk_id],
            rank=rank,
            component_ranks={name: ranks.get(chunk_id) for name, ranks in component_ranks.items()},
        )
        for rank, chunk_id in enumerate(ordered, 1)
    ]


def classify_query(query: str) -> Literal["proper_noun", "numeric_condition", "semantic"]:
    normalized = normalize_text(query)
    if PROPER_QUESTION_PATTERN.search(normalized):
        return "proper_noun"
    if NUMERIC_QUESTION_PATTERN.search(normalized):
        return "numeric_condition"
    return "semantic"


def collapse_cards(
    rows: Sequence[Candidate],
    chunks: Mapping[str, Chunk],
    *,
    top_k: int,
    standalone_query: str = "",
    max_evidence_per_card: int = 5,
    max_payload_size: int = ANSWER_PAYLOAD_UNIT_LIMIT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    by_card: dict[str, dict[str, Any]] = {}
    dropped_chunk_ids: list[str] = []
    for row in rows:
        chunk = chunks[row.chunk_id]
        if isinstance(chunk.page_num, bool) or not isinstance(chunk.page_num, int) or chunk.page_num < 0:
            raise ValueError("invalid evidence page_num")
        card = by_card.get(chunk.card_key)
        if (card is None and len(cards) >= top_k) or (
            card is not None and card["evidence_count"] >= max_evidence_per_card
        ):
            dropped_chunk_ids.append(chunk.chunk_id)
            continue
        candidate = {
            "rank": len(evidence) + 1,
            "card_key": chunk.card_key,
            "card_name": chunk.card_name,
            "issuer": chunk.issuer,
            "chunk_id": chunk.chunk_id,
            "page_num": chunk.page_num,
            "text": chunk.text,
            "section": chunk.section,
            "level": chunk.level,
            "score": row.score,
        }
        if measure_answer_payload(standalone_query, [*evidence, candidate])[0] > max_payload_size:
            dropped_chunk_ids.append(chunk.chunk_id)
            continue
        if card is None:
            card = {
                "card_key": chunk.card_key,
                "card_name": chunk.card_name,
                "issuer": chunk.issuer,
                "score": row.score,
                "rank": len(cards) + 1,
                "evidence_count": 0,
            }
            cards.append(card)
            by_card[chunk.card_key] = card
        card["evidence_count"] += 1
        evidence.append(candidate)
    payload_size, payload_unit = measure_answer_payload(standalone_query, evidence)
    return cards, evidence, {
        "payload_unit": payload_unit,
        "payload_unit_limit": max_payload_size,
        "payload_size": payload_size,
        "dropped_chunk_count": len(dropped_chunk_ids),
        "dropped_chunk_ids": dropped_chunk_ids,
    }


def _candidate_dict(row: Candidate) -> dict[str, Any]:
    result: dict[str, Any] = {"chunk_id": row.chunk_id, "score": row.score, "rank": row.rank}
    if row.component_ranks:
        result["component_ranks"] = dict(row.component_ranks)
    if row.prior_rank is not None:
        result["prior_rank"] = row.prior_rank
    return result


class RagPipeline:
    def __init__(
        self,
        chunks: Sequence[Chunk],
        lexical_searcher: LexicalSearcher,
        vector_searcher: VectorSearcher | None = None,
        reranker: Reranker | None = None,
        *,
        profile: ChunkingProfile = BENEFIT_HIERARCHY,
    ) -> None:
        self.chunks = {chunk.chunk_id: chunk for chunk in chunks}
        if len(self.chunks) != len(chunks):
            raise ValueError("chunk ids must be unique")
        self.lexical_searcher = lexical_searcher
        self.vector_searcher = vector_searcher
        self.reranker = reranker
        self.profile = profile

    def search(
        self, query: str, query_embedding: np.ndarray | None = None, config: SearchConfig = SearchConfig()
    ) -> dict[str, Any]:
        if config.profile != self.profile.identifier:
            raise ValueError("search profile does not match the registered chunking contract")
        started = time.perf_counter()
        query_type = classify_query(query)
        lexical = self.lexical_searcher.search(query, limit=config.component_depth)
        vector: list[Candidate] = []
        if config.vector_weight:
            if query_embedding is None or self.vector_searcher is None:
                raise ValueError("vector searcher and query embedding are required")
            vector = self.vector_searcher.search(query_embedding, limit=config.component_depth)
        components: dict[str, Sequence[Candidate]] = {"bm25": lexical}
        weights = {"bm25": 1.0 - config.vector_weight}
        if vector:
            components["vector"] = vector
            weights["vector"] = config.vector_weight
        fused = weighted_rrf(components, weights)
        leaf = [row for row in fused if self.chunks[row.chunk_id].level in self.profile.eligible_levels][
            : config.candidate_depth
        ]
        should_rerank = config.reranker != "off" and (
            config.reranker_route == "all" or query_type == "semantic"
        )
        reranker_trace = None
        reranked = leaf
        if should_rerank:
            if self.reranker is None:
                raise RerankerUnavailable("reranker is unavailable")
            scores, reranker_trace = self.reranker.score(
                config.reranker, query, [self.chunks[row.chunk_id].text for row in leaf]
            )
            if len(scores) != len(leaf) or not np.isfinite(np.asarray(scores, dtype=np.float64)).all():
                raise RerankerUnavailable("reranker score count or finiteness mismatch")
            reranked = [
                Candidate(**{**row.__dict__, "score": float(scores[index]), "prior_rank": row.rank})
                for index, row in enumerate(leaf)
            ]
            reranked.sort(key=lambda row: (-row.score, row.prior_rank or row.rank, row.chunk_id))
            reranked = [Candidate(**{**row.__dict__, "rank": rank}) for rank, row in enumerate(reranked, 1)]
        cards, evidence, budget = collapse_cards(
            reranked, self.chunks, top_k=config.top_k, standalone_query=query
        )
        return {
            "query_type": query_type,
            "cards": cards,
            "evidence": evidence,
            "trace": {
                "vector": [_candidate_dict(row) for row in vector],
                "bm25": [_candidate_dict(row) for row in lexical],
                "rrf": [_candidate_dict(row) for row in fused],
                "leaf": [_candidate_dict(row) for row in leaf],
                "rerank": [_candidate_dict(row) for row in reranked] if should_rerank else [],
                "reranker": reranker_trace,
                "card": cards,
                "evidence_budget": budget,
                "profile": self.profile.identifier,
                "latency": {"total_ms": round((time.perf_counter() - started) * 1000, 3)},
            },
        }


def fingerprint_local_artifact(path: str | Path) -> str:
    root = Path(path)
    if root.is_symlink() or not root.is_dir():
        raise RerankerUnavailable("local model artifact must be a regular directory")
    files: list[tuple[str, Path, os.stat_result]] = []
    for current, directories, filenames in os.walk(root, followlinks=False):
        directories.sort()
        filenames.sort()
        for name in directories:
            mode = os.lstat(Path(current) / name).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise RerankerUnavailable("local model artifact contains an invalid directory")
        for name in filenames:
            file_path = Path(current) / name
            file_stat = os.lstat(file_path)
            if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
                raise RerankerUnavailable("local model artifact contains an invalid file")
            files.append((file_path.relative_to(root).as_posix(), file_path, file_stat))
    if not files:
        raise RerankerUnavailable("local model artifact directory is empty")
    signature = tuple(
        (relative, file_stat.st_size, file_stat.st_mtime_ns, file_stat.st_ctime_ns)
        for relative, _, file_stat in files
    )
    cache_key = str(root.resolve())
    with _ARTIFACT_LOCK:
        cached = _ARTIFACT_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
        digest = hashlib.sha256()
        for relative, file_path, file_stat in files:
            digest.update(relative.encode())
            digest.update(b"\0")
            digest.update(str(file_stat.st_size).encode())
            digest.update(b"\0")
            with file_path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        fingerprint = digest.hexdigest()
        _ARTIFACT_CACHE[cache_key] = (signature, fingerprint)
        return fingerprint


def require_gte_revision_declaration(path: str | Path) -> None:
    root = Path(path)
    revisions: set[str] = set()
    revision_file = root / "revision.txt"
    if revision_file.is_file():
        revisions.add(revision_file.read_text(encoding="utf-8").strip())
    for name in ("manifest.json", "config.json"):
        file_path = root / name
        if not file_path.is_file():
            continue
        try:
            document = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RerankerUnavailable(f"invalid GTE revision manifest: {name}") from exc
        if isinstance(document, dict):
            for key in ("revision", "commit_hash", "_commit_hash"):
                if isinstance(document.get(key), str):
                    revisions.add(document[key])
    if revisions != {GTE_REVISION}:
        raise RerankerUnavailable("GTE local artifact does not declare the pinned revision")


def _scores_from_logits(logits: Any, expected_count: int) -> list[float]:
    shape = tuple(int(value) for value in logits.shape)
    if shape == (expected_count,):
        values = logits.float().cpu().tolist()
    elif shape == (expected_count, 1):
        values = [row[0] for row in logits.float().cpu().tolist()]
    else:
        raise RerankerUnavailable(f"reranker logits shape mismatch: {shape}")
    if len(values) != expected_count or not np.isfinite(np.asarray(values, dtype=np.float64)).all():
        raise RerankerUnavailable("reranker scores are missing or non-finite")
    return [float(value) for value in values]


class LocalReranker:
    # Process-lifetime cache prevents duplicate loading of multi-gigabyte local models.
    _lock = threading.Lock()
    _models: dict[tuple[str, str, str], tuple[Any, Any, str, str, int]] = {}

    def __init__(self, bge_path: str, gte_path: str | None = None, gte_allow_custom_code: bool = False) -> None:
        self.bge_path = bge_path
        self.gte_path = gte_path
        self.gte_allow_custom_code = gte_allow_custom_code

    def artifact_contract(self, mode: str) -> dict[str, Any]:
        path = self.bge_path if mode == "bge" else self.gte_path
        if mode not in {"bge", "gte"} or not path:
            raise RerankerUnavailable(f"{mode} local model path is unavailable")
        if mode == "gte":
            if not self.gte_allow_custom_code:
                raise RerankerUnavailable("GTE custom code opt-in is disabled")
            require_gte_revision_declaration(path)
        try:
            fingerprint = fingerprint_local_artifact(path)
            path_hash = hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()
        except RerankerUnavailable:
            raise
        except OSError as exc:
            raise RerankerUnavailable(f"{mode} local model artifact is unreadable") from exc
        return {
            "artifact_fingerprint": fingerprint,
            "model_path_hash": path_hash,
            "revision": GTE_REVISION if mode == "gte" else None,
            "revision_claim_unverified": mode == "gte",
        }

    @staticmethod
    def _effective_max_length(tokenizer: Any, model: Any) -> int:
        limits = [RERANKER_REQUESTED_MAX_LENGTH]
        for value in (
            getattr(tokenizer, "model_max_length", None),
            getattr(getattr(model, "config", None), "max_position_embeddings", None),
        ):
            if isinstance(value, int) and 2 <= value < 1_000_000:
                limits.append(value)
        return min(limits)

    def score(self, mode: str, query: str, documents: list[str]) -> tuple[list[float], dict[str, Any]]:
        path = self.bge_path if mode == "bge" else self.gte_path
        contract = self.artifact_contract(mode)
        assert path is not None
        key = (mode, path, contract["artifact_fingerprint"])
        with self._lock:
            if key not in self._models:
                try:
                    import torch
                    from transformers import AutoModelForSequenceClassification, AutoTokenizer

                    use_cuda = torch.cuda.is_available()
                    tokenizer = AutoTokenizer.from_pretrained(
                        path,
                        local_files_only=True,
                        trust_remote_code=mode == "gte",
                        revision=GTE_REVISION if mode == "gte" else None,
                    )
                    model = AutoModelForSequenceClassification.from_pretrained(
                        path,
                        local_files_only=True,
                        trust_remote_code=mode == "gte",
                        revision=GTE_REVISION if mode == "gte" else None,
                        torch_dtype=torch.float16 if use_cuda else torch.float32,
                    ).to("cuda" if use_cuda else "cpu").eval()
                    self._models[key] = (
                        tokenizer,
                        model,
                        "cuda" if use_cuda else "cpu",
                        "float16" if use_cuda else "float32",
                        self._effective_max_length(tokenizer, model),
                    )
                except Exception as exc:
                    raise RerankerUnavailable(f"{mode} local model load failed: {type(exc).__name__}") from exc
            tokenizer, model, device, dtype, max_length = self._models[key]
            scores: list[float] = []
            input_token_count = truncated_count = batch_count = 0
            try:
                import torch

                for offset in range(0, len(documents), RERANKER_BATCH_SIZE):
                    pairs = [[query, document] for document in documents[offset : offset + RERANKER_BATCH_SIZE]]
                    token_lengths = [
                        len(ids) for ids in tokenizer(pairs, truncation=False, add_special_tokens=True)["input_ids"]
                    ]
                    input_token_count += sum(token_lengths)
                    truncated_count += sum(length > max_length for length in token_lengths)
                    encoded = tokenizer(
                        pairs, padding=True, truncation=True, max_length=max_length, return_tensors="pt"
                    )
                    encoded = {name: value.to(device) for name, value in encoded.items()}
                    with torch.no_grad():
                        logits = model(**encoded).logits
                    scores.extend(_scores_from_logits(logits, len(pairs)))
                    batch_count += 1
            except RerankerUnavailable:
                raise
            except Exception as exc:
                raise RerankerUnavailable(f"{mode} scoring failed: {type(exc).__name__}") from exc
        if len(scores) != len(documents):
            raise RerankerUnavailable("reranker score count mismatch")
        return scores, {
            **contract,
            "device": device,
            "dtype": dtype,
            "batch_size": RERANKER_BATCH_SIZE,
            "batch_count": batch_count,
            "input_token_count": input_token_count,
            "truncated_count": truncated_count,
            "requested_max_length": RERANKER_REQUESTED_MAX_LENGTH,
            "effective_max_length": max_length,
        }
