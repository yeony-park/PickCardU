from __future__ import annotations

import unittest

from pickcardu_rag import IndexIdentity, SearchConfig, comparison_config_hash, retrieval_metrics


class DiagnosticsTests(unittest.TestCase):
    def test_metrics_and_comparison_hash_contract(self) -> None:
        metrics = retrieval_metrics(
            ["c2", "c2", "c1"],
            [{"card_key": "c1", "chunk_id": "k1", "text": "Air Money 0.2%"}],
            {"card_key": "c1", "required_terms": ["Air Money", "0.2%"]},
            valid_card_keys={"c1", "c2"},
            top_k=3,
        )
        self.assertEqual(metrics["hit_at_3"], 1)
        self.assertEqual(metrics["recall_at_3"], 1.0)
        self.assertEqual(metrics["rr_at_3"], 0.5)
        self.assertEqual(metrics["required_term_evidence_coverage"], 1.0)
        index = IndexIdentity("index-v1", "manifest-hash", "text-embedding-3-small")
        kwargs = {
            "runtime_embedding_model": "text-embedding-3-small",
            "embedding_model_match": True,
            "reranker_artifact": None,
        }
        free_a = comparison_config_hash(SearchConfig(reranker="off"), index, **kwargs)
        free_b = comparison_config_hash(SearchConfig(reranker="off"), index, **kwargs)
        fixed = comparison_config_hash(
            SearchConfig(reranker="off"), index, query_set_hash="set", fixture_hash="fixture", **kwargs
        )
        self.assertEqual(free_a, free_b)
        self.assertNotEqual(free_a, fixed)


if __name__ == "__main__":
    unittest.main()
