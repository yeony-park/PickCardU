from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

import numpy as np
import torch

from pickcardu_rag import (
    BM25,
    Candidate,
    Chunk,
    ChunkingProfile,
    InMemoryBM25Searcher,
    InMemorySquaredL2Searcher,
    LocalReranker,
    PARENT_CHILD_BUNDLE,
    RagPipeline,
    SearchConfig,
    classify_query,
    collapse_cards,
    normalize_text,
    normalized_tokens,
    squared_l2_rank,
    weighted_rrf,
)
from pickcardu_rag.errors import EvidencePackageTooLarge, RerankerUnavailable
from pickcardu_rag.retrieval import _scores_from_logits, fingerprint_local_artifact, lexical_terms


class RecordingLexical:
    def __init__(self, rows: list[Candidate]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, *, limit: int) -> list[Candidate]:
        self.calls.append((query, limit))
        return self.rows[:limit]


class RecordingVector:
    embedding_model = "text-embedding-3-small"

    def __init__(self, rows: list[Candidate]) -> None:
        self.rows = rows
        self.calls: list[int] = []

    def search(self, query_embedding: np.ndarray, *, limit: int) -> list[Candidate]:
        self.calls.append(limit)
        return self.rows[:limit]

class FixedReranker:
    def __init__(self) -> None:
        self.calls = 0
        self.documents: list[list[str]] = []

    def score(self, mode: str, query: str, documents: list[str], *, document_token_limit=None):
        self.calls += 1
        self.documents.append(list(documents))
        return list(reversed(range(len(documents)))), {"mode": mode}


class FakeTokenizer:
    model_max_length = 8

    def __call__(self, pairs, **kwargs):
        lengths = [len(pair[1]) + 2 if isinstance(pair, list) else len(pair) for pair in pairs]
        if not kwargs.get("return_tensors"):
            return {"input_ids": [list(range(length)) for length in lengths]}
        return {"input_ids": torch.ones((len(pairs), min(max(lengths), kwargs["max_length"])), dtype=torch.long)}


class FakeModel:
    config = types.SimpleNamespace(max_position_embeddings=4)

    def __init__(self) -> None:
        self.batch_sizes: list[int] = []

    def __call__(self, **encoded):
        size = encoded["input_ids"].shape[0]
        self.batch_sizes.append(size)
        return types.SimpleNamespace(logits=torch.arange(size, dtype=torch.float32))


class RetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.chunks = [
            Chunk("a", "카페 할인", "c1", "카드1", "발급사", "page", 0, "표지"),
            Chunk("b", "카페 10% 할인", "c1", "카드1", "발급사", "benefit", 1, "카페"),
            Chunk(
                "c",
                "항공 마일리지",
                "c2",
                "카드2",
                "발급사",
                "section",
                2,
                "항공",
                child_ids=("e",),
            ),
            Chunk("d", "OTT 구독 할인", "c3", "카드3", "발급사", "benefit", 3, "OTT"),
            Chunk(
                "e",
                "항공 1마일 적립",
                "c2",
                "카드2",
                "발급사",
                "benefit",
                2,
                "항공",
                parent_id="c",
            ),
        ]
        self.embeddings = np.asarray(
            [[1.0, 0.0], [1.0, 0.0], [0.0, 1.0], [0.7, 0.7], [0.0, 1.0]],
            dtype=np.float32,
        )

    def test_normalization_bm25_l2_rrf_and_classifier_parity(self) -> None:
        self.assertEqual(normalize_text("  ＡBC\t카드  "), "abc 카드")
        self.assertIn("percent_10", normalized_tokens("할인 10%"))
        bm25 = BM25(["카페 할인", "항공 마일리지"])
        self.assertGreater(bm25.scores(["카페"])[0], bm25.scores(["카페"])[1])
        ranked = squared_l2_rank(np.zeros(2, dtype=np.float32), self.embeddings[:2], ["a", "b"])
        self.assertEqual(ranked[0].chunk_id, "a")
        fused = weighted_rrf(
            {"lexical": [Candidate("a", 1, 1)], "vector": [Candidate("b", 0, 1)]},
            {"lexical": 0.6, "vector": 0.4},
        )
        self.assertEqual(fused[0].chunk_id, "a")
        self.assertEqual(classify_query("연회비는 얼마인가?"), "numeric_condition")
        self.assertEqual(classify_query("biz AirMoney는 어느 카드사 상품인가?"), "proper_noun")
        self.assertEqual(classify_query("OTT 혜택 좋은 카드"), "semantic")
        with self.assertRaises(ValueError):
            SearchConfig(vector_weight=1.1)
        for kwargs in (
            {"reranker": "other"},
            {"reranker_route": "other"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SearchConfig(**kwargs)

    def test_chunk_input_boundary(self) -> None:
        fields = ["a", "text", "card", "name", "issuer", "benefit", 0]
        for index in range(6):
            invalid = list(fields)
            invalid[index] = " "
            with self.subTest(field=index), self.assertRaises(ValueError):
                Chunk(*invalid)
        for page_num in (True, -1, 1.5, "1"):
            with self.subTest(page_num=page_num), self.assertRaises(ValueError):
                Chunk(*fields[:6], page_num)

    def test_adapter_boundary_pipeline_rerank_collapse_and_profile(self) -> None:
        lexical = RecordingLexical([Candidate("a", 4, 1), Candidate("b", 3, 2), Candidate("c", 2, 3)])
        vector = RecordingVector([Candidate("c", 0, 1), Candidate("b", 1, 2)])
        reranker = FixedReranker()
        pipeline = RagPipeline(self.chunks, lexical, vector, reranker)
        result = pipeline.search(
            "카페 혜택 좋은 카드", np.zeros(2, dtype=np.float32), SearchConfig(reranker="bge", top_k=2)
        )
        self.assertEqual(lexical.calls, [("카페 혜택 좋은 카드", 50)])
        self.assertEqual(vector.calls, [50])
        self.assertEqual(reranker.calls, 1)
        self.assertNotIn("a", [item["chunk_id"] for item in result["evidence"]])
        self.assertIn("c", [item["chunk_id"] for item in result["evidence"]])
        self.assertNotIn("e", [item["chunk_id"] for item in result["evidence"]])
        self.assertEqual(next(item["text"] for item in result["evidence"] if item["chunk_id"] == "c"), self.chunks[2].text)
        self.assertTrue(all(isinstance(item["page_num"], int) for item in result["evidence"]))
        self.assertEqual(result["trace"]["profile"], "card_page_section_benefit")
        with self.assertRaises(ValueError):
            pipeline.search("q", np.zeros(2), SearchConfig(profile="other", reranker="off"))

        numeric = pipeline.search(
            "연회비는 얼마인가?", np.zeros(2, dtype=np.float32), SearchConfig(reranker="bge")
        )
        self.assertEqual(numeric["query_type"], "numeric_condition")
        self.assertEqual(reranker.calls, 1)

        custom = RagPipeline(
            self.chunks, lexical, vector,
            profile=ChunkingProfile("replacement", frozenset({"page", "benefit", "section"})),
        )
        self.assertEqual(
            custom.search("q", np.zeros(2), SearchConfig(profile="replacement", reranker="off"))["trace"]["profile"],
            "replacement",
        )

    def test_in_memory_adapters_and_evidence_budget(self) -> None:
        lexical = InMemoryBM25Searcher(self.chunks)
        vector = InMemorySquaredL2Searcher(
            [chunk.chunk_id for chunk in self.chunks], self.embeddings, embedding_model="text-embedding-3-small"
        )
        pipeline = RagPipeline(self.chunks, lexical, vector)
        first = pipeline.search("카페 할인", np.asarray([1.0, 0.0]), SearchConfig(reranker="off"))
        second = pipeline.search("카페 할인", np.asarray([1.0, 0.0]), SearchConfig(reranker="off"))
        self.assertEqual(first["cards"], second["cards"])
        self.assertEqual(first["evidence"], second["evidence"])
        rows = [Candidate("b", 2, 1), Candidate("c", 1, 2)]
        cards, evidence, budget = collapse_cards(rows, {chunk.chunk_id: chunk for chunk in self.chunks}, top_k=2)
        self.assertEqual([card["card_key"] for card in cards], ["c1", "c2"])
        self.assertEqual([item["page_num"] for item in evidence], [1, 2])
        self.assertLessEqual(budget["payload_size"], budget["payload_unit_limit"])

    def test_parent_child_bundles_are_built_before_all_query_bge(self) -> None:
        chunks = [
            Chunk("a", "생활 > 통신\n통신비 10% 할인", "c1", "카드1", "발급사", "structural", 1,
                  node_id="c1::n1", heading_path=("생활", "통신"), related_chunk_ids=("b",), optional_parent_heading="생활"),
            Chunk("b", "생활 > 통신 조건\n전월 실적 40만원", "c1", "카드1", "발급사", "structural", 2,
                  node_id="c1::n2", heading_path=("생활", "통신 조건")),
            Chunk("c", "생활 > 카페\n카페 할인", "c2", "카드2", "발급사", "structural", 1,
                  node_id="c2::n1", heading_path=("생활", "카페")),
        ]
        lexical = RecordingLexical([Candidate("a", 3, 1), Candidate("c", 2, 2)])
        reranker = FixedReranker()
        pipeline = RagPipeline(chunks, lexical, reranker=reranker, profile=PARENT_CHILD_BUNDLE)
        result = pipeline.search(
            "할인율은 얼마야?",
            config=SearchConfig(
                profile="parent_child_bundle",
                vector_weight=0,
                reranker="bge",
                reranker_route="all",
                top_k=2,
            ),
        )
        self.assertEqual(reranker.calls, 1)
        self.assertIn("통신비 10% 할인", reranker.documents[0][0])
        self.assertIn("전월 실적 40만원", reranker.documents[0][0])
        self.assertEqual(result["query_type"], "numeric_condition")
        self.assertTrue(result["trace"]["bundles"])
        self.assertIn("b", [row["chunk_id"] for row in result["evidence"]])
        from pickcardu_rag.answering import build_answer_payload
        answer_evidence = build_answer_payload("질문", result["evidence"])["evidence"]
        for card_key, document in zip(("c1", "c2"), reranker.documents[0]):
            self.assertEqual("\n\n".join(row["text"] for row in answer_evidence if row["card_key"] == card_key), document)
        self.assertEqual(pipeline.chunks["a"].text, chunks[0].text)
        with self.assertRaisesRegex(ValueError, "all-query BGE"):
            pipeline.search(
                "질문",
                config=SearchConfig(profile="parent_child_bundle", vector_weight=0, reranker="bge", reranker_route="selective"),
            )

    def test_artifact_fingerprint_is_content_bound_and_rejects_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            first = fingerprint_local_artifact(root)
            (root / "config.json").write_text('{"changed":true}', encoding="utf-8")
            self.assertNotEqual(first, fingerprint_local_artifact(root))
            (root / "linked").symlink_to(root / "config.json")
            with self.assertRaises(RerankerUnavailable):
                fingerprint_local_artifact(root)

    def test_reranker_batch_truncation_and_logits_contract_without_model_load(self) -> None:
        self.assertEqual(_scores_from_logits(torch.tensor([[1.0], [2.0]]), 2), [1.0, 2.0])
        for invalid in (torch.ones((2, 2)), torch.tensor([1.0]), torch.tensor([1.0, float("nan")])):
            with self.assertRaises(RerankerUnavailable):
                _scores_from_logits(invalid, 2)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text("{}", encoding="utf-8")
            reranker = LocalReranker(str(root))
            contract = reranker.artifact_contract("bge")
            key = ("bge", str(root), contract["artifact_fingerprint"])
            model = FakeModel()
            LocalReranker._models[key] = (FakeTokenizer(), model, "cpu", "float32", 4)
            try:
                with self.assertRaisesRegex(RerankerUnavailable, "truncation forbidden"):
                    reranker.score("bge", "q", ["a", "bb", "ccc"])
                self.assertEqual(model.batch_sizes, [])
                scores, trace = reranker.score("bge", "q", ["a", "bb", "a", "bb", "a"])
                with self.assertRaisesRegex(RerankerUnavailable, "bundle token limit"):
                    reranker.score("bge", "q", ["bb"], document_token_limit=1)
            finally:
                LocalReranker._models.pop(key, None)
        self.assertEqual(scores, [0.0, 1.0, 0.0, 1.0, 0.0])
        self.assertEqual(model.batch_sizes, [2, 2, 1])
        self.assertEqual(trace["batch_count"], 3)
        self.assertEqual(trace["truncated_count"], 0)

    def test_search_aliases_and_conservative_query_rules(self) -> None:
        for text in ("30만원", "300,000원", "300000원"):
            self.assertIn("money_krw_300000", normalized_tokens(text))
        self.assertIn("money_krw_12000", normalized_tokens("1만 2천원"))
        self.assertIn("percent_1.5", normalized_tokens("1.50%"))
        self.assertIn("period_월_2_회", normalized_tokens("월 2회"))
        self.assertIn("ko4_전월실적", normalized_tokens("전월 실적"))
        self.assertTrue(all(term.isalnum() and term.isascii() for term in lexical_terms("30만원 할인 1.5%")))
        cases = {
            "이 카드의 이용 기간?": "numeric_condition",
            "포인트 몇 포인트?": "numeric_condition",
            "얼마나 적립해?": "numeric_condition",
            "IBK포인트3.8 어느 은행?": "proper_noun",
            "카드 발행사는?": "proper_noun",
            "통신비와 편의점 혜택을 모두 주는 카드 추천": "semantic",
            "연회비 면제 조건": "semantic",
            "30만원 쓰는데 어떤 카드가 좋아?": "semantic",
        }
        for query, expected in cases.items():
            with self.subTest(query=query):
                self.assertEqual(classify_query(query), expected)

    def test_fused_top50_precedes_eligible_filter(self) -> None:
        chunks = [Chunk(str(i), "표지", str(i), "카드", "발급사", "card", 1) for i in range(50)]
        chunks.append(Chunk("last", "편의점 할인", "last", "카드", "발급사", "benefit", 1))
        shared = [Candidate(str(i), 0, i + 1) for i in range(49)]
        lexical = RecordingLexical([*shared, Candidate("49", 0, 50)])
        vector = RecordingVector([*shared, Candidate("last", 0, 50)])
        result = RagPipeline(chunks, lexical, vector).search("q", np.zeros(2), SearchConfig(reranker="off"))
        self.assertEqual(result["evidence"], [])

    def test_rrf_ties_use_best_component_rank_before_id(self) -> None:
        result = weighted_rrf({"a": [Candidate("z", 0, 1)], "b": [Candidate("a", 0, 62)]}, {"a": 1, "b": 2})
        self.assertEqual([row.chunk_id for row in result], ["z", "a"])

    def test_evidence_budget_failure_does_not_substitute_or_partially_emit(self) -> None:
        chunks = {chunk.chunk_id: chunk for chunk in self.chunks}
        with self.assertRaises(EvidencePackageTooLarge):
            collapse_cards([Candidate("b", 2, 1), Candidate("c", 1, 2)], chunks, top_k=2, max_payload_size=1)
        cards, evidence, _ = collapse_cards([Candidate("b", 2, 1), Candidate("b", 1, 2)], chunks, top_k=3)
        self.assertEqual((len(cards), len(evidence)), (1, 1))

    def test_runtime_source_has_no_service_or_legacy_path_dependencies(self) -> None:
        source_root = Path(__file__).parents[1] / "src" / "pickcardu_rag"
        source = "\n".join(path.read_text(encoding="utf-8").casefold() for path in source_root.glob("*.py"))
        for forbidden in ("fastapi", "sqlite", "cookie", "notebooks/", "chroma", "data/search", "scripts/rag_pipeline"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
