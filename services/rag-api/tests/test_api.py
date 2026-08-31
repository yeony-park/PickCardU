from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag_api.index import ActiveIndexLoader  # noqa: E402
from pickcardu_rag_api.main import create_app  # noqa: E402
from support import FakeProvider, FakeReranker, build_release, settings  # noqa: E402


class EmptyHandle:
    release_id = "empty"
    manifest = {
        "strategy": "card_page_section_benefit",
        "document_ids": [],
        "chunk_ids": [],
        "embedding_model": "text-embedding-3-small",
    }

    def search(self, _query, _embedding, _config):
        return {"query_type": "semantic", "cards": [], "evidence": [], "trace": {}}


class EmptyLoader:
    def load(self):
        return EmptyHandle()


class BrokenLoader:
    def load(self):
        raise RuntimeError("local index detail must not be exposed")


class InsufficientProvider:
    embedding_model = "text-embedding-3-small"
    llm_model = "gpt-5.6-luna"

    def __init__(self) -> None:
        self.answer_inputs = []

    def embed(self, _query):
        import numpy as np

        return np.asarray([0.0, 0.0], dtype=np.float32), {"provider_called": True}

    def answer(self, query, evidence):
        from pickcardu_rag import AnswerOutput

        self.answer_inputs.append((query, evidence))
        return AnswerOutput(
            answer_status="insufficient_evidence",
            answer_text="현재 근거만으로는 답하기 어렵습니다.",
        ), {"provider_called": True}


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        build_release(self.root / "runtime")
        self.provider = FakeProvider()
        self.reranker = FakeReranker()
        loader = ActiveIndexLoader(self.root / "runtime", reranker=self.reranker)
        self.client = TestClient(create_app(settings(self.root), provider=self.provider, index_loader=loader, reranker=self.reranker))

    def tearDown(self) -> None:
        self.client.close()
        for path in self.root.rglob("*"):
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    def test_only_pipeline_endpoints_exist(self) -> None:
        paths = set(self.client.get("/openapi.json").json()["paths"])
        self.assertTrue({"/v1/health/live", "/v1/health/ready", "/v1/search", "/v1/answer"} <= paths)
        self.assertFalse(any("auth" in path or "profile" in path or "conversation" in path or "lab" in path for path in paths))
        self.assertEqual(self.client.get("/v1/health/live").json(), {"status": "live"})
        self.assertEqual(self.client.get("/v1/health/ready").json()["status"], "ready")

    def test_search_runs_hybrid_rrf_and_selective_bge(self) -> None:
        response = self.client.post("/v1/search", json={"query": "카페 혜택 좋은 카드", "top_k": 3})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["profile"], "card_page_section_benefit")
        self.assertEqual(body["cards"][0]["card_key"], "issuer/card-a")
        self.assertEqual(body["query_type"], "semantic")
        self.assertEqual(self.provider.embedding_queries, ["카페 혜택 좋은 카드"])

    def test_answer_uses_server_retrieval_evidence(self) -> None:
        response = self.client.post("/v1/answer", json={"query": "카페 혜택 좋은 카드"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["answer_status"], "answered")
        self.assertEqual(body["recommendations"][0]["card_key"], "issuer/card-a")
        query, evidence = self.provider.answer_inputs[0]
        self.assertEqual(query, "카페 혜택 좋은 카드")
        self.assertTrue(evidence)

    def test_empty_retrieval_abstains_without_llm(self) -> None:
        client = TestClient(create_app(settings(self.root), provider=self.provider, index_loader=EmptyLoader(), reranker=self.reranker))
        try:
            response = client.post("/v1/answer", json={"query": "등록 문서에 없는 혜택"})
        finally:
            client.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer_status"], "insufficient_evidence")
        self.assertEqual(response.json()["recommendations"], [])
        self.assertEqual(self.provider.answer_inputs, [])

    def test_llm_abstention_does_not_expose_search_results_as_recommendations(self) -> None:
        provider = InsufficientProvider()
        loader = ActiveIndexLoader(self.root / "runtime", reranker=self.reranker)
        client = TestClient(
            create_app(
                settings(self.root),
                provider=provider,
                index_loader=loader,
                reranker=self.reranker,
            )
        )
        try:
            response = client.post("/v1/answer", json={"query": "근거가 불충분한 질문"})
        finally:
            client.close()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer_status"], "insufficient_evidence")
        self.assertEqual(response.json()["cards"], [])
        self.assertEqual(response.json()["evidence"], [])
        self.assertTrue(provider.answer_inputs[0][1])

    def test_profile_mismatch_and_invalid_query_fail_closed(self) -> None:
        mismatch = self.client.post("/v1/search", json={"query": "카페", "profile": "parent_child_bundle"})
        self.assertEqual(mismatch.status_code, 409)
        blank = self.client.post("/v1/search", json={"query": "   "})
        self.assertEqual(blank.status_code, 422)

    def test_unavailable_index_returns_sanitized_retryable_error(self) -> None:
        client = TestClient(
            create_app(
                settings(self.root),
                provider=self.provider,
                index_loader=BrokenLoader(),
                reranker=self.reranker,
            )
        )
        try:
            response = client.post("/v1/search", json={"query": "카페"})
        finally:
            client.close()
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["code"], "INDEX_UNAVAILABLE")
        self.assertTrue(body["retryable"])
        self.assertNotIn("local index detail", body["message"])

    def test_embedding_model_mismatch_fails_before_provider_call(self) -> None:
        self.provider.embedding_model = "different-embedding-model"
        response = self.client.post("/v1/search", json={"query": "카페"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.provider.embedding_queries, [])


if __name__ == "__main__":
    unittest.main()
