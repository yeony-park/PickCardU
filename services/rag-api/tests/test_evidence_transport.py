"""Offline endpoint checks without the environment's TestClient thread portal."""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag import Candidate, Chunk, RagPipeline
from pickcardu_rag.errors import EvidencePackageTooLarge
from pickcardu_rag_api.main import QueryRequest, create_app
from support import FakeProvider, FakeReranker, settings


class EndpointEvidenceTest(unittest.TestCase):
    def test_answer_receives_the_ranked_section_and_does_not_call_llm_on_overflow(self):
        for overflow in (False, True):
            with self.subTest(overflow=overflow), tempfile.TemporaryDirectory() as directory:
                text = "카페 10% 할인, 전월 실적 30만원 이상" + (" 조건" * 5000 if overflow else "")
                chunk = Chunk("section", text, "card", "Card", "Issuer", "section", 1,
                              child_ids=("leaf",), reranker_text="[문서 경로]\nIssuer > Card > section\n[본문]\n" + text)
                leaf = Chunk("leaf", "카페 10% 할인", "card", "Card", "Issuer", "benefit", 1, parent_id="section")
                lexical = types.SimpleNamespace(search=lambda query, limit: [Candidate("section", 1, 1)])
                vector = types.SimpleNamespace(search=lambda query, limit: [Candidate("section", 1, 1)])
                pipeline = RagPipeline([chunk, leaf], lexical, vector, FakeReranker())
                configs = []

                def search(query, embedding, config):
                    configs.append(config)
                    return pipeline.search(query, embedding, config)

                handle = types.SimpleNamespace(release_id="fixture", manifest={
                    "strategy": "card_page_section_benefit", "embedding_model": "text-embedding-3-small",
                }, search=search)
                provider = FakeProvider()
                app = create_app(settings(Path(directory)), provider=provider,
                                 index_loader=types.SimpleNamespace(load=lambda: handle), reranker=FakeReranker())
                endpoint = next(route.endpoint for route in app.routes if route.path == "/v1/answer")
                if overflow:
                    with self.assertRaises(EvidencePackageTooLarge):
                        endpoint(QueryRequest(query="카페 혜택 추천"))
                    self.assertEqual(provider.answer_inputs, [])
                else:
                    result = endpoint(QueryRequest(query="카페 혜택 추천", top_k=5))
                    self.assertEqual(result.evidence[0].text, text)
                    self.assertEqual(provider.answer_inputs[0][1][0]["chunk_id"], "section")
                    self.assertEqual(configs[0].candidate_depth, 20)
                    self.assertEqual(configs[0].component_depth, 50)
                    self.assertEqual(configs[0].top_k, 5)


if __name__ == "__main__":
    unittest.main()
