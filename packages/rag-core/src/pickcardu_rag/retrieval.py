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
from dataclasses import dataclass, field, replace
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Mapping, Protocol, Sequence

import numpy as np

from .answering import ANSWER_PAYLOAD_UNIT_LIMIT, measure_answer_payload
from .errors import EvidencePackageTooLarge, RerankerUnavailable


TOKEN_PATTERN = re.compile(r"[가-힣a-z0-9]+(?:[.,%+~-][가-힣a-z0-9]+)*", re.IGNORECASE)
LEXICAL_CONTRACT = "normalized_ko_numeric_hex_v1"
QUERY_CLASSIFIER_CONTRACT = "query_text_conservative_v2"
FUSED_WORKLIST_DEPTH = 50
PARENT_CHILD_DOCUMENT_TOKEN_LIMIT = 4096
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
    reranker_text: str | None = None
    parent_id: str | None = None
    child_ids: tuple[str, ...] = ()
    node_id: str | None = None
    heading_path: tuple[str, ...] = ()
    part_index: int = 1
    related_chunk_ids: tuple[str, ...] = ()
    optional_parent_heading: str | None = None

    def __post_init__(self) -> None:
        for name in ("chunk_id", "text", "card_key", "card_name", "issuer", "level"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if isinstance(self.page_num, bool) or not isinstance(self.page_num, int) or self.page_num < 0:
            raise ValueError("page_num must be a non-negative integer")
        if self.section is not None and not isinstance(self.section, str):
            raise ValueError("section must be a string or None")
        if self.reranker_text is not None and (not isinstance(self.reranker_text, str) or not self.reranker_text.strip()):
            raise ValueError("reranker_text must be a non-empty string or None")
        if self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id.strip()):
            raise ValueError("parent_id must be a non-empty string or None")
        if not isinstance(self.child_ids, tuple) or any(
            not isinstance(child_id, str) or not child_id.strip() for child_id in self.child_ids
        ):
            raise ValueError("child_ids must be a tuple of non-empty strings")
        if self.node_id is not None and (not isinstance(self.node_id, str) or not self.node_id.strip()):
            raise ValueError("node_id must be a non-empty string or None")
        if not isinstance(self.heading_path, tuple) or any(not isinstance(item, str) or not item.strip() for item in self.heading_path):
            raise ValueError("heading_path must contain non-empty strings")
        if isinstance(self.part_index, bool) or not isinstance(self.part_index, int) or self.part_index < 1:
            raise ValueError("part_index must be positive")
        if not isinstance(self.related_chunk_ids, tuple) or any(not isinstance(item, str) or not item.strip() for item in self.related_chunk_ids):
            raise ValueError("related_chunk_ids must contain non-empty strings")
        if self.optional_parent_heading is not None and (not isinstance(self.optional_parent_heading, str) or not self.optional_parent_heading.strip()):
            raise ValueError("optional_parent_heading must be a non-empty string or None")


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
    def score(self, mode: str, query: str, documents: list[str], *, document_token_limit: int | None = None) -> tuple[list[float], dict[str, Any]]: ...


@dataclass(frozen=True)
class ChunkingProfile:
    identifier: str
    eligible_levels: frozenset[str]


CARD_PAGE_SECTION_BENEFIT = ChunkingProfile(
    "card_page_section_benefit", frozenset({"section", "benefit"})
)
PARENT_CHILD_BUNDLE = ChunkingProfile(
    "parent_child_bundle", frozenset({"structural"})
)
# Historical import alias. New manifests and API requests use the explicit name.
BENEFIT_HIERARCHY = CARD_PAGE_SECTION_BENEFIT
CHUNKING_PROFILES = {
    profile.identifier: profile
    for profile in (CARD_PAGE_SECTION_BENEFIT, PARENT_CHILD_BUNDLE)
}


@dataclass(frozen=True)
class SearchConfig:
    profile: str = "card_page_section_benefit"
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
    """Historical Korean n-grams and numeric search aliases; never rewrite evidence."""
    text = normalize_text(text)
    tokens = list(TOKEN_PATTERN.findall(text))
    for run in re.findall(r"[가-힣](?:[가-힣 ]{0,38}[가-힣])?", text):
        joined = run.replace(" ", "")
        for size in (2, 3, 4):
            tokens.extend(f"ko{size}_" + joined[i:i + size] for i in range(len(joined) - size + 1))

    def decimal(value: str | Decimal) -> str:
        return format(Decimal(str(value).replace(",", "")).normalize(), "f")

    consumed = []
    for match in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*만\s*(\d[\d,]*(?:\.\d+)?)\s*천\s*원", text):
        amount = Decimal(match[1].replace(",", "")) * 10000 + Decimal(match[2].replace(",", "")) * 1000
        tokens.append("money_krw_" + decimal(amount))
        consumed.append(match.span())
    for match in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*(만|천)?\s*원", text):
        if not any(start <= match.start() and match.end() <= end for start, end in consumed):
            amount = Decimal(match[1].replace(",", "")) * {"만": 10000, "천": 1000, None: 1}[match[2]]
            tokens.append("money_krw_" + decimal(amount))
    for match in re.finditer(r"(\d[\d,]*(?:\.\d+)?)\s*%", text):
        tokens.append("percent_" + decimal(match[1]))
    for match in re.finditer(r"(?:(월|연|년|일)\s*)?(\d[\d,]*(?:\.\d+)?)\s*(회|개월|년|일)", text):
        tokens.append(f"period_{match[1] or 'none'}_{decimal(match[2])}_{match[3]}")
    return tokens


def lexical_terms(text: str) -> list[str]:
    """One normalized token per opaque ASCII term, independent of backend splitting."""
    return ["t" + token.encode("utf-8").hex() for token in normalized_tokens(text)]


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
    ordered = sorted(scores, key=lambda chunk_id: (
        -scores[chunk_id],
        min(ranks[chunk_id] for ranks in component_ranks.values() if chunk_id in ranks),
        chunk_id,
    ))
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
    normalized = normalize_text(query).rstrip(" ?.!,。？！")
    # Conservative, query-text-only rules; ambiguous recommendations stay semantic.
    proper = r"(?:어느\s*)?(?:카드사|은행|회사)(?:인가|이야)?$|(?:발급사|발행사)(?:는|은|가|인가)?$|어디서\s*(?:발급|발행|출시)"
    numeric = r"몇\s*(?:원|%|퍼센트|마일|마일리지|포인트|회|개월|일|년)(?:인가|야)?$|얼마나\s*(?:할인|적립|차감|청구)|(?:수수료|실적\s*(?:금액|기준)|이용\s*(?:횟수|기간))(?:은|는|이|가|인가)?$"
    if PROPER_QUESTION_PATTERN.search(normalized) or re.search(proper, normalized):
        return "proper_noun"
    if "연회비 면제 조건" not in normalized and (NUMERIC_QUESTION_PATTERN.search(normalized) or re.search(numeric, normalized)):
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
    seen: set[str] = set()
    for row in rows:
        if row.chunk_id in seen:
            continue
        seen.add(row.chunk_id)
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
            raise EvidencePackageTooLarge(
                "selected evidence exceeds the answer payload budget; no partial package returned",
                extra={"chunk_id": chunk.chunk_id, "payload_unit_limit": max_payload_size},
            )
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


def parent_child_bundles(
    rows: Sequence[Candidate], chunks: Mapping[str, Chunk], *, max_chunks: int = 5
) -> list[dict[str, Any]]:
    """Build notebook 26's deterministic same-card 1-hop BGE inputs."""
    if max_chunks < 1:
        raise ValueError("parent-child bundle size must be positive")
    by_card: dict[str, list[Candidate]] = {}
    for row in rows:
        chunk = chunks[row.chunk_id]
        if chunk.level != "structural" or chunk.node_id is None:
            raise ValueError("parent-child candidates must be structural chunks")
        by_card.setdefault(chunk.card_key, []).append(row)

    bundles: list[dict[str, Any]] = []
    ordered_cards = sorted(by_card, key=lambda card_key: (by_card[card_key][0].rank, card_key))
    for card_key in ordered_cards:
        seeds = by_card[card_key]
        seed_row, seed = seeds[0], chunks[seeds[0].chunk_id]
        selected: list[str] = []

        def add(chunk_id: str) -> None:
            candidate = chunks.get(chunk_id)
            if (
                len(selected) < max_chunks
                and chunk_id not in selected
                and candidate is not None
                and candidate.card_key == card_key
                and candidate.level == "structural"
            ):
                selected.append(chunk_id)

        add(seed.chunk_id)
        for chunk_id in seed.related_chunk_ids:
            add(chunk_id)
        for row in seeds[1:]:
            add(row.chunk_id)
        if not selected:
            raise RuntimeError("parent-child bundle has no evidence")

        sections = [f"[카드]\n{seed.issuer} > {seed.card_name}"]
        if seed.optional_parent_heading:
            sections.append(f"[상위 제목]\n{seed.optional_parent_heading}")
        evidence_texts: dict[str, str] = {}
        for index, chunk_id in enumerate(selected, 1):
            chunk = chunks[chunk_id]
            path = " > ".join(chunk.heading_path) or "(root content)"
            section = f"[근거 {index} 경로]\n{path}\n[근거 {index} 본문]\n{chunk.text}"
            evidence_texts[chunk_id] = "\n\n".join([*sections, section]) if index == 1 else section
            sections.append(section)
        text = "\n\n".join(sections)
        bundles.append({
            "card_key": card_key,
            "seed": seed_row,
            "selected_chunk_ids": selected,
            "text": text,
            "evidence_texts": evidence_texts,
            "bundle_sha256": hashlib.sha256(text.encode()).hexdigest(),
        })
    return bundles


def _rank_parent_child_bundles(
    query: str,
    bundles: list[dict[str, Any]],
    reranker: Reranker,
    mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    scores, trace = reranker.score(
        mode, query, [bundle["text"] for bundle in bundles],
        document_token_limit=PARENT_CHILD_DOCUMENT_TOKEN_LIMIT,
    )
    if len(scores) != len(bundles) or not np.isfinite(np.asarray(scores, dtype=np.float64)).all():
        raise RerankerUnavailable("reranker score count or finiteness mismatch")
    ranked = [{**bundle, "score": float(scores[index])} for index, bundle in enumerate(bundles)]
    ranked.sort(key=lambda item: (-item["score"], item["seed"].rank, item["card_key"]))
    return ranked, trace


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
        fused = weighted_rrf(components, weights)[:FUSED_WORKLIST_DEPTH]
        leaf = [row for row in fused if self.chunks[row.chunk_id].level in self.profile.eligible_levels][
            : config.candidate_depth
        ]
        reranker_trace = None
        bundle_trace: list[dict[str, Any]] = []
        answer_chunks = self.chunks
        if self.profile.identifier == "parent_child_bundle":
            if config.reranker != "bge" or config.reranker_route != "all":
                raise ValueError("parent-child profile requires all-query BGE reranking")
            if self.reranker is None:
                raise RerankerUnavailable("reranker is unavailable")
            bundles = parent_child_bundles(leaf, self.chunks)
            ranked_bundles, reranker_trace = _rank_parent_child_bundles(query, bundles, self.reranker, "bge")
            hydrated = []
            reranked = []
            answer_chunks = dict(self.chunks)
            for bundle_rank, bundle in enumerate(ranked_bundles, 1):
                seed = bundle["seed"]
                reranked.append(Candidate(
                    seed.chunk_id,
                    bundle["score"],
                    bundle_rank,
                    seed.component_ranks,
                    seed.rank,
                ))
                bundle_trace.append({
                    "rank": bundle_rank,
                    "card_key": bundle["card_key"],
                    "seed_chunk_id": seed.chunk_id,
                    "selected_chunk_ids": list(bundle["selected_chunk_ids"]),
                    "bundle_sha256": bundle["bundle_sha256"],
                    "score": bundle["score"],
                })
                for chunk_id in bundle["selected_chunk_ids"]:
                    answer_chunks[chunk_id] = replace(self.chunks[chunk_id], text=bundle["evidence_texts"][chunk_id])
                    hydrated.append(Candidate(
                        chunk_id,
                        bundle["score"],
                        len(hydrated) + 1,
                        seed.component_ranks,
                        seed.rank,
                    ))
            should_rerank = True
        else:
            should_rerank = config.reranker != "off" and (
                config.reranker_route == "all" or query_type == "semantic"
            )
            reranked = leaf
            if should_rerank:
                if self.reranker is None:
                    raise RerankerUnavailable("reranker is unavailable")
                scores, reranker_trace = self.reranker.score(
                    config.reranker,
                    query,
                    [self.chunks[row.chunk_id].reranker_text or self.chunks[row.chunk_id].text for row in leaf],
                )
                if len(scores) != len(leaf) or not np.isfinite(np.asarray(scores, dtype=np.float64)).all():
                    raise RerankerUnavailable("reranker score count or finiteness mismatch")
                reranked = [
                    Candidate(**{**row.__dict__, "score": float(scores[index]), "prior_rank": row.rank})
                    for index, row in enumerate(leaf)
                ]
                reranked.sort(key=lambda row: (-row.score, row.prior_rank or row.rank, row.chunk_id))
                reranked = [Candidate(**{**row.__dict__, "rank": rank}) for rank, row in enumerate(reranked, 1)]
            # Preserve the actual ranked section/benefit; do not substitute children.
            hydrated = reranked
        cards, evidence, budget = collapse_cards(
            hydrated, answer_chunks, top_k=config.top_k, standalone_query=query
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
                "evidence_hydration": [_candidate_dict(row) for row in hydrated],
                "bundles": bundle_trace,
                "reranker": reranker_trace,
                "card": cards,
                "evidence_budget": budget,
                "profile": self.profile.identifier,
                "lexical_contract": LEXICAL_CONTRACT,
                "query_classifier_contract": QUERY_CLASSIFIER_CONTRACT,
                "fused_worklist_depth": FUSED_WORKLIST_DEPTH,
                "evidence_policy": "ranked_source_preserved_fail_on_budget_overflow",
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

    def score(self, mode: str, query: str, documents: list[str], *, document_token_limit: int | None = None) -> tuple[list[float], dict[str, Any]]:
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

                # Preflight every input before any model scoring; no silent truncation.
                all_pairs = [[query, document] for document in documents]
                all_lengths = [len(ids) for ids in tokenizer(all_pairs, truncation=False, add_special_tokens=True)["input_ids"]] if all_pairs else []
                if any(length > max_length for length in all_lengths):
                    raise RerankerUnavailable("reranker input exceeds token limit; truncation forbidden")
                if document_token_limit is not None:
                    document_lengths = [len(ids) for ids in tokenizer(documents, truncation=False, add_special_tokens=False)["input_ids"]] if documents else []
                    if any(length > document_token_limit for length in document_lengths):
                        raise RerankerUnavailable("parent-child document exceeds bundle token limit")
                for offset in range(0, len(documents), RERANKER_BATCH_SIZE):
                    pairs = [[query, document] for document in documents[offset : offset + RERANKER_BATCH_SIZE]]
                    token_lengths = all_lengths[offset : offset + RERANKER_BATCH_SIZE]
                    input_token_count += sum(token_lengths)
                    truncated_count += sum(length > max_length for length in token_lengths)
                    encoded = tokenizer(
                        pairs, padding=True, truncation="only_second", max_length=max_length, return_tensors="pt"
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
