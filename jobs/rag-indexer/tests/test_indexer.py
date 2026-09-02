from __future__ import annotations

import gc
import json
import hashlib
import os
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest import mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [
    str(PACKAGE_ROOT),
    str(PROJECT_ROOT / "services/rag-api/src"),
    str(PROJECT_ROOT / "packages/rag-core/src"),
]

from pickcardu_indexer.pipeline import (  # noqa: E402
    Indexer,
    OpenAIEmbeddingAdapter,
    compare_ocr_outputs,
    normalise_fact,
    strict_resolution,
    validate_lane,
)
from pickcardu_indexer.__main__ import parser as cli_parser, run_ocr  # noqa: E402
from pickcardu_indexer.ocr import STRUCTURE_PROMPT, LiveLaneAdapter, LunaFactStructurer, LunaOcrTranscriber, OcrProviderError, pages_text, upstage_pages  # noqa: E402
from pickcardu_indexer.structural import build_structural_chunks  # noqa: E402
from pickcardu_rag_api.config import Settings  # noqa: E402
from pickcardu_rag_api.index import ActiveIndexLoader  # noqa: E402
from pickcardu_rag_api.main import QueryRequest, create_app  # noqa: E402


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


SOURCE_SHA = "c5c2e8be6ad0825a56ded1f1a153ceaf11dc060ab44b120a94406a08207babc2"


def lane(document_id: str, provider: str, *, condition: str = "monthly", value: str = "1", quote: str | None = None) -> dict[str, object]:
    quote = quote or f"카페 {condition} {value}% 할인"
    return {
        "document_id": document_id,
        "provider": provider,
        "source_pdf_sha256": SOURCE_SHA,
        "provenance": {"endpoint": "local-fixture", "model": f"{provider}-fixture", "config_hash": "fixture-v1"},
        "identity": {"issuer_name": "Issuer", "card_name": "Card", "issuer_evidence": {"page": 1, "quote": "Issuer"}, "card_evidence": {"page": 1, "quote": "Card"}},
        "pages": [{"page": 1, "text": f"Issuer Card\n### 상품 안내\n상품 안내: {quote}"}],
        "span_dispositions": [{"page": 1, "quote": "Issuer Card", "kind": "identity"}, {"page": 1, "quote": "### 상품 안내", "kind": "ignore", "reason": "heading"}, {"page": 1, "quote": f"상품 안내: {quote}", "kind": "fact"}],
        "facts": [{"target": "카페", "condition": condition, "value": value, "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": "", "evidence": {"page": 1, "quote": quote}}],
    }


def resolution_payload() -> dict[str, object]:
    return {
        "resolution": {"selected_provider": "luna", "selected_identity_provider": "luna", "reason": "verified", "rejected_relations": []},
        "identity": {
            "issuer_name": "Issuer",
            "card_name": "Card",
            "evidence_refs": {
                provider: {
                    "issuer": {"provider": provider, "page": 1, "quote": "Issuer"},
                    "card": {"provider": provider, "page": 1, "quote": "Card"},
                    "supports_selected": True,
                }
                for provider in ("luna", "upstage")
            },
        },
        "canonical": [{"fact": {"target": "카페", "condition": "monthly", "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}, "evidence_refs": {"luna": {"provider": "luna", "page": 1, "quote": "카페 monthly 1% 할인", "supports_selected": True}, "upstage": {"provider": "upstage", "page": 1, "quote": "카페 monthly 1% 할인", "supports_selected": True}}}],
    }


def add_relation(payload: dict[str, object], *, condition: str, quote: str) -> None:
    payload["pages"][0]["text"] += f"\n{quote}"
    payload["span_dispositions"].append({"page": 1, "quote": quote, "kind": "fact"})
    payload["facts"].append({"target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": "", "evidence": {"page": 1, "quote": quote}})


def relation_item(condition: str, luna_quote: str, upstage_quote: str, *, luna_supports: bool, upstage_supports: bool) -> dict[str, object]:
    return {
        "fact": {"target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""},
        "evidence_refs": {
            "luna": {"provider": "luna", "page": 1, "quote": luna_quote, "supports_selected": luna_supports},
            "upstage": {"provider": "upstage", "page": 1, "quote": upstage_quote, "supports_selected": upstage_supports},
        },
    }


class DeterministicEmbeddingAdapter:
    model = "text-embedding-3-small"
    dimension = 16

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> tuple[np.ndarray, dict[str, object]]:
        self.calls.append(list(texts))
        return (
            np.asarray([Indexer._fake_embedding(text, self.dimension) for text in texts], dtype=np.float32),
            {"provider_called": False, "item_count": len(texts)},
        )


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.embeddings = self
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        rows = [
            types.SimpleNamespace(index=index, embedding=[float(index + 1)] * 1536)
            for index, _text in enumerate(kwargs["input"])
        ]
        usage = types.SimpleNamespace(model_dump=lambda: {"total_tokens": len(rows)})
        return types.SimpleNamespace(data=list(reversed(rows)), usage=usage)


class FakeResponsesClient:
    def __init__(self, outputs: list[dict[str, object]]) -> None:
        self.responses, self.outputs, self.calls = self, list(outputs), []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        raw = {
            "id": f"response-{len(self.calls)}",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(output, ensure_ascii=False)}]}],
        }
        return types.SimpleNamespace(output_text=json.dumps(output, ensure_ascii=False), model_dump=lambda mode="json": raw)


class QueryEmbeddingProvider:
    embedding_model = "text-embedding-3-small"
    llm_model = "gpt-5.6-luna"

    def embed(self, query: str):
        return Indexer._fake_embedding(query, 16), {"provider_called": False}


class RecordingReranker:
    def __init__(self) -> None:
        self.calls = 0

    def score(self, _mode: str, _query: str, _documents: list[str]):
        self.calls += 1
        return [float(len(_documents) - index) for index in range(len(_documents))], {"fixture": True}


class FakeTranscriber:
    def __init__(self, provider: str, *, extra_line: str = "") -> None:
        self.provider, self.extra_line, self.calls = provider, extra_line, 0
        self.config = {"endpoint": "fake", "model": f"{provider}-ocr"}

    def request(self, _source: Path):
        self.calls += 1
        text = "Issuer Card\n상품 안내: 카페 monthly 1% 할인"
        if self.extra_line:
            text += f"\n{self.extra_line}"
        return {"provider": self.provider, "request": self.calls, "pages": [{"page": 1, "text": text, "uncertain_spans": []}]}, 1

    def parse(self, raw: dict[str, object], _expected_count: int):
        return raw["pages"]


class FakeStructurer:
    def __init__(self, model: str = "shared-luna-structurer") -> None:
        self.config = {"endpoint": "fake", "model": model}
        self.calls: list[str] = []

    def request(self, provider: str, pages: list[dict[str, object]]):
        self.calls.append(provider)
        dispositions = [
            {"page": 1, "quote": "Issuer Card", "kind": "identity", "reason": "identity"},
            {"page": 1, "quote": "상품 안내: 카페 monthly 1% 할인", "kind": "fact", "reason": "benefit"},
        ]
        if "추가 문구" in str(pages[0]["text"]):
            dispositions.append({"page": 1, "quote": "추가 문구", "kind": "ignore", "reason": "non-benefit text"})
        structured = {
            "identity": {"issuer_name": "Issuer", "card_name": "Card", "issuer_evidence": {"page": 1, "quote": "Issuer"}, "card_evidence": {"page": 1, "quote": "Card"}},
            "facts": [{"target": "카페", "condition": "monthly", "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": "", "evidence": {"page": 1, "quote": "카페 monthly 1% 할인"}}],
            "span_dispositions": dispositions,
        }
        return {"provider": provider, "structured": structured}

    def parse(self, raw: dict[str, object]):
        return raw["structured"]

    def structure(self, provider: str, pages: list[dict[str, object]]):
        raw = self.request(provider, pages)
        return raw, self.parse(raw)


class IndexerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.pdf"
        self.source.write_bytes(b"%PDF-1.4 fixture")
        self.document_id = "issuer/card"
        self.manifest = self.root / "source-manifest.json"
        write_json(self.manifest, {"documents": [{"document_id": self.document_id, "source_pdf": "source.pdf"}]})
        self.luna_dir, self.upstage_dir = self.root / "luna", self.root / "upstage"
        write_json(self.luna_dir / "issuer__card.json", lane(self.document_id, "luna"))
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage"))
        self.indexer = Indexer(self.root / "runtime")

    def tearDown(self) -> None:
        self.indexer.close()
        for path in sorted((self.root / "runtime").rglob("*"), reverse=True):
            if path.is_dir():
                path.chmod(0o755)
            else:
                path.chmod(0o644)
        self.temporary.cleanup()

    def execute_indexer(self, *, partial: bool = False) -> dict[str, object]:
        return self.indexer.run(
            self.manifest,
            self.luna_dir,
            self.upstage_dir,
            fake_vectors=True,
            allow_partial=partial,
            config={"profile": "card_page_section_benefit", "fake_vectors": True, "allow_partial": partial},
        )

    def open_relation_review(self) -> tuple[int, Path]:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "resolution.json"
        payload = resolution_payload()
        payload["canonical"][0]["evidence_refs"]["upstage"] = {"provider": "upstage", "page": 1, "quote": "카페 monthly daily 1% 할인", "supports_selected": False}
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily differs from selected monthly relation"}]
        write_json(after, payload)
        return review_id, after

    def test_dual_lane_release_resume_and_pointer(self) -> None:
        result = self.execute_indexer()
        release_id = result["release_id"]
        self.assertIsInstance(release_id, str)
        self.assertEqual(result["status"]["run"]["status"], "test_only_published")
        release = self.root / "runtime/index-release" / str(release_id)
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["document_ids"], [self.document_id])
        self.assertEqual(manifest["coverage"], {"included_document_ids": [self.document_id], "omitted_document_ids": [], "partial": False})
        connection = sqlite3.connect(release / "corpus.sqlite")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 4)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0], 4)
        connection.close()
        with self.assertRaisesRegex(RuntimeError, "production"):
            self.indexer.activate(str(release_id))
        resumed = self.execute_indexer()
        self.assertEqual(resumed["run_id"], result["run_id"])
        self.assertEqual(resumed["release_id"], release_id)

    def test_both_production_profiles_activate_and_reach_search_handler(self) -> None:
        runtime = self.root / "runtime"
        for profile in ("card_page_section_benefit", "parent_child_bundle"):
            with self.subTest(profile=profile):
                adapter = DeterministicEmbeddingAdapter()
                result = self.indexer.run(
                    self.manifest,
                    self.luna_dir,
                    self.upstage_dir,
                    fake_vectors=False,
                    allow_partial=False,
                    config={
                        "profile": profile,
                        "fake_vectors": False,
                        "allow_partial": False,
                        "embedding_model": adapter.model,
                        "embedding_dimension": adapter.dimension,
                    },
                    embedding_adapter=adapter,
                )
                release = runtime / "index-release" / str(result["release_id"])
                manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["release_status"], "production")
                self.assertEqual(manifest["embedding_model"], adapter.model)
                self.assertEqual(len(adapter.calls), 1)
                self.indexer.activate(str(result["release_id"]))
                bge_path = self.root / "unused-bge"
                bge_path.mkdir(exist_ok=True)
                settings = Settings(
                    "test",
                    runtime,
                    ("http://testserver",),
                    None,
                    adapter.model,
                    "gpt-5.6-luna",
                    bge_path,
                )
                provider, reranker = QueryEmbeddingProvider(), RecordingReranker()
                app = create_app(
                    settings,
                    provider=provider,
                    index_loader=ActiveIndexLoader(runtime, reranker=reranker),
                    reranker=reranker,
                )
                route = next(
                    route
                    for route in app.routes
                    if getattr(route, "path", None) == "/v1/search" and "POST" in getattr(route, "methods", set())
                )
                response = route.endpoint(QueryRequest(query="Card 할인율은 얼마야?", profile=profile))
                body = response.model_dump()
                self.assertEqual(body["profile"], profile)
                self.assertEqual(body["cards"][0]["card_key"], self.document_id)
                self.assertTrue(body["evidence"])
                expected_level = "structural" if profile == "parent_child_bundle" else "benefit"
                self.assertTrue(all(row["level"] == expected_level for row in body["evidence"]))
                self.assertEqual(reranker.calls, 1 if profile == "parent_child_bundle" else 0)

    def test_openai_embedding_adapter_batches_and_restores_response_order(self) -> None:
        client = FakeEmbeddingClient()
        adapter = OpenAIEmbeddingAdapter(api_key=None, batch_size=2, client=client)
        vectors, usage = adapter.embed_documents(["one", "two", "three"])
        self.assertEqual(vectors.shape, (3, 1536))
        self.assertEqual(vectors[:, 0].tolist(), [1.0, 2.0, 1.0])
        self.assertEqual([call["input"] for call in client.calls], [["one", "two"], ["three"]])
        self.assertTrue(all(call["model"] == "text-embedding-3-small" for call in client.calls))
        self.assertTrue(all(call["dimensions"] == 1536 for call in client.calls))
        self.assertTrue(all(call["encoding_format"] == "float" for call in client.calls))
        self.assertEqual(usage["request_count"], 2)
        self.assertEqual(usage["provider_usage"], [{"total_tokens": 2}, {"total_tokens": 1}])

    def test_live_lane_artifacts_are_separate_resumable_and_auditable(self) -> None:
        import pymupdf

        source = self.root / "live-source.pdf"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(source)
        pdf.close()
        manifest = self.root / "live-source-manifest.json"
        write_json(manifest, {"documents": [{"document_id": self.document_id, "source_pdf": source.name}]})
        structurer = FakeStructurer()
        luna_ocr, upstage_ocr = FakeTranscriber("luna"), FakeTranscriber("upstage", extra_line="추가 문구")
        sources = {self.document_id: source}
        providers = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "live", luna_ocr, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "live", upstage_ocr, structurer),
        }
        prepared = self.indexer.ocr(manifest, None, None, config={"mode": "live-fixture"}, providers=providers)
        self.assertEqual(prepared["status"]["run"]["status"], "canonical_approved")
        self.assertEqual(prepared["ocr_difference_documents"], [self.document_id])
        self.assertEqual((luna_ocr.calls, upstage_ocr.calls), (1, 1))
        self.assertEqual(structurer.calls, ["luna", "upstage"])
        document_root = self.root / "runtime/working" / prepared["run_id"] / "documents/issuer__card"
        for provider in ("luna", "upstage"):
            self.assertTrue((document_root / provider / "ocr.txt").is_file())
            self.assertTrue((document_root / provider / "pages.json").is_file())
            self.assertTrue((document_root / provider / "normalized.json").is_file())
        comparison = json.loads((document_root / "validation/ocr_comparison.json").read_text(encoding="utf-8"))
        self.assertFalse(comparison["all_normalized_text_equal"])
        self.assertEqual(json.loads((document_root / "validation/normalized_json_comparison.json").read_text())["status"], "pass")
        normalized = providers["luna"].artifact_paths(self.document_id)["normalized"]
        normalized.unlink()
        providers["luna"].load(self.document_id)
        self.assertEqual(structurer.calls, ["luna", "upstage"])
        resumed = self.indexer.ocr(manifest, None, None, config={"mode": "live-fixture"}, providers=providers)
        self.assertEqual(resumed["run_id"], prepared["run_id"])
        self.assertEqual((luna_ocr.calls, upstage_ocr.calls), (1, 1))

    def test_new_structure_config_reuses_ocr_cache(self) -> None:
        import pymupdf

        source = self.root / "restructure.pdf"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(source)
        pdf.close()
        sources = {self.document_id: source}
        first_ocr, first_structure = FakeTranscriber("luna"), FakeStructurer("structure-v1")
        first = LiveLaneAdapter("luna", sources, self.root / "shared-ocr-cache", first_ocr, first_structure)
        first_path, _payload = first.load(self.document_id)

        second_ocr, second_structure = FakeTranscriber("luna"), FakeStructurer("structure-v2")
        second = LiveLaneAdapter("luna", sources, self.root / "shared-ocr-cache", second_ocr, second_structure)
        second_path, _payload = second.load(self.document_id)

        self.assertNotEqual(first_path, second_path)
        self.assertEqual(first_ocr.calls, 1)
        self.assertEqual(second_ocr.calls, 0)
        self.assertEqual(second_structure.calls, ["luna"])

    def test_live_provider_normalizers_and_openai_boundaries_are_explicit(self) -> None:
        import pymupdf

        pdf = self.root / "valid.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(pdf)
        document.close()
        ocr_client = FakeResponsesClient([{"pages": [{"page": 1, "text": "Issuer Card", "uncertain_spans": []}]}])
        raw, pages, provenance = LunaOcrTranscriber("secret", client=ocr_client).transcribe(pdf)
        self.assertEqual(raw["id"], "response-1")
        self.assertEqual(pages_text(pages), "=== PAGE 1 ===\nIssuer Card\n")
        self.assertEqual(provenance["endpoint"], "openai.responses")
        self.assertFalse(ocr_client.calls[0]["store"])
        self.assertEqual(ocr_client.calls[0]["text"]["format"]["name"], "ocr_pages")
        self.assertNotIn("max_output_tokens", ocr_client.calls[0])
        content = ocr_client.calls[0]["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], ["input_file", "input_text"])
        self.assertEqual(content[0]["detail"], "high")

        structured = FakeStructurer().structure("luna", [{"page": 1, "text": "Issuer Card\n상품 안내: 카페 monthly 1% 할인"}])[1]
        structure_client = FakeResponsesClient([structured])
        _, output = LunaFactStructurer("secret", client=structure_client).structure("luna", pages)
        self.assertEqual(output["identity"]["card_name"], "Card")
        self.assertEqual(structure_client.calls[0]["text"]["format"]["name"], "card_facts")
        self.assertEqual(structure_client.calls[0]["max_output_tokens"], 128_000)
        self.assertNotIn("upstage", structure_client.calls[0]["input"][0]["content"][0]["text"].casefold())
        self.assertIn("모든 필드는 같은 evidence.quote", STRUCTURE_PROMPT)

        normalized = upstage_pages({"elements": [{"page": 1, "content": {"markdown": "Issuer Card"}}]}, 1)
        self.assertEqual(normalized[0]["text"], "Issuer Card")

    def test_live_ocr_stages_are_independent_and_barriered(self) -> None:
        import pymupdf

        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(self.source)
        document.close()
        sources = {self.document_id: self.source}
        luna_ocr, upstage_ocr, structurer = FakeTranscriber("luna"), FakeTranscriber("upstage"), FakeStructurer()
        adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "stage-cache", luna_ocr, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "stage-cache", upstage_ocr, structurer),
        }
        config = {"mode": "staged-test", "luna": luna_ocr.config, "upstage": upstage_ocr.config, "structure": structurer.config}

        extracted = self.indexer.staged_ocr(self.manifest, config=config, providers=adapters, stage="extract")
        self.assertEqual(extracted["status"]["run"]["status"], "ocr_extracted")
        self.assertEqual((luna_ocr.calls, upstage_ocr.calls), (1, 1))
        self.assertEqual(structurer.calls, [])

        structured = self.indexer.staged_ocr(self.manifest, config=config, providers=adapters, stage="structure")
        self.assertEqual(structured["status"]["run"]["status"], "structured")
        self.assertEqual((luna_ocr.calls, upstage_ocr.calls), (1, 1))
        self.assertEqual(structurer.calls, ["luna", "upstage"])

        normalized = self.indexer.staged_ocr(self.manifest, config=config, providers=adapters, stage="normalize")
        self.assertEqual(normalized["status"]["run"]["status"], "normalized")
        validated = self.indexer.staged_ocr(self.manifest, config=config, providers=adapters, stage="validate")
        self.assertEqual(validated["status"]["run"]["status"], "canonical_approved")

        empty_structurer = FakeStructurer()
        empty_adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "empty-stage-cache", FakeTranscriber("luna"), empty_structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "empty-stage-cache", FakeTranscriber("upstage"), empty_structurer),
        }
        blocked = self.indexer.staged_ocr(self.manifest, config={**config, "mode": "missing-extract"}, providers=empty_adapters, stage="structure")
        self.assertEqual(blocked["status"]["run"]["status"], "blocked")
        self.assertEqual(empty_structurer.calls, [])

        parsed = cli_parser().parse_args(["ocr", "extract", "--source-manifest", str(self.manifest), "--confirm-luna", "--confirm-upstage"])
        self.assertEqual(parsed.ocr_stage, "extract")

    def test_ocr_orchestrator_stops_before_structure_when_any_extraction_fails(self) -> None:
        import pymupdf

        second_source = self.root / "second.pdf"
        third_source = self.root / "third.pdf"
        for path in (self.source, second_source, third_source):
            document = pymupdf.open()
            document.new_page().insert_text((72, 72), path.stem)
            document.save(path)
            document.close()
        manifest = self.root / "three-documents.json"
        write_json(manifest, {"documents": [
            {"document_id": self.document_id, "source_pdf": str(self.source)},
            {"document_id": "issuer/second", "source_pdf": str(second_source)},
            {"document_id": "issuer/third", "source_pdf": str(third_source)},
        ]})

        class FailingTranscriber(FakeTranscriber):
            def __init__(self, provider: str) -> None:
                super().__init__(provider)
                self.attempted: list[Path] = []

            def request(self, source: Path):
                self.attempted.append(source)
                if source == second_source:
                    raise OcrProviderError("fixture extraction failure")
                return super().request(source)

        sources = {self.document_id: self.source, "issuer/second": second_source, "issuer/third": third_source}
        luna_ocr, upstage_ocr, structurer = FailingTranscriber("luna"), FakeTranscriber("upstage"), FakeStructurer()
        adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "barrier-cache", luna_ocr, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "barrier-cache", upstage_ocr, structurer),
        }
        config = {"mode": "barrier-test", "luna": luna_ocr.config, "upstage": upstage_ocr.config, "structure": structurer.config}
        result = self.indexer.orchestrated_ocr(manifest, config=config, providers=adapters)
        self.assertEqual(result["completed_stages"], ["extract"])
        self.assertEqual(result["stopped_before"], "structure")
        self.assertEqual(result["status"]["run"]["status"], "blocked")
        self.assertEqual(structurer.calls, [])
        self.assertEqual(luna_ocr.attempted, [self.source, second_source, third_source])
        self.assertEqual(upstage_ocr.calls, 3)
        statuses = {row["document_id"]: row["status"] for row in result["status"]["documents"]}
        self.assertEqual(statuses, {
            self.document_id: "ocr_extracted",
            "issuer/second": "blocked",
            "issuer/third": "ocr_extracted",
        })

    def test_ocr_orchestrator_stops_before_normalize_when_structure_fails(self) -> None:
        import pymupdf

        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(self.source)
        document.close()

        class FailingStructurer(FakeStructurer):
            def request(self, provider: str, pages: list[dict[str, object]]):
                if provider == "luna":
                    self.calls.append(provider)
                    raise OcrProviderError("fixture structure failure")
                return super().request(provider, pages)

        sources = {self.document_id: self.source}
        luna_ocr, upstage_ocr, structurer = FakeTranscriber("luna"), FakeTranscriber("upstage"), FailingStructurer()
        adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "structure-barrier-cache", luna_ocr, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "structure-barrier-cache", upstage_ocr, structurer),
        }
        config = {"mode": "structure-barrier-test", "luna": luna_ocr.config, "upstage": upstage_ocr.config, "structure": structurer.config}

        result = self.indexer.orchestrated_ocr(self.manifest, config=config, providers=adapters)

        self.assertEqual(result["completed_stages"], ["extract", "structure"])
        self.assertEqual(result["stopped_before"], "normalize")
        self.assertEqual(result["status"]["run"]["status"], "blocked")
        self.assertEqual(structurer.calls, ["luna", "upstage"])
        self.assertFalse(any(row["stage"] == "normalize" for row in result["status"]["stages"]))

    def test_ocr_orchestrator_stops_before_validate_when_normalize_fails(self) -> None:
        import pymupdf

        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(self.source)
        document.close()

        class FailingNormalizeAdapter(LiveLaneAdapter):
            def normalize(self, document_id: str):
                if self.provider == "luna":
                    raise ValueError("fixture normalize failure")
                return super().normalize(document_id)

        sources = {self.document_id: self.source}
        luna_ocr, upstage_ocr, structurer = FakeTranscriber("luna"), FakeTranscriber("upstage"), FakeStructurer()
        adapters = {
            "luna": FailingNormalizeAdapter("luna", sources, self.root / "normalize-barrier-cache", luna_ocr, structurer),
            "upstage": FailingNormalizeAdapter("upstage", sources, self.root / "normalize-barrier-cache", upstage_ocr, structurer),
        }
        config = {"mode": "normalize-barrier-test", "luna": luna_ocr.config, "upstage": upstage_ocr.config, "structure": structurer.config}

        result = self.indexer.orchestrated_ocr(self.manifest, config=config, providers=adapters)

        self.assertEqual(result["completed_stages"], ["extract", "structure", "normalize"])
        self.assertEqual(result["stopped_before"], "validate")
        self.assertEqual(result["status"]["run"]["status"], "blocked")
        validation_stages = {"ocr_comparison", "grounding", "structured", "relation"}
        self.assertFalse(any(row["stage"] in validation_stages for row in result["status"]["stages"]))

    def test_upstage_multi_page_grounding_requires_explicit_complete_pages(self) -> None:
        valid = upstage_pages(
            {"usage": {"pages": 2}, "elements": [
                {"page": 1, "content": {"markdown": "첫 페이지"}},
                {"page": 2, "content": {"markdown": "둘째 페이지"}},
            ]},
            2,
        )
        self.assertEqual([row["text"] for row in valid], ["첫 페이지", "둘째 페이지"])
        with self.assertRaisesRegex(ValueError, "page number is required"):
            upstage_pages({"elements": [{"content": {"markdown": "페이지 미상"}}]}, 2)
        with self.assertRaisesRegex(ValueError, "outside"):
            upstage_pages({"elements": [{"page": 3, "content": {"markdown": "범위 밖"}}]}, 2)
        with self.assertRaisesRegex(ValueError, "no text"):
            upstage_pages({"elements": [{"page": 1, "content": {"markdown": "첫 페이지만"}}]}, 2)

    def test_cached_raw_response_prevents_paid_retry_after_parse_failure(self) -> None:
        import pymupdf

        source = self.root / "parse-failure.pdf"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(source)
        pdf.close()

        class ParseFailure(FakeTranscriber):
            def parse(self, _raw, _expected_count):
                raise ValueError("invalid provider payload")

        transcriber = ParseFailure("luna")
        adapter = LiveLaneAdapter("luna", {self.document_id: source}, self.root / "retry", transcriber, FakeStructurer())
        for _attempt in range(2):
            with self.assertRaisesRegex(OcrProviderError, "response validation failed"):
                adapter.load(self.document_id)
        self.assertEqual(transcriber.calls, 1)

    def test_cached_structure_response_prevents_paid_retry_after_parse_failure(self) -> None:
        import pymupdf

        source = self.root / "structure-failure.pdf"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(source)
        pdf.close()

        class StructureFailure(FakeStructurer):
            def parse(self, _raw):
                raise OcrProviderError("invalid structured payload")

        transcriber, structurer = FakeTranscriber("luna"), StructureFailure()
        adapter = LiveLaneAdapter("luna", {self.document_id: source}, self.root / "structure-retry", transcriber, structurer)
        for _attempt in range(2):
            with self.assertRaisesRegex(OcrProviderError, "structured response validation failed"):
                adapter.load(self.document_id)
        self.assertEqual(transcriber.calls, 1)
        self.assertEqual(structurer.calls, ["luna"])

    def test_bad_pdf_isolated_from_next_document(self) -> None:
        import pymupdf

        bad, good = self.root / "bad.pdf", self.root / "good.pdf"
        bad.write_bytes(b"not-a-pdf")
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(good)
        pdf.close()
        manifest = self.root / "batch-manifest.json"
        write_json(manifest, {"documents": [
            {"document_id": "a/bad", "source_pdf": bad.name},
            {"document_id": "z/good", "source_pdf": good.name},
        ]})
        sources = {"a/bad": bad, "z/good": good}
        structurer = FakeStructurer()
        luna, upstage = FakeTranscriber("luna"), FakeTranscriber("upstage")
        providers = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "batch", luna, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "batch", upstage, structurer),
        }
        result = self.indexer.ocr(manifest, None, None, config={"mode": "batch-isolation"}, providers=providers)
        statuses = {row["document_id"]: row["status"] for row in result["status"]["documents"]}
        self.assertEqual(statuses, {"a/bad": "blocked", "z/good": "canonical_approved"})
        self.assertEqual((luna.calls, upstage.calls), (1, 1))

    def test_risky_ignore_and_duplicate_disposition_fail_closed(self) -> None:
        risky = lane(self.document_id, "luna")
        risky["pages"][0]["text"] += "\n카페 10% 할인"
        risky["span_dispositions"].append({"page": 1, "quote": "카페 10% 할인", "kind": "ignore", "reason": "omitted"})
        with self.assertRaisesRegex(ValueError, "benefit-like"):
            validate_lane("luna", risky)
        safe = lane(self.document_id, "luna")
        safe["pages"][0]["text"] += "\n청구할인 서비스\n연체이자율 최대 3%\n카드 신규 출시 이후 할인혜택 유지"
        safe["span_dispositions"].extend([
            {"page": 1, "quote": "청구할인 서비스", "kind": "ignore", "reason": "섹션 제목"},
            {"page": 1, "quote": "연체이자율 최대 3%", "kind": "ignore", "reason": "연체이자율 안내"},
            {"page": 1, "quote": "카드 신규 출시 이후 할인혜택 유지", "kind": "ignore", "reason": "부가서비스 유지 고지"},
        ])
        self.assertEqual(len(validate_lane("luna", safe)), 1)
        duplicate = lane(self.document_id, "luna")
        duplicate["span_dispositions"].append(dict(duplicate["span_dispositions"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            validate_lane("luna", duplicate)
        repeated = lane(self.document_id, "luna")
        repeated["pages"][0]["text"] = repeated["pages"][0]["text"].replace("Issuer Card", "Issuer Card\nIssuer Card")
        repeated["span_dispositions"].append(dict(repeated["span_dispositions"][0]))
        self.assertEqual(len(validate_lane("luna", repeated)), 1)
        markdown_identity = lane(self.document_id, "luna")
        markdown_identity["pages"][0]["text"] = markdown_identity["pages"][0]["text"].replace("Issuer Card", "# Issuer Card")
        markdown_identity["span_dispositions"][0]["quote"] = "# Issuer Card"
        self.assertEqual(len(validate_lane("luna", markdown_identity)), 1)

    def test_risky_ignore_is_non_resolvable_restructure_block(self) -> None:
        for provider, root in (("luna", self.luna_dir), ("upstage", self.upstage_dir)):
            payload = lane(self.document_id, provider)
            payload["pages"][0]["text"] += "\n카페 10% 할인"
            payload["span_dispositions"].append({"page": 1, "quote": "카페 10% 할인", "kind": "ignore", "reason": "omitted"})
            write_json(root / "issuer__card.json", payload)
        result = self.indexer.ocr(self.manifest, self.luna_dir, self.upstage_dir, config={"mode": "risky-ignore"})
        self.assertEqual(result["status"]["documents"][0]["status"], "blocked")
        self.assertEqual(result["status"]["reviews"], [])
        stage = next(row for row in result["status"]["stages"] if row["stage"] == "structured")
        self.assertEqual(json.loads(stage["detail_json"])["action"], "new_structuring_run")

    def test_cli_live_ocr_approval_is_fail_closed(self) -> None:
        one_flag = cli_parser().parse_args(["ocr", "--source-manifest", str(self.manifest), "--confirm-luna"])
        with self.assertRaisesRegex(RuntimeError, "both --confirm"):
            run_ocr(self.indexer, one_flag)
        both_flags = cli_parser().parse_args(["ocr", "--source-manifest", str(self.manifest), "--confirm-luna", "--confirm-upstage"])
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "API_KEY"):
            run_ocr(self.indexer, both_flags)
        mixed = cli_parser().parse_args([
            "ocr", "--source-manifest", str(self.manifest), "--luna-json-dir", str(self.luna_dir),
            "--upstage-json-dir", str(self.upstage_dir), "--confirm-luna", "--confirm-upstage",
        ])
        with self.assertRaisesRegex(RuntimeError, "mutually exclusive"):
            run_ocr(self.indexer, mixed)

    def test_cli_stage_approval_boundaries(self) -> None:
        structure_without_approval = cli_parser().parse_args([
            "ocr", "structure", "--source-manifest", str(self.manifest),
        ])
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}, clear=True), self.assertRaisesRegex(
            RuntimeError, "--confirm-luna"
        ):
            run_ocr(self.indexer, structure_without_approval)

        structure_without_key = cli_parser().parse_args([
            "ocr", "structure", "--source-manifest", str(self.manifest), "--confirm-luna",
        ])
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            run_ocr(self.indexer, structure_without_key)

        fake_luna, fake_upstage, fake_structurer = FakeTranscriber("luna"), FakeTranscriber("upstage"), FakeStructurer()
        constructors = (
            mock.patch("pickcardu_indexer.__main__.LunaOcrTranscriber", return_value=fake_luna),
            mock.patch("pickcardu_indexer.__main__.UpstageOcrTranscriber", return_value=fake_upstage),
            mock.patch("pickcardu_indexer.__main__.LunaFactStructurer", return_value=fake_structurer),
        )
        with constructors[0], constructors[1], constructors[2], mock.patch.object(
            self.indexer, "staged_ocr", return_value={"run_id": "fixture"}
        ) as staged:
            structure = cli_parser().parse_args([
                "ocr", "structure", "--source-manifest", str(self.manifest), "--confirm-luna",
            ])
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "fixture"}, clear=True):
                run_ocr(self.indexer, structure)
            self.assertEqual(staged.call_args.kwargs["stage"], "structure")

            for stage in ("normalize", "validate"):
                staged.reset_mock()
                arguments = cli_parser().parse_args(["ocr", stage, "--source-manifest", str(self.manifest)])
                with mock.patch.dict(os.environ, {}, clear=True):
                    run_ocr(self.indexer, arguments)
                self.assertEqual(staged.call_args.kwargs["stage"], stage)

    def test_two_phase_index_and_partial_real_release_is_preview_only(self) -> None:
        prepared = self.indexer.ocr(self.manifest, self.luna_dir, self.upstage_dir, config={"mode": "local-fixture"})
        self.assertEqual(prepared["status"]["releases"], [])
        self.indexer.state.upsert_document(prepared["run_id"], "issuer/unapproved", str(self.source), SOURCE_SHA, "review")
        adapter = DeterministicEmbeddingAdapter()
        indexed = self.indexer.index(prepared["run_id"], allow_preview=True, fake_vectors=False, embedding_adapter=adapter)
        release_id = indexed["release_id"]
        manifest = json.loads((self.root / "runtime/index-release" / release_id / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["release_status"], "preview")
        self.assertEqual(manifest["coverage"]["omitted_document_ids"], ["issuer/unapproved"])
        with self.assertRaisesRegex(RuntimeError, "production"):
            self.indexer.activate(release_id)

    def test_relation_mismatch_opens_review_and_blocks_publish(self) -> None:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaisesRegex(RuntimeError, "release blocked"):
            self.execute_indexer()
        status = self.indexer.state.status()
        self.assertEqual(status["documents"][0]["status"], "review")
        self.assertEqual(status["reviews"][0]["kind"], "relation_mismatch")

    def test_resolution_audits_and_revalidates(self) -> None:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 monthly daily 1% 할인"))
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "after.json"
        payload = resolution_payload()
        payload["resolution"]["reason"] = "selected grounded Luna relation"
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily relation differs"}]
        payload["canonical"][0]["evidence_refs"]["upstage"] = {"provider": "upstage", "page": 1, "quote": "카페 monthly daily 1% 할인", "supports_selected": False}
        write_json(after, payload)
        self.indexer.resolve_review(review_id, "reviewer@example.test", "checked source evidence", after, self.luna_dir, self.upstage_dir)
        review = self.indexer.state.review(review_id)
        self.assertEqual(review["status"], "resolved")
        self.assertEqual(review["reviewer"], "reviewer@example.test")
        release_id = self.indexer.publish(review["run_id"], allow_partial=False, fake_vectors=True)
        self.assertTrue((self.root / "runtime/index-release" / release_id / "chroma").is_dir())

    def test_rule_and_grounding_failures_are_not_silenced(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            normalise_fact({"target": "x", "value": "", "unit": "원"})
        self.assertEqual(
            normalise_fact({"target": "온라인 예매", "exceptions": "제외"})["exceptions"],
            "제외",
        )
        with self.assertRaisesRegex(ValueError, "dash"):
            normalise_fact({"target": "x", "value": "-", "unit": "원"})
        with self.assertRaisesRegex(ValueError, "linked"):
            validate_lane("luna", lane(self.document_id, "luna", value="2", quote="카페 monthly 1% 할인"))
        invalid = lane(self.document_id, "luna")
        invalid["facts"][0]["evidence"]["quote"] = "없는 근거"
        with self.assertRaisesRegex(ValueError, "grounded"):
            validate_lane("luna", invalid)
        hallucinated = lane(self.document_id, "luna", value="캐시백", quote="카페 monthly % 할인")
        with self.assertRaisesRegex(ValueError, "non-numeric value"):
            validate_lane("luna", hallucinated)

    def test_field_and_critical_span_coverage_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "condition"):
            validate_lane("luna", lane(self.document_id, "luna", quote="카페 1% 할인"))
        missing = lane(self.document_id, "luna")
        missing["pages"] = [{"page": 1, "text": "Issuer Card\n카페 monthly 1% 할인\n주유 daily 2% 할인"}]
        missing["span_dispositions"] = [{"page": 1, "quote": "Issuer Card", "kind": "identity"}, {"page": 1, "quote": "카페 monthly 1% 할인", "kind": "fact"}]
        with self.assertRaisesRegex(ValueError, "OCR line lacks explicit disposition"):
            validate_lane("luna", missing)
        zero = lane(self.document_id, "luna", value="0", quote="카페 monthly 0% 할인")
        self.assertEqual(len(validate_lane("luna", zero)), 1)
        mislabeled = lane(self.document_id, "luna")
        mislabeled["span_dispositions"][0]["kind"] = "fact"
        with self.assertRaisesRegex(ValueError, "fact disposition"):
            validate_lane("luna", mislabeled)

    def test_ocr_comparison_and_chunk_profiles_are_explicit(self) -> None:
        luna_payload = lane(self.document_id, "luna")
        upstage_payload = lane(self.document_id, "upstage")
        upstage_payload["pages"][0]["text"] += "\n추가 문구"
        upstage_payload["span_dispositions"].append({"page": 1, "quote": "추가 문구", "kind": "ignore", "reason": "diagnostic"})
        audit = compare_ocr_outputs(luna_payload, upstage_payload)
        self.assertEqual(audit["purpose"], "diagnostic_only_not_a_correctness_or_selection_gate")
        self.assertFalse(audit["all_normalized_text_equal"])

        result = self.execute_indexer()
        approved = [row for row in self.indexer.state.documents(result["run_id"]) if row["status"] == "canonical_approved"]
        baseline, _ = self.indexer._chunks(result["run_id"], approved, "card_page_section_benefit")
        experimental, _ = self.indexer._chunks(result["run_id"], approved, "parent_child_bundle")
        self.assertEqual({row["level"] for row in baseline}, {"card", "page", "section", "benefit"})
        self.assertEqual({row["level"] for row in experimental}, {"structural"})
        self.assertTrue(all(row["metadata"]["reranker_text"] for row in baseline + experimental))

        alternate = self.indexer.run(
            self.manifest,
            self.luna_dir,
            self.upstage_dir,
            fake_vectors=True,
            allow_partial=False,
            config={"profile": "parent_child_bundle", "fake_vectors": True, "allow_partial": False},
        )
        self.assertNotEqual(alternate["run_id"], result["run_id"])
        alternate_manifest = json.loads(
            (
                self.root
                / "runtime/index-release"
                / str(alternate["release_id"])
                / "manifest.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(alternate_manifest["strategy"], "parent_child_bundle")

    def test_historical_parent_child_chunks_reproduce_all_ten_cards(self) -> None:
        root = PROJECT_ROOT / "data/ocr_benchmark/gold/raw"
        chunks, nodes = [], []
        for path in sorted(root.glob("*/*.txt")):
            produced, hierarchy, _audit = build_structural_chunks(
                path.read_text(encoding="utf-8"),
                document_id=f"{path.parent.name}/{path.stem}",
                issuer=path.parent.name,
                card_name=path.stem,
                source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            chunks.extend(produced)
            nodes.extend(hierarchy)
        ids = "\n".join(sorted(row["chunk_id"] for row in chunks)) + "\n"
        self.assertEqual((len(chunks), len(nodes)), (147, 172))
        self.assertEqual(hashlib.sha256(ids.encode()).hexdigest(), "65e83ae1f328a340bcd9e14290545e7ba12e2d2dcccb186ccbb379f0325038e0")

    def test_duplicate_ocr_page_numbers_fail_closed(self) -> None:
        duplicated = lane(self.document_id, "luna")
        duplicated["pages"].append(dict(duplicated["pages"][0]))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            compare_ocr_outputs(duplicated, lane(self.document_id, "upstage"))
        with self.assertRaisesRegex(ValueError, "duplicated"):
            validate_lane("luna", duplicated)

    def test_identity_grounding_and_lane_mismatch_are_reviews(self) -> None:
        ungrounded = lane(self.document_id, "luna")
        ungrounded["identity"]["issuer_evidence"]["quote"] = "Missing issuer"
        with self.assertRaisesRegex(ValueError, "identity is not grounded"):
            validate_lane("luna", ungrounded)
        mismatched = lane(self.document_id, "upstage")
        mismatched["identity"]["card_name"] = "OtherCard"
        mismatched["identity"]["card_evidence"]["quote"] = "Card OtherCard"
        mismatched["pages"][0]["text"] = mismatched["pages"][0]["text"].replace("Issuer Card", "Issuer Card OtherCard")
        mismatched["span_dispositions"][0]["quote"] = "Issuer Card OtherCard"
        write_json(self.upstage_dir / "issuer__card.json", mismatched)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        self.assertEqual(self.indexer.state.status()["reviews"][0]["kind"], "identity_mismatch")

    def test_release_identity_mismatch_is_blocked(self) -> None:
        chunks = [{"chunk_id": "a", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        embeddings = self.indexer._fake_embedding("x", 16).reshape(1, 16)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.indexer._build_fts(root / "corpus.sqlite", chunks)
            self.indexer._build_chroma(root / "chroma", chunks, embeddings)
            with self.assertRaisesRegex(RuntimeError, "manifest identity"):
                self.indexer._verify_release(root, {"chunk_ids": ["wrong"], "document_ids": ["d"]}, chunks, embeddings)

    def test_fts_chroma_identity_mismatch_is_blocked(self) -> None:
        fts_chunks = [{"chunk_id": "a", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        chroma_chunks = [{"chunk_id": "b", "document_id": "d", "level": "benefit", "text": "x", "metadata": {}}]
        embeddings = self.indexer._fake_embedding("x", 16).reshape(1, 16)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.indexer._build_fts(root / "corpus.sqlite", fts_chunks)
            self.indexer._build_chroma(root / "chroma", chroma_chunks, embeddings)
            with self.assertRaisesRegex(RuntimeError, "FTS5-Chroma identity"):
                self.indexer._verify_release(root, {"chunk_ids": ["a"], "document_ids": ["d"]}, fts_chunks, embeddings)

    def test_provider_artifacts_are_recorded_per_lane(self) -> None:
        self.execute_indexer()
        rows = self.indexer.state.connection.execute(
            "SELECT provider, path FROM artifacts WHERE kind='ocr_json' ORDER BY provider"
        ).fetchall()
        self.assertEqual([(row["provider"], Path(row["path"]).parent.name) for row in rows], [("luna", "luna"), ("upstage", "upstage")])

    def test_changed_lane_input_invalidates_resume(self) -> None:
        first = self.execute_indexer()
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily"))
        with self.assertRaisesRegex(RuntimeError, "release blocked"):
            self.execute_indexer()
        run_ids = [row[0] for row in self.indexer.state.connection.execute("SELECT run_id FROM runs ORDER BY created_at, run_id")]
        self.assertEqual(len(run_ids), 2)
        self.assertIn(first["run_id"], run_ids)

    def test_resolved_review_retry_is_fully_immutable(self) -> None:
        review_id, after = self.open_relation_review()
        result = self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        path = Path(result["canonical_path"])
        before = (sorted(path.parent.iterdir()), hashlib.sha256(path.read_bytes()).hexdigest(), dict(self.indexer.state.review(review_id)))
        with self.assertRaisesRegex(RuntimeError, "not open"):
            self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        self.assertEqual(before, (sorted(path.parent.iterdir()), hashlib.sha256(path.read_bytes()).hexdigest(), dict(self.indexer.state.review(review_id))))

    def test_resolve_rejects_stale_source_and_lanes(self) -> None:
        for changed in ("source", "luna", "upstage"):
            with self.subTest(changed=changed):
                self.tearDown(); self.setUp()
                review_id, after = self.open_relation_review()
                if changed == "source":
                    self.source.write_bytes(b"changed")
                else:
                    target = self.luna_dir if changed == "luna" else self.upstage_dir
                    payload = json.loads((target / "issuer__card.json").read_text())
                    payload["provenance"]["config_hash"] = "changed"
                    write_json(target / "issuer__card.json", payload)
                with self.assertRaisesRegex(RuntimeError, "stale"):
                    self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)

    def test_strict_resolution_rejects_mixed_wrong_and_false_support(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        for kind in ("mixed", "wrong", "false"):
            with self.subTest(kind=kind):
                payload = resolution_payload()
                if kind == "mixed": payload["canonical"][0]["fact"]["condition"] = "daily"
                if kind == "wrong": payload["canonical"][0]["evidence_refs"]["luna"]["quote"] = "wrong"
                if kind == "false": payload["canonical"][0]["evidence_refs"]["upstage"]["supports_selected"] = False
                with self.assertRaises(ValueError): strict_resolution(payload, luna, upstage)

    def test_strict_resolution_requires_complete_selected_and_rejected_sets(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload()
        payload["resolution"]["rejected_relations"] = [{"provider": "upstage", "tuple": ["카페", "daily", "1", "%", "", "", "", ""], "reason": "daily differs"}]
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            strict_resolution(payload, luna, upstage)
        payload["canonical"].append(relation_item("weekly", "카페 weekly 1% 할인", "카페 daily 1% 할인", luna_supports=True, upstage_supports=False))
        canonical, identity, audit = strict_resolution(payload, luna, upstage)
        self.assertEqual([item["fact"]["condition"] for item in canonical], ["monthly", "weekly"])
        self.assertEqual(identity["card_name"], "Card")
        self.assertEqual(audit["resolution"]["rejected_relations"], payload["resolution"]["rejected_relations"])
        payload["resolution"]["rejected_relations"] = []
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            strict_resolution(payload, luna, upstage)

    def test_strict_resolution_accepts_complete_upstage_selection(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload()
        payload["resolution"]["selected_provider"] = "upstage"
        payload["resolution"]["rejected_relations"] = [{"provider": "luna", "tuple": ["카페", "weekly", "1", "%", "", "", "", ""], "reason": "weekly differs"}]
        payload["canonical"].append(relation_item("daily", "카페 weekly 1% 할인", "카페 daily 1% 할인", luna_supports=False, upstage_supports=True))
        canonical, _, _ = strict_resolution(payload, luna, upstage)
        self.assertEqual([item["fact"]["condition"] for item in canonical], ["daily", "monthly"])

    def test_upstage_identity_resolution_preserves_exact_provenance(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        upstage["identity"]["card_name"] = "OtherCard"
        upstage["identity"]["card_evidence"]["quote"] = "OtherCard"
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        upstage["span_dispositions"][0]["quote"] = "Issuer OtherCard"
        payload = resolution_payload()
        payload["resolution"]["selected_identity_provider"] = "upstage"
        payload["identity"]["card_name"] = "OtherCard"
        payload["identity"]["evidence_refs"]["luna"]["supports_selected"] = False
        payload["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "OtherCard"
        _, identity, _ = strict_resolution(payload, luna, upstage)
        self.assertEqual(identity["card_name"], "OtherCard")
        self.assertFalse(identity["evidence_refs"]["luna"]["supports_selected"])
        for change in ("wrong_name", "wrong_evidence", "false_support"):
            invalid = json.loads(json.dumps(payload))
            if change == "wrong_name": invalid["identity"]["card_name"] = "Card"
            if change == "wrong_evidence": invalid["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "wrong"
            if change == "false_support": invalid["identity"]["evidence_refs"]["luna"]["supports_selected"] = True
            with self.subTest(change=change), self.assertRaises(ValueError):
                strict_resolution(invalid, luna, upstage)

    def test_resolve_review_uses_selected_upstage_identity(self) -> None:
        upstage = lane(self.document_id, "upstage")
        upstage["identity"]["card_name"] = "OtherCard"
        upstage["identity"]["card_evidence"]["quote"] = "OtherCard"
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        upstage["span_dispositions"][0]["quote"] = "Issuer OtherCard"
        write_json(self.upstage_dir / "issuer__card.json", upstage)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        payload = resolution_payload()
        payload["resolution"]["selected_identity_provider"] = "upstage"
        payload["identity"]["card_name"] = "OtherCard"
        payload["identity"]["evidence_refs"]["luna"]["supports_selected"] = False
        payload["identity"]["evidence_refs"]["upstage"]["card"]["quote"] = "OtherCard"
        after = self.root / "identity-resolution.json"
        write_json(after, payload)
        result = self.indexer.resolve_review(review_id, "reviewer", "Upstage identity is grounded", after, self.luna_dir, self.upstage_dir)
        saved = json.loads(Path(result["canonical_path"]).read_text(encoding="utf-8"))
        self.assertEqual(saved["identity"]["card_name"], "OtherCard")
        self.assertFalse(saved["identity"]["evidence_refs"]["luna"]["supports_selected"])

    def test_canonical_tamper_blocks_publish_and_auto_path_is_hashed(self) -> None:
        result = self.execute_indexer()
        document = result["status"]["documents"][0]
        path = Path(document["canonical_path"])
        self.assertEqual(path.stem, document["canonical_sha256"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), document["canonical_sha256"])
        path.chmod(0o644); path.write_text("tampered"); path.chmod(0o444)
        with self.assertRaisesRegex(RuntimeError, "canonical artifact hash mismatch"):
            self.indexer.publish(result["run_id"], allow_partial=False, fake_vectors=True)

    def test_resolve_transaction_failure_rolls_back_review_and_document(self) -> None:
        review_id, after = self.open_relation_review()
        self.indexer.state.connection.execute("CREATE TRIGGER reject_canonical BEFORE UPDATE OF canonical_path ON documents BEGIN SELECT RAISE(ABORT, 'trigger'); END")
        with self.assertRaises(sqlite3.DatabaseError):
            self.indexer.resolve_review(review_id, "reviewer", "reason", after, self.luna_dir, self.upstage_dir)
        self.assertEqual(self.indexer.state.review(review_id)["status"], "open")
        self.assertEqual(self.indexer.state.status()["documents"][0]["status"], "review")

    def test_finalized_source_is_read_only_and_serving_is_writable(self) -> None:
        result = self.execute_indexer(); release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual((release / "manifest.json").stat().st_mode & 0o222, 0)
        self.assertEqual((release / "corpus.sqlite").stat().st_mode & 0o222, 0)
        self.assertEqual((release / "chroma").stat().st_mode & 0o222, 0)
        serving = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"] / "chroma"
        self.assertNotEqual(serving.stat().st_mode & 0o200, 0)
        import chromadb
        collection = chromadb.PersistentClient(path=str(serving)).get_collection("card_page_section_benefit")
        self.assertEqual(len(collection.get(include=[])["ids"]), 4)

    def test_serving_version_is_revalidated_and_never_replaced(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        marker = version / "version.json"
        before = (version.stat().st_ino, marker.read_bytes(), hashlib.sha256((version / "chroma/chroma.sqlite3").read_bytes()).hexdigest())
        self.execute_indexer()
        after = (version.stat().st_ino, marker.read_bytes(), hashlib.sha256((version / "chroma/chroma.sqlite3").read_bytes()).hexdigest())
        self.assertEqual(after, before)
        self.assertTrue((self.root / "runtime/serving/.locks" / f"{result['release_id']}.lock").is_file())
        self.assertEqual([path for path in version.parent.iterdir() if path.name.startswith(".")], [])

    def test_serving_version_tamper_is_blocked_without_replacement(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        marker = version / "version.json"
        marker_before, inode_before = marker.read_bytes(), version.stat().st_ino
        import chromadb

        client = chromadb.PersistentClient(path=str(version / "chroma"))
        collection = client.get_collection(manifest["strategy"])
        collection.delete(ids=[manifest["chunk_ids"][0]])
        del collection, client
        gc.collect()
        with self.assertRaisesRegex(RuntimeError, "stored embedding identity mismatch|content mismatch"):
            self.execute_indexer()
        self.assertEqual((version.stat().st_ino, marker.read_bytes()), (inode_before, marker_before))

    def test_concurrent_serving_materialization_converges_on_one_version(self) -> None:
        result = self.execute_indexer()
        release = self.root / "runtime/index-release" / result["release_id"]
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        version = self.root / "runtime/serving" / result["release_id"] / manifest["chroma_tree_sha256"]
        version.rename(version.with_name("prior-fixture-version"))
        approved = [row for row in self.indexer.state.documents(result["run_id"]) if row["status"] == "canonical_approved"]
        chunks, _ = self.indexer._chunks(result["run_id"], approved)
        embeddings = np.asarray(
            [self.indexer._fake_embedding(chunk["text"], manifest["embedding_dimension"]) for chunk in chunks],
            dtype=np.float32,
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            paths = list(executor.map(lambda _: self.indexer._materialize_serving(release, manifest, chunks, embeddings), range(2)))
        self.assertEqual(paths, [version, version])
        self.assertEqual(paths[0].stat().st_ino, paths[1].stat().st_ino)
        self.assertEqual([path for path in version.parent.iterdir() if path.name.startswith(f".{manifest['chroma_tree_sha256']}")], [])

    def test_test_only_run_status(self) -> None:
        self.assertEqual(self.execute_indexer()["status"]["run"]["status"], "test_only_published")

    def test_runtime_has_no_legacy_or_cross_lane_reference(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8") for path in PACKAGE_ROOT.rglob("*.py"))
        self.assertNotIn("note" + "books/", source)
        self.assertNotIn("snap" + "shot", source.casefold())
        self.assertNotIn("ki" + "wi", source.casefold())
        self.assertNotIn("m" + "mr", source.casefold())


if __name__ == "__main__":
    unittest.main()
