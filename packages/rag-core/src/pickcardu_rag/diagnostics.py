"""Development retrieval metrics without service or storage dependencies."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from .retrieval import GTE_REVISION, SearchConfig, normalize_text


LAB_LABEL = "development retrieval diagnostics; not a product success criterion."
METRIC_VERSION = "card_search_metrics_v1"


@dataclass(frozen=True)
class IndexIdentity:
    index_id: str
    manifest_hash: str
    embedding_model: str


def comparison_config(
    config: SearchConfig,
    index: IndexIdentity,
    *,
    runtime_embedding_model: str,
    embedding_model_match: bool,
    reranker_artifact: dict[str, Any] | None,
    query_set_hash: str | None = None,
    fixture_hash: str | None = None,
) -> dict[str, Any]:
    return {
        "requested": asdict(config),
        "index_id": index.index_id,
        "index_manifest_hash": index.manifest_hash,
        "query_set_hash": query_set_hash,
        "fixture_hash": fixture_hash,
        "index_embedding_model": index.embedding_model,
        "runtime_embedding_model": runtime_embedding_model,
        "embedding_model_match": embedding_model_match,
        "distance": "squared_l2",
        "filter": "profile_eligible_levels",
        "component_depth": config.component_depth,
        "candidate_depth": config.candidate_depth,
        "card_aggregation": "first_rank_unique_card_max5_evidence",
        "reranker": config.reranker,
        "reranker_route": config.reranker_route,
        "reranker_revision": GTE_REVISION if config.reranker == "gte" else None,
        "reranker_revision_claim_unverified": config.reranker == "gte",
        "reranker_artifact": reranker_artifact,
        "top_k": config.top_k,
        "tie": "score_then_prior_rank_then_chunk_id",
        "dedup": "unordered_card_key",
        "metric_version": METRIC_VERSION,
        "profile": config.profile,
        "tokenizer": config.tokenizer,
        "vector_weight": config.vector_weight,
        "mmr_lambda": config.mmr_lambda,
    }


def comparison_config_hash(*args: Any, **kwargs: Any) -> str:
    serialized = json.dumps(comparison_config(*args, **kwargs), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def retrieval_metrics(
    predicted_card_keys: list[str],
    evidence: list[dict[str, Any]],
    gold: dict[str, Any] | None,
    *,
    valid_card_keys: set[str],
    top_k: int,
) -> dict[str, Any]:
    if gold is None:
        return {"status": "N/A", "label": LAB_LABEL}
    if any(card not in valid_card_keys for card in predicted_card_keys):
        raise ValueError("invalid predicted card")
    if any(item.get("card_key") not in valid_card_keys or not item.get("chunk_id") for item in evidence):
        raise ValueError("invalid evidence")
    deduplicated = list(dict.fromkeys(predicted_card_keys))[:top_k]
    gold_cards = gold.get("card_keys") or [gold.get("card_key")]
    if not gold_cards or any(card not in valid_card_keys for card in gold_cards):
        raise ValueError("invalid gold card")
    matched = set(deduplicated) & set(gold_cards)
    terms = gold.get("required_terms", [])
    relevant_text = normalize_text(" ".join(item["text"] for item in evidence if item["card_key"] in gold_cards))
    term_hits = [normalize_text(term) in relevant_text for term in terms]
    return {
        "status": "diagnostic",
        "label": LAB_LABEL,
        f"hit_at_{top_k}": int(bool(matched)),
        f"recall_at_{top_k}": len(matched) / len(set(gold_cards)),
        f"rr_at_{top_k}": next(
            (1.0 / rank for rank, card in enumerate(deduplicated, 1) if card in gold_cards), 0.0
        ),
        "required_term_evidence_coverage": sum(term_hits) / len(term_hits) if term_hits else None,
        "required_term_hits": term_hits,
        "singleton_hit_equals_recall": len(set(gold_cards)) == 1,
        "claim_citation_metrics": "diagnostic_only",
    }

