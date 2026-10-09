"""Offline endpoint checks without the environment's TestClient thread portal."""
from __future__ import annotations

import sys
from dataclasses import replace
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag import Candidate, Chunk, OpenAIService, RagPipeline
from pickcardu_rag.errors import EvidencePackageTooLarge
from pickcardu_rag_api.main import QueryRequest, create_app
from support import FakeProvider, FakeReranker, settings


class EndpointEvidenceTest(unittest.TestCase):
    def test_custom_api_budget_reaches_search_and_default_provider(self):
        import numpy as np
        text = '가' * 24000
        chunk = Chunk('large', text, 'card', 'Card', 'Issuer', 'benefit', 1)
        searcher = types.SimpleNamespace(search=lambda query, limit: [Candidate('large', 1, 1)])
        pipeline = RagPipeline([chunk], searcher, searcher, FakeReranker())
        handle = types.SimpleNamespace(release_id='fixture', manifest={
            'strategy': 'card_page_section_benefit', 'embedding_model': 'text-embedding-3-small'},
            search=pipeline.search, chunks=(chunk,), catalog=())
        for cap, allowed in ((64000, False), (96000, True)):
            with self.subTest(cap=cap), tempfile.TemporaryDirectory() as directory:
                calls = []
                def parse(**kwargs):
                    calls.append(kwargs)
                    return types.SimpleNamespace(output_parsed={
                        'answer_text': '문서 안내', 'recommendations': [],
                        'claims': [{'text': '문서 안내', 'citations': ['e1']}]}, usage=None)
                def provider_factory(**kwargs):
                    provider = OpenAIService(client=types.SimpleNamespace(responses=types.SimpleNamespace(parse=parse)), **kwargs)
                    provider.embed = lambda query: (np.zeros(2), {'provider_called': False})
                    return provider
                with patch('pickcardu_rag_api.main.OpenAIService', side_effect=provider_factory):
                    app = create_app(replace(settings(Path(directory)), answer_payload_bytes=cap),
                                     index_loader=types.SimpleNamespace(load=lambda: handle), reranker=FakeReranker())
                endpoint = next(route.endpoint for route in app.routes if route.path == '/v1/answer')
                if allowed:
                    answer = endpoint(QueryRequest(query='문서 안내'))
                    self.assertEqual(answer.evidence[0].text, text)
                    self.assertEqual(answer.claims[0].citations, ['large'])
                    self.assertEqual(answer.usage.answer['retrieval']['evidence_budget']['payload_unit_limit'], 96000)
                    self.assertEqual(len(calls), 1)
                else:
                    with self.assertRaises(EvidencePackageTooLarge):
                        endpoint(QueryRequest(query='문서 안내'))
                    self.assertEqual(calls, [])

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
                }, search=search, chunks=(chunk, leaf), catalog=())
                provider = FakeProvider()
                app = create_app(replace(settings(Path(directory)), answer_payload_bytes=12000), provider=provider,
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
