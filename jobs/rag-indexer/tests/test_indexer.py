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
    LaneRestructureRequired,
    _identity_evidence,
    OpenAIEmbeddingAdapter,
    compare_ocr_outputs,
    normalise_fact,
    relation_tuple,
    strict_resolution,
    validate_lane,
    validate_lanes_independently,
    validation_diagnostics,
    validation_summary,
    diagnostic_json_comparison,
    diagnose_lane,
    _list_marker_ranges,
)
from pickcardu_indexer.grounding import RELATION_FIELDS, STRUCTURE_SCHEMA_VERSION, normalized, typed_literals  # noqa: E402
from pickcardu_indexer.__main__ import parser as cli_parser, run_ocr  # noqa: E402
from pickcardu_indexer.ocr import OCR_PROMPT, OCR_SCHEMA, STRUCTURE_PROMPT, STRUCTURE_SCHEMA, LiveLaneAdapter, LunaFactStructurer, LunaOcrTranscriber, OcrProviderError, UpstageOcrTranscriber, pages_text, upstage_pages  # noqa: E402
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
    source_line = f"상품 안내: {quote}"
    raw_fields = {"benefit_type": "상품 안내", "action": "할인", "target": "카페", "condition": condition, "value": value, "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
    field_evidence = {
        field: ([] if not raw else [{"line_id": "P0001-L0003", "fragment": raw, "char_start": max(source_line.find(raw), 0), "char_end": max(source_line.find(raw), 0) + len(raw)}])
        for field, raw in raw_fields.items()
    }
    payload = {
        "document_id": document_id,
        "provider": provider,
        "source_pdf_sha256": SOURCE_SHA,
        "provenance": {"endpoint": "local-fixture", "model": f"{provider}-fixture", "config_hash": "fixture-v1"},
        "structure_schema_version": STRUCTURE_SCHEMA_VERSION,
        "identity": {"issuer_name": "Issuer", "card_name": "Card", "issuer_evidence": {"line_ids": ["P0001-L0001"]}, "card_evidence": {"line_ids": ["P0001-L0001"]}},
        "pages": [{"page": 1, "text": f"Issuer Card\n### 상품 안내\n상품 안내: {quote}"}],
        "ignored_risky_lines": [],
        "facts": [{**raw_fields, "field_evidence": field_evidence, "relation_scope": {"scope_type": "sentence", "line_ids": ["P0001-L0003"], "header_line_ids": [], "row_line_ids": []}}],
    }
    return payload


def line_id_lane(document_id: str, provider: str, *, condition: str = "monthly") -> dict[str, object]:
    quote = f"카페 {condition} 1% 할인"
    raw_fields = {"benefit_type": "카페", "action": "할인", "target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
    field_evidence = {
        field: ([] if not raw else [{"line_id": "P0001-L0002", "fragment": raw, "char_start": quote.index(raw), "char_end": quote.index(raw) + len(raw)}])
        for field, raw in raw_fields.items()
    }
    return {
        "document_id": document_id,
        "provider": provider,
        "source_pdf_sha256": SOURCE_SHA,
        "provenance": {"endpoint": "local-fixture", "model": f"{provider}-fixture", "config_hash": "fixture-v2"},
        "structure_schema_version": STRUCTURE_SCHEMA_VERSION,
        "identity": {
            "issuer_name": "Issuer",
            "card_name": "Card",
            "issuer_evidence": {"line_ids": ["P0001-L0001"]},
            "card_evidence": {"line_ids": ["P0001-L0001"]},
        },
        "pages": [{"page": 1, "text": f"Issuer Card\n{quote}"}],
        "facts": [{**raw_fields, "field_evidence": field_evidence, "relation_scope": {"scope_type": "sentence", "line_ids": ["P0001-L0002"], "header_line_ids": [], "row_line_ids": []}}],
        "ignored_risky_lines": [],
    }


def resolution_payload(luna: dict[str, object] | None = None, upstage: dict[str, object] | None = None, *, selected: str = "luna", selected_identity: str | None = None) -> dict[str, object]:
    luna = lane("issuer/card", "luna") if luna is None else luna
    upstage = lane("issuer/card", "upstage") if upstage is None else upstage
    selected_identity = selected if selected_identity is None else selected_identity
    validated = {"luna": validate_lane("luna", luna), "upstage": validate_lane("upstage", upstage)}
    keyed = {provider: {relation_tuple(item["fact"]): item for item in items} for provider, items in validated.items()}
    other = "upstage" if selected == "luna" else "luna"
    identity_evidence = {
        provider: _identity_evidence(provider, payload)
        for provider, payload in (("luna", luna), ("upstage", upstage))
    }
    identities = {
        provider: (normalized(payload["identity"]["issuer_name"]), normalized(payload["identity"]["card_name"]))
        for provider, payload in (("luna", luna), ("upstage", upstage))
    }
    selected_identity_pair = identities[selected_identity]
    payload = {
        "resolution": {"selected_provider": selected, "selected_identity_provider": selected_identity, "reason": "verified", "rejected_relations": []},
        "identity": {
            "issuer_name": selected_identity_pair[0],
            "card_name": selected_identity_pair[1],
            "evidence_refs": {
                provider: {
                    "issuer": identity_evidence[provider]["issuer"],
                    "card": identity_evidence[provider]["card"],
                    "supports_selected": identities[provider] == selected_identity_pair,
                }
                for provider in ("luna", "upstage")
            },
        },
        "canonical": [
            {"fact": item["fact"], "field_evidence_refs": {selected: item["field_evidence"], other: keyed[other].get(key, validated[other][0])["field_evidence"]}, "relation_scope_refs": {selected: item["relation_scope"], other: keyed[other].get(key, validated[other][0])["relation_scope"]}, "evidence_refs": {selected: {**item["evidence"], "supports_selected": True}, other: {**keyed[other].get(key, validated[other][0])["evidence"], "supports_selected": key in keyed[other]}}}
            for key, item in keyed[selected].items()
        ],
    }
    payload["resolution"]["rejected_relations"] = [
        {"provider": other, "tuple": list(relation_tuple(item["fact"])), "reason": "different grounded relation"}
        for key, item in keyed[other].items()
        if key not in keyed[selected]
    ]
    return payload


def add_relation(payload: dict[str, object], *, condition: str, quote: str) -> None:
    payload["pages"][0]["text"] += f"\n{quote}"
    line_id = f"P0001-L{len(payload['pages'][0]['text'].splitlines()):04d}"
    values = {"benefit_type": "카페", "action": "할인", "target": "카페", "condition": condition, "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
    payload["facts"].append({**values, "field_evidence": {field: ([] if not value else [{"line_id": line_id, "fragment": value, "char_start": quote.index(value), "char_end": quote.index(value) + len(value)}]) for field, value in values.items()}, "relation_scope": {"scope_type": "sentence", "line_ids": [line_id], "header_line_ids": [], "row_line_ids": []}})


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

    def score(self, _mode: str, _query: str, _documents: list[str], **_kwargs: object):
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
        source_line = "상품 안내: 카페 monthly 1% 할인"
        raw_fields = {"benefit_type": "상품 안내", "action": "할인", "target": "카페", "condition": "monthly", "value": "1", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        structured = {
            "structure_schema_version": STRUCTURE_SCHEMA_VERSION,
            "identity": {"issuer_name": "Issuer", "card_name": "Card", "issuer_evidence": {"line_ids": ["P0001-L0001"]}, "card_evidence": {"line_ids": ["P0001-L0001"]}},
            "facts": [{**raw_fields, "field_evidence": {field: ([] if not raw else [{"line_id": "P0001-L0002", "fragment": raw, "char_start": source_line.index(raw), "char_end": source_line.index(raw) + len(raw)}]) for field, raw in raw_fields.items()}, "relation_scope": {"scope_type": "sentence", "line_ids": ["P0001-L0002"], "header_line_ids": [], "row_line_ids": []}}],
            "ignored_risky_lines": [],
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
        luna_payload = lane(self.document_id, "luna")
        upstage_payload = lane(self.document_id, "upstage", condition="daily", quote="카페 daily 1% 할인")
        write_json(self.upstage_dir / "issuer__card.json", upstage_payload)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "resolution.json"
        payload = resolution_payload(luna_payload, upstage_payload)
        write_json(after, payload)
        return review_id, after

    def test_dual_lane_release_resume_and_pointer(self) -> None:
        result = self.execute_indexer()
        document = self.indexer.state.document(result["run_id"], self.document_id)
        canonical = json.loads(Path(document["canonical_path"]).read_text(encoding="utf-8"))
        self.assertEqual(canonical["structure_provider"], "luna")
        release_id = result["release_id"]
        self.assertIsInstance(release_id, str)
        self.assertEqual(result["status"]["run"]["status"], "test_only_published")
        release = self.root / "runtime/index-release" / str(release_id)
        manifest = json.loads((release / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["document_ids"], [self.document_id])
        self.assertEqual(manifest["coverage"], {"included_document_ids": [self.document_id], "omitted_document_ids": [], "partial": False})
        self.assertEqual(manifest["chunking_audit"][self.document_id]["mapped_facts"], 1)
        connection = sqlite3.connect(release / "corpus.sqlite")
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0], 5)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0], 5)
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
                expected_levels = {"structural"} if profile == "parent_child_bundle" else {"benefit", "section"}
                self.assertTrue(all(row["level"] in expected_levels for row in body["evidence"]))
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
        normalized_lane = json.loads((document_root / "luna" / "normalized.json").read_text(encoding="utf-8"))
        self.assertEqual(normalized_lane["normalization_errors"], [])
        self.assertEqual(normalized_lane["facts"][0]["typed_normalization"]["value"], [{"kind": "ratio", "decimal": "0.01"}])
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
        ocr_client = FakeResponsesClient([{"pages": [{"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []}]}])
        raw, pages, provenance = LunaOcrTranscriber("secret", client=ocr_client).transcribe(pdf)
        self.assertEqual(raw["id"], "response-1")
        self.assertEqual(pages_text(pages), "=== PAGE 1 ===\nIssuer Card\n")
        self.assertEqual(provenance["endpoint"], "openai.responses")
        self.assertEqual(provenance["page_fallback"]["candidate_policy"], "missing-failed-or-nonblank-empty-and-source-blank-v3")
        self.assertEqual(UpstageOcrTranscriber("fixture").parse_config["blank_labeling"], "empty-pages-only-v2")
        self.assertFalse(ocr_client.calls[0]["store"])
        self.assertEqual(ocr_client.calls[0]["text"]["format"]["name"], "ocr_pages")
        self.assertNotIn("max_output_tokens", ocr_client.calls[0])
        content = ocr_client.calls[0]["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], ["input_file", "input_text"])
        self.assertEqual(content[0]["detail"], "high")
        self.assertEqual(OCR_SCHEMA["properties"]["pages"]["items"]["required"], ["page_num", "status", "markdown", "uncertain_spans"])
        self.assertIn("병합된 셀", OCR_PROMPT)
        self.assertIn("로고만 있는 페이지", OCR_PROMPT)

        structured = FakeStructurer().structure("luna", [{"page": 1, "text": "Issuer Card\n상품 안내: 카페 monthly 1% 할인"}])[1]
        structure_client = FakeResponsesClient([structured])
        _, output = LunaFactStructurer("secret", client=structure_client).structure("luna", pages)
        self.assertEqual(output["identity"]["card_name"], "Card")
        self.assertEqual(structure_client.calls[0]["text"]["format"]["name"], "card_facts")
        self.assertEqual(structure_client.calls[0]["max_output_tokens"], 128_000)
        self.assertEqual(structure_client.calls[0]["timeout"], 1800.0)
        self.assertNotIn("upstage", structure_client.calls[0]["input"][0]["content"][0]["text"].casefold())
        self.assertIn("같은 혜택 블록이면 하나의 fact", STRUCTURE_PROMPT)
        self.assertIn("P0001-L0001", structure_client.calls[0]["input"][0]["content"][0]["text"])

        normalized = upstage_pages({"elements": [{"page": 1, "content": {"markdown": "Issuer Card"}}]}, 1)
        self.assertEqual(normalized[0]["text"], "Issuer Card")

    def test_luna_recovers_only_failed_page_from_200dpi_image(self) -> None:
        import pymupdf

        source = self.root / "page-fallback.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.new_page().insert_text((72, 72), "IBK기업은행")
        document.save(source)
        document.close()
        client = FakeResponsesClient([
            {"pages": [
                {"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []},
                {"page_num": 2, "status": "failed", "markdown": "", "uncertain_spans": [{"text": "page 2", "reason": "missing image"}]},
            ]},
            {"pages": [{"page_num": 2, "status": "success", "markdown": "IBK기업은행", "uncertain_spans": []}]},
        ])
        transcriber = LunaOcrTranscriber("secret", client=client, max_attempts=1)
        adapter = LiveLaneAdapter(
            "luna",
            {self.document_id: source},
            self.root / "page-fallback-cache",
            transcriber,
            FakeStructurer(),
        )

        path, envelope = adapter.extract(self.document_id)

        self.assertTrue(path.is_file())
        self.assertEqual(len(client.calls), 2)
        self.assertEqual([item["type"] for item in client.calls[0]["input"][0]["content"]], ["input_file", "input_text"])
        self.assertEqual([item["type"] for item in client.calls[1]["input"][0]["content"]], ["input_image", "input_text"])
        self.assertTrue(client.calls[1]["input"][0]["content"][0]["image_url"].startswith("data:image/png;base64,"))
        self.assertIn("Required page_num: 2", client.calls[1]["input"][0]["content"][1]["text"])
        self.assertEqual([(row["page"], row["text"]) for row in envelope["pages"]], [(1, "Issuer Card"), (2, "IBK기업은행")])
        self.assertEqual(envelope["pages"][1]["recovered_from"], "rendered_page_png_200dpi")
        self.assertEqual([row["page"] for row in envelope["page_fallbacks"]], [2])
        self.assertEqual(len(list(adapter._root(self.document_id, envelope["source_pdf_sha256"]).glob("page_fallbacks/*/page-0002/raw_response.*.json"))), 1)

    def test_luna_recovers_success_page_with_empty_text_when_source_is_not_blank(self) -> None:
        import pymupdf

        source = self.root / "empty-text-fallback.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(source)
        document.close()
        client = FakeResponsesClient([
            {"pages": [{"page_num": 1, "status": "success", "markdown": "", "uncertain_spans": []}]},
            {"pages": [{"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []}]},
        ])

        _, pages, _ = LunaOcrTranscriber("secret", client=client, max_attempts=1).transcribe(source)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[1]["input"][0]["content"][0]["type"], "input_image")
        self.assertEqual(pages[0]["text"], "Issuer Card")
        self.assertEqual(pages[0]["recovered_from"], "rendered_page_png_200dpi")

    def test_luna_adapter_recovers_cached_success_page_with_empty_text(self) -> None:
        import pymupdf

        source = self.root / "cached-empty-text-fallback.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(source)
        document.close()
        client = FakeResponsesClient([
            {"pages": [{"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []}]},
        ])
        transcriber = LunaOcrTranscriber("secret", client=client, max_attempts=1)
        adapter = LiveLaneAdapter(
            "luna",
            {self.document_id: source},
            self.root / "cached-empty-text-fallback-cache",
            transcriber,
            FakeStructurer(),
        )
        source_hash = adapter._context(self.document_id)[1]
        write_json(
            adapter._root(self.document_id, source_hash) / "raw_response.fixture.json",
            {
                "output": [{
                    "type": "message",
                    "content": [{
                        "type": "output_text",
                        "text": json.dumps({
                            "pages": [{"page_num": 1, "status": "success", "markdown": "", "uncertain_spans": []}],
                        }),
                    }],
                }],
            },
        )

        _, envelope = adapter.extract(self.document_id)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["input"][0]["content"][0]["type"], "input_image")
        self.assertEqual(envelope["pages"][0]["text"], "Issuer Card")
        self.assertEqual(envelope["pages"][0]["recovered_from"], "rendered_page_png_200dpi")
        fallback_root = adapter._root(self.document_id, source_hash) / "page_fallbacks" / str(adapter.page_fallback_config_hash)
        self.assertEqual(len(list(fallback_root.glob("page-0001/raw_response.*.json"))), 1)

    def test_luna_does_not_fallback_for_visually_blank_page(self) -> None:
        import pymupdf

        source = self.root / "blank-page.pdf"
        document = pymupdf.open()
        document.new_page()
        document.save(source)
        document.close()
        client = FakeResponsesClient([
            {"pages": [{"page_num": 1, "status": "success", "markdown": "", "uncertain_spans": []}]},
        ])

        _, pages, _ = LunaOcrTranscriber("secret", client=client, max_attempts=1).transcribe(source)

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0]["input"][0]["content"][0]["type"], "input_file")
        self.assertTrue(pages[0]["is_blank"])

    def test_luna_discards_hallucinated_text_on_visually_blank_page(self) -> None:
        import pymupdf

        source = self.root / "blank-page-with-hallucinated-text.pdf"
        document = pymupdf.open()
        page = document.new_page()
        page.draw_rect(page.rect, fill=(0.5, 0.5, 0.5), color=None)
        document.save(source)
        document.close()
        client = FakeResponsesClient([
            {"pages": [{"page_num": 1, "status": "success", "markdown": "2", "uncertain_spans": []}]},
        ])

        _, pages, _ = LunaOcrTranscriber("secret", client=client, max_attempts=1).transcribe(source)

        self.assertEqual(len(client.calls), 1)
        self.assertTrue(pages[0]["is_blank"])
        self.assertEqual(pages[0]["text"], "")

    def test_failed_page_fallback_never_resends_the_full_pdf(self) -> None:
        import pymupdf

        source = self.root / "failed-page-fallback.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.new_page().insert_text((72, 72), "IBK기업은행")
        document.save(source)
        document.close()

        class FailingPageClient(FakeResponsesClient):
            def create(self, **kwargs):
                if self.calls:
                    self.calls.append(kwargs)
                    raise RuntimeError("page request failed")
                return super().create(**kwargs)

        client = FailingPageClient([{"pages": [
            {"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []},
            {"page_num": 2, "status": "failed", "markdown": "", "uncertain_spans": [{"text": "page 2", "reason": "missing image"}]},
        ]}])
        transcriber = LunaOcrTranscriber("secret", client=client, max_attempts=1)
        adapter = LiveLaneAdapter(
            "luna",
            {self.document_id: source},
            self.root / "failed-page-fallback-cache",
            transcriber,
            FakeStructurer(),
        )

        with self.assertRaisesRegex(OcrProviderError, "page image fallback failed"):
            adapter.extract(self.document_id)

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0]["input"][0]["content"][0]["type"], "input_file")
        self.assertEqual(client.calls[1]["input"][0]["content"][0]["type"], "input_image")
        request_root = adapter._root(self.document_id, hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(len(list(request_root.glob("raw_response.*.json"))), 1)

    def test_provider_transport_retries_are_bounded(self) -> None:
        import httpx
        import openai
        import pymupdf

        pdf = self.root / "retry.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(pdf)
        document.close()

        class FlakyResponses(FakeResponsesClient):
            def create(self, **kwargs):
                if not self.calls:
                    self.calls.append(kwargs)
                    raise openai.APITimeoutError(request=httpx.Request("POST", "https://api.openai.com/v1/responses"))
                return super().create(**kwargs)

        luna_client = FlakyResponses([{"pages": [{"page_num": 1, "status": "success", "markdown": "Issuer Card", "uncertain_spans": []}]}])
        raw, count = LunaOcrTranscriber("secret", client=luna_client, sleeper=lambda _seconds: None).request(pdf)
        self.assertEqual((raw["id"], count, len(luna_client.calls)), ("response-2", 1, 2))

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps({"usage": {"pages": 1}, "elements": [{"page": 1, "content": {"markdown": "Issuer Card"}}]}).encode()

        calls = []

        def opener(_request, timeout):
            calls.append(timeout)
            if len(calls) == 1:
                raise TimeoutError("temporary")
            return Response()

        raw, count = UpstageOcrTranscriber("secret", opener=opener, sleeper=lambda _seconds: None).request(pdf)
        self.assertEqual((raw["usage"]["pages"], count, len(calls)), (1, 1, 2))

        class BadRequest(FakeResponsesClient):
            def create(self, **kwargs):
                self.calls.append(kwargs)
                error = RuntimeError("bad request")
                error.status_code = 400
                raise error

        bad_client = BadRequest([])
        with self.assertRaisesRegex(OcrProviderError, "bad request"):
            LunaOcrTranscriber("secret", client=bad_client, sleeper=lambda _seconds: None).request(pdf)
        self.assertEqual(len(bad_client.calls), 1)

    def test_upstage_normalization_preserves_layout_and_tables(self) -> None:
        pages = upstage_pages(
            {"usage": {"pages": 1}, "elements": [
                {"id": 1, "page": 1, "category": "heading1", "content": {"markdown": "# 혜택"}, "coordinates": [{"x": 0.1, "y": 0.1}, {"x": 0.8, "y": 0.2}]},
                {"id": 2, "page": 1, "category": "table", "content": {"markdown": "| 혜택 | 할인 |\n|---|---|\n| 편의점 | 10% |"}, "coordinates": [{"x": 0.1, "y": 0.3}, {"x": 0.9, "y": 0.8}]},
            ]},
            1,
        )
        self.assertEqual([block["reading_order"] for block in pages[0]["blocks"]], [1, 2])
        self.assertEqual(pages[0]["blocks"][1]["type"], "table")
        self.assertEqual(pages[0]["tables"][0]["table_id"], "2")
        self.assertEqual(pages[0]["coordinate_space"], "normalized_0_1")

    def test_upstage_layout_survives_staged_normalization(self) -> None:
        import pymupdf

        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "Issuer Card")
        document.save(self.source)
        document.close()
        raw = {"api": "2.0", "model": "document-parse-test", "usage": {"pages": 1}, "elements": [
            {"id": 7, "page": 1, "category": "table", "content": {"markdown": "Issuer Card\n상품 안내: 카페 monthly 1% 할인"}, "coordinates": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.8}]},
        ]}

        class LayoutUpstage(UpstageOcrTranscriber):
            def request(self, _source):
                return raw, 1

        sources = {self.document_id: self.source}
        luna, upstage, structurer = FakeTranscriber("luna"), LayoutUpstage("unused"), FakeStructurer()
        adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "layout-cache", luna, structurer),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "layout-cache", upstage, structurer),
        }
        config = {"mode": "layout-test", "luna": luna.config, "upstage": upstage.config, "upstage_parse": upstage.parse_config, "structure": structurer.config}
        for stage in ("extract", "structure", "normalize"):
            result = self.indexer.staged_ocr(self.manifest, config=config, providers=adapters, stage=stage)
        self.assertEqual(result["status"]["run"]["status"], "normalized")
        working = self.root / "runtime/working" / result["run_id"] / "documents/issuer__card/upstage"
        materialized = json.loads((working / "pages.json").read_text())
        normalized = json.loads((working / "normalized.json").read_text())
        self.assertEqual(materialized["pages"][0]["blocks"][0]["table_id"], "7")
        self.assertEqual(normalized["pages"][0]["tables"][0]["table_id"], "7")

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
        approved_document = self.indexer.state.document(validated["run_id"], self.document_id)
        canonical = json.loads(Path(approved_document["canonical_path"]).read_text(encoding="utf-8"))
        self.assertEqual(canonical["structure_provider"], "luna")

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

    def test_upstage_allows_missing_elements_only_for_visually_blank_source_pages(self) -> None:
        import pymupdf

        blank_source = self.root / "blank-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        document.new_page().insert_text((300, 800), "2")
        document.save(blank_source)
        document.close()
        raw = {"usage": {"pages": 2}, "elements": [{"page": 1, "content": {"markdown": "첫 페이지"}}]}

        parsed = UpstageOcrTranscriber("fixture").parse_source(raw, 2, blank_source)
        self.assertEqual([(row["page"], row["text"]) for row in parsed], [(1, "첫 페이지"), (2, "")])
        self.assertEqual([row["is_blank"] for row in parsed], [False, True])

        decorative_source = self.root / "decorative-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        page = document.new_page()
        page.draw_rect(pymupdf.Rect(20, 20, page.rect.width - 20, page.rect.height - 20), color=None, fill=(0, 0.2, 0.4))
        page.draw_line((0, 10), (10, 10))
        page.draw_line((page.rect.width - 10, page.rect.height - 10), (page.rect.width, page.rect.height - 10))
        document.save(decorative_source)
        document.close()
        parsed = UpstageOcrTranscriber("fixture").parse_source(raw, 2, decorative_source)
        self.assertEqual([(row["page"], row["text"]) for row in parsed], [(1, "첫 페이지"), (2, "")])
        self.assertEqual([row["is_blank"] for row in parsed], [False, True])

        content_source = self.root / "content-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        document.new_page().insert_text((72, 72), "둘째 페이지의 실제 내용")
        document.save(content_source)
        document.close()
        with self.assertRaisesRegex(ValueError, r"source pages: \[2\]"):
            UpstageOcrTranscriber("fixture").parse_source(raw, 2, content_source)

        sparse_image_source = self.root / "sparse-image-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        page = document.new_page()
        pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 10, 10), False)
        pixmap.clear_with(0)
        page.insert_image(pymupdf.Rect(72, 72, 82, 82), pixmap=pixmap)
        document.save(sparse_image_source)
        document.close()
        with self.assertRaisesRegex(ValueError, r"source pages: \[2\]"):
            UpstageOcrTranscriber("fixture").parse_source(raw, 2, sparse_image_source)

        sparse_vector_source = self.root / "sparse-vector-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        document.new_page().draw_rect(pymupdf.Rect(72, 72, 82, 82), color=None, fill=(0, 0, 0))
        document.save(sparse_vector_source)
        document.close()
        with self.assertRaisesRegex(ValueError, r"source pages: \[2\]"):
            UpstageOcrTranscriber("fixture").parse_source(raw, 2, sparse_vector_source)

        annotation_source = self.root / "annotation-second-page.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        annotation = document.new_page().add_rect_annot(pymupdf.Rect(72, 72, 82, 82))
        annotation.update()
        document.save(annotation_source)
        document.close()
        with self.assertRaisesRegex(ValueError, r"source pages: \[2\]"):
            UpstageOcrTranscriber("fixture").parse_source(raw, 2, annotation_source)

    def test_upstage_parse_policy_reuses_immutable_provider_raw_response(self) -> None:
        import pymupdf

        source = self.root / "blank-page-cache.pdf"
        document = pymupdf.open()
        document.new_page().insert_text((72, 72), "첫 페이지")
        document.new_page()
        document.save(source)
        document.close()
        raw = {"usage": {"pages": 2}, "elements": [{"page": 1, "content": {"markdown": "첫 페이지"}}]}

        class RecordingUpstage(UpstageOcrTranscriber):
            def __init__(self, policy: str) -> None:
                super().__init__("fixture")
                self.policy, self.calls = policy, 0

            @property
            def parse_config(self):
                return {**super().parse_config, "test_policy": self.policy}

            def request(self, _source: Path):
                self.calls += 1
                return raw, 2

        sources = {self.document_id: source}
        first = RecordingUpstage("v1")
        first_adapter = LiveLaneAdapter("upstage", sources, self.root / "parse-policy-cache", first, FakeStructurer())
        first_path, first_envelope = first_adapter.extract(self.document_id)
        self.assertEqual(first.calls, 1)

        second = RecordingUpstage("v2")
        second_adapter = LiveLaneAdapter("upstage", sources, self.root / "parse-policy-cache", second, FakeStructurer())
        second_path, second_envelope = second_adapter.extract(self.document_id)
        self.assertEqual(second.calls, 0)
        self.assertNotEqual(first_path, second_path)
        self.assertNotEqual(first_envelope["parse_config_hash"], second_envelope["parse_config_hash"])
        request_root = second_adapter._root(self.document_id, second_envelope["source_pdf_sha256"])
        self.assertEqual(len(list(request_root.glob("raw_response.*.json"))), 1)

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

    def test_luna_validation_failure_retries_once_and_preserves_failed_raw(self) -> None:
        import pymupdf

        source = self.root / "validation-retry.pdf"
        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(source)
        pdf.close()

        class RecoveringTranscriber(FakeTranscriber):
            validation_max_attempts = 2

            def parse(self, raw, _expected_count):
                if raw["request"] == 1:
                    raise ValueError("missing page")
                return raw["pages"]

        transcriber = RecoveringTranscriber("luna")
        adapter = LiveLaneAdapter("luna", {self.document_id: source}, self.root / "validation-retry", transcriber, FakeStructurer())
        _path, envelope = adapter.extract(self.document_id)

        self.assertEqual(transcriber.calls, 2)
        self.assertEqual(envelope["validation_attempts"], 2)
        request_root = adapter._root(self.document_id, envelope["source_pdf_sha256"])
        self.assertEqual(len(list((request_root / "failed_attempts").glob("*/raw_response.*.json"))), 1)
        self.assertEqual(len(list(request_root.glob("raw_response.*.json"))), 1)

    def test_legacy_ocr_records_failed_provider_attempts_in_state(self) -> None:
        import pymupdf

        pdf = pymupdf.open()
        pdf.new_page().insert_text((72, 72), "Issuer Card")
        pdf.save(self.source)
        pdf.close()

        class InvalidTranscriber(FakeTranscriber):
            validation_max_attempts = 2

            def parse(self, _raw, _expected_count):
                raise ValueError("invalid provider payload")

        sources = {self.document_id: self.source}
        luna = InvalidTranscriber("luna")
        adapters = {
            "luna": LiveLaneAdapter("luna", sources, self.root / "legacy-failure", luna, FakeStructurer()),
            "upstage": LiveLaneAdapter("upstage", sources, self.root / "legacy-failure", FakeTranscriber("upstage"), FakeStructurer()),
        }

        result = self.indexer.ocr(
            self.manifest,
            None,
            None,
            config={"mode": "legacy-failure-test"},
            providers=adapters,
        )

        self.assertEqual(result["status"]["run"]["status"], "blocked")
        self.assertEqual(luna.calls, 2)
        kinds = {
            row["kind"]
            for row in self.indexer.state.connection.execute(
                "SELECT kind FROM artifacts WHERE run_id=? AND document_id=? AND provider='luna'",
                (result["run_id"], self.document_id),
            )
        }
        self.assertTrue({
            "raw_response",
            "failed_raw_response_attempt-01",
            "failed_raw_response_attempt-02",
            "validation_error_attempt-01",
            "validation_error_attempt-02",
        }.issubset(kinds))

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
        risky["ignored_risky_lines"] = [{"line_id": "P0001-L0004", "reason": "omitted benefit"}]
        with self.assertRaises(LaneRestructureRequired):
            validate_lane("luna", risky)
        safe = lane(self.document_id, "luna")
        self.assertEqual(len(validate_lane("luna", safe)), 1)

    def test_risky_ignore_is_non_resolvable_restructure_block(self) -> None:
        for provider, root in (("luna", self.luna_dir), ("upstage", self.upstage_dir)):
            payload = lane(self.document_id, provider)
            payload["pages"][0]["text"] += "\n카페 10% 할인"
            payload["ignored_risky_lines"] = [{"line_id": "P0001-L0004", "reason": "omitted benefit"}]
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
            self.assertEqual(staged.call_args.kwargs["config"]["luna_page_fallback"], {})

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
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 daily 1% 할인"))
        with self.assertRaisesRegex(RuntimeError, "release blocked"):
            self.execute_indexer()
        status = self.indexer.state.status()
        self.assertEqual(status["documents"][0]["status"], "review")
        self.assertEqual(status["reviews"][0]["kind"], "relation_mismatch")

    def test_resolution_audits_and_revalidates(self) -> None:
        write_json(self.upstage_dir / "issuer__card.json", lane(self.document_id, "upstage", condition="daily", quote="카페 daily 1% 할인"))
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        after = self.root / "after.json"
        payload = resolution_payload(lane(self.document_id, "luna"), lane(self.document_id, "upstage", condition="daily", quote="카페 daily 1% 할인"))
        payload["resolution"]["reason"] = "selected grounded Luna relation"
        write_json(after, payload)
        self.indexer.resolve_review(review_id, "reviewer@example.test", "checked source evidence", after, self.luna_dir, self.upstage_dir)
        review = self.indexer.state.review(review_id)
        self.assertEqual(review["status"], "resolved")
        self.assertEqual(review["reviewer"], "reviewer@example.test")
        release_id = self.indexer.publish(review["run_id"], allow_partial=False, fake_vectors=True)
        self.assertTrue((self.root / "runtime/index-release" / release_id / "chroma").is_dir())

    def test_rule_and_grounding_failures_are_not_silenced(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing"):
            normalise_fact({"benefit_type": "할인", "action": "할인", "target": "x", "value": "", "unit": "원"})
        self.assertEqual(
            normalise_fact({"benefit_type": "할인", "action": "할인", "target": "온라인 예매", "exceptions": "제외"})["exceptions"],
            "제외",
        )
        with self.assertRaisesRegex(ValueError, "dash"):
            normalise_fact({"benefit_type": "할인", "action": "할인", "target": "x", "value": "-", "unit": "원"})
        with self.assertRaisesRegex(ValueError, "fragment"):
            validate_lane("luna", lane(self.document_id, "luna", value="2", quote="카페 monthly 1% 할인"))
        invalid = lane(self.document_id, "luna")
        invalid["facts"][0]["field_evidence"]["value"][0]["fragment"] = "없는 근거"
        with self.assertRaisesRegex(ValueError, "fragment"):
            validate_lane("luna", invalid)
        hallucinated = lane(self.document_id, "luna", value="캐시백", quote="카페 monthly % 할인")
        with self.assertRaisesRegex(ValueError, "fragment"):
            validate_lane("luna", hallucinated)

    def test_line_id_evidence_groups_one_benefit_and_lanes_validate_independently(self) -> None:
        payload = line_id_lane(self.document_id, "luna")
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        broken = json.loads(json.dumps(payload))
        broken["provider"] = "upstage"
        broken["facts"][0]["target"] = ""
        validated, errors = validate_lanes_independently({"luna": payload, "upstage": broken})
        self.assertEqual(len(validated["luna"]), 1)
        self.assertIn("upstage", errors)
        omitted = json.loads(json.dumps(payload))
        omitted["pages"][0]["text"] += "\n주유 5% 할인"
        with self.assertRaises(LaneRestructureRequired):
            validate_lane("luna", omitted)
        spoofed = json.loads(json.dumps(omitted))
        spoofed["ignored_risky_lines"] = [{"line_id": "P0001-L0005", "reason": "표 헤더"}]
        with self.assertRaises(ValueError):
            validate_lane("luna", spoofed)
        waiver = json.loads(json.dumps(payload))
        waiver["pages"][0]["text"] += "\n연회비 면제"
        waiver["ignored_risky_lines"] = [{"line_id": "P0001-L0005", "reason": "연회비 안내"}]
        with self.assertRaises(ValueError):
            validate_lane("luna", waiver)

    def test_line_id_lanes_auto_publish_and_review_resolution_preserves_evidence(self) -> None:
        luna = line_id_lane(self.document_id, "luna")
        upstage = line_id_lane(self.document_id, "upstage")
        write_json(self.luna_dir / "issuer__card.json", luna)
        write_json(self.upstage_dir / "issuer__card.json", upstage)
        result = self.execute_indexer()
        self.assertEqual(result["status"]["run"]["status"], "test_only_published")

        upstage = line_id_lane(self.document_id, "upstage", condition="daily")
        resolution = resolution_payload(luna, upstage)
        canonical, identity, _audit = strict_resolution(resolution, luna, upstage)
        self.assertEqual(canonical[0]["evidence_refs"]["luna"]["line_ids"], ["P0001-L0002"])
        self.assertEqual(identity["evidence_refs"]["upstage"]["card"]["page"], 1)

    def test_field_and_critical_span_coverage_fail_closed(self) -> None:
        invalid_condition = lane(self.document_id, "luna", quote="카페 1% 할인")
        invalid_condition["facts"][0]["field_evidence"]["condition"] = []
        with self.assertRaisesRegex(ValueError, "condition"):
            validate_lane("luna", invalid_condition)
        missing = lane(self.document_id, "luna")
        missing["pages"] = [{"page": 1, "text": "Issuer Card\n### 상품 안내\n상품 안내: 카페 monthly 1% 할인\n주유 daily 2% 할인"}]
        with self.assertRaises(LaneRestructureRequired):
            validate_lane("luna", missing)
        zero = lane(self.document_id, "luna", value="0", quote="카페 monthly 0% 할인")
        self.assertEqual(len(validate_lane("luna", zero)), 1)
        unscoped = lane(self.document_id, "luna")
        unscoped["facts"][0]["relation_scope"]["line_ids"] = ["P0001-L0001", "P0001-L0003"]
        self.assertEqual(len(validate_lane("luna", unscoped)), 1)

    def test_ocr_comparison_and_chunk_profiles_are_explicit(self) -> None:
        luna_payload = lane(self.document_id, "luna")
        upstage_payload = lane(self.document_id, "upstage")
        upstage_payload["pages"][0]["text"] += "\n추가 문구"
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
        ungrounded["identity"]["issuer_evidence"]["line_ids"] = ["P0001-L9999"]
        with self.assertRaisesRegex(ValueError, "identity is not grounded"):
            validate_lane("luna", ungrounded)
        mismatched = lane(self.document_id, "upstage")
        mismatched["identity"]["card_name"] = "OtherCard"
        mismatched["pages"][0]["text"] = mismatched["pages"][0]["text"].replace("Issuer Card", "Issuer Card OtherCard")
        mismatched["identity"]["card_evidence"] = {"line_ids": ["P0001-L0001"]}
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

    def test_development_unvalidated_chunk_preview_is_state_isolated(self) -> None:
        review_id, _after = self.open_relation_review()
        review = self.indexer.state.review(review_id)
        run_id = str(review["run_id"])
        before_document = dict(self.indexer.state.document(run_id, self.document_id))
        before_reviews = [dict(row) for row in self.indexer.state.reviews(run_id)]
        before_releases = list(self.indexer.state.status(run_id)["releases"])

        result = self.indexer.development_unvalidated_luna_chunk_preview(run_id)

        preview = Path(result["path"])
        manifest = json.loads((preview / "manifest.json").read_text(encoding="utf-8"))
        chunks = [json.loads(line) for line in (preview / "chunks.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(manifest["artifact_status"], "development_unvalidated")
        self.assertEqual(manifest["validation_assumption"], "pdf_pass_assumed_for_development_only")
        self.assertFalse(manifest["activation_eligible"])
        self.assertFalse(manifest["embedding_performed"])
        self.assertEqual(manifest["document_count"], 1)
        self.assertEqual({chunk["level"] for chunk in chunks}, {"card", "page", "section", "benefit"})
        self.assertEqual(dict(self.indexer.state.document(run_id, self.document_id)), before_document)
        self.assertEqual([dict(row) for row in self.indexer.state.reviews(run_id)], before_reviews)
        self.assertEqual(self.indexer.state.status(run_id)["releases"], before_releases)
        self.assertFalse((self.root / "runtime/active-index.json").exists())

        normalized_path = self.indexer.state.artifact_path(run_id, self.document_id, "normalized_json", "luna")
        stale_payload = json.loads(normalized_path.read_text(encoding="utf-8"))
        stale_payload["source_pdf_sha256"] = "0" * 64
        stale_path = self.root / "stale-luna-normalized.json"
        write_json(stale_path, stale_payload)
        stale_hash = hashlib.sha256(stale_path.read_bytes()).hexdigest()
        self.indexer.state.record_artifact(
            run_id, self.document_id, "normalized_json", "luna",
            str(stale_path), stale_hash, {},
        )
        with self.assertRaisesRegex(RuntimeError, "source PDF provenance mismatch"):
            self.indexer.development_unvalidated_luna_chunk_preview(run_id)

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
                payload = resolution_payload(luna, upstage)
                if kind == "mixed": payload["canonical"][0]["fact"]["condition"] = "daily"
                if kind == "wrong": payload["canonical"][0]["evidence_refs"]["luna"]["quote"] = "wrong"
                if kind == "false": payload["canonical"][0]["evidence_refs"]["upstage"]["supports_selected"] = False
                with self.assertRaises(ValueError): strict_resolution(payload, luna, upstage)

    def test_strict_resolution_requires_complete_selected_and_rejected_sets(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload(luna, upstage)
        canonical, identity, audit = strict_resolution(payload, luna, upstage)
        self.assertCountEqual([item["fact"]["condition"] for item in canonical], ["monthly", "weekly"])
        self.assertEqual(identity["card_name"], "Card")
        self.assertEqual(audit["resolution"]["rejected_relations"], payload["resolution"]["rejected_relations"])
        payload["resolution"]["rejected_relations"] = []
        with self.assertRaisesRegex(ValueError, "exactly equal"):
            strict_resolution(payload, luna, upstage)

    def test_strict_resolution_accepts_complete_upstage_selection(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        add_relation(luna, condition="weekly", quote="카페 weekly 1% 할인")
        add_relation(upstage, condition="daily", quote="카페 daily 1% 할인")
        payload = resolution_payload(luna, upstage, selected="upstage")
        canonical, _, _ = strict_resolution(payload, luna, upstage)
        # Bundle keys have their own deterministic order; array position is not
        # part of the selected facts' meaning or the resolution contract.
        self.assertCountEqual([item["fact"]["condition"] for item in canonical], ["monthly", "daily"])

    def test_upstage_identity_resolution_preserves_exact_provenance(self) -> None:
        luna, upstage = lane(self.document_id, "luna"), lane(self.document_id, "upstage")
        upstage["identity"]["card_name"] = "OtherCard"
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        payload = resolution_payload(luna, upstage, selected_identity="upstage")
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
        upstage["pages"][0]["text"] = upstage["pages"][0]["text"].replace("Issuer Card", "Issuer OtherCard")
        write_json(self.upstage_dir / "issuer__card.json", upstage)
        with self.assertRaises(RuntimeError):
            self.execute_indexer()
        review_id = self.indexer.state.status()["reviews"][0]["review_id"]
        payload = resolution_payload(lane(self.document_id, "luna"), upstage, selected_identity="upstage")
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
        self.assertEqual(len(collection.get(include=[])["ids"]), 5)

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


class FieldEvidenceV6Test(unittest.TestCase):
    def _lane(self) -> dict[str, object]:
        return line_id_lane("issuer/card", "luna")

    def test_source_witness_ignores_presentation_not_changed_claims(self) -> None:
        payload = self._lane()
        source = "카페 전월 실적 (자세한 내용은 안내 참고) 30만원 이상 10% 할인입니다"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact.update(dict.fromkeys(RELATION_FIELDS, ""))
        fact.update(benefit_type="할인 서비스", action="할인", target="카페",
                    condition="전월 실적 300000원 이상", value="10", unit="%")
        fragments = {"benefit_type": ["할인입니다"], "action": ["할인입니다"], "target": ["카페"],
                     "condition": ["30만원 이상", "전월 실적"], "value": ["10"], "unit": ["%"]}
        fact["field_evidence"] = {key: [{"line_id": "P0001-L0002", "fragment": text,
            "char_start": -1, "char_end": None} for text in fragments.get(key, [])] for key in RELATION_FIELDS}
        fact["relation_scope"] = {"scope_type": "unknown", "line_ids": ["P0001-L9999"],
                                  "header_line_ids": [], "row_line_ids": []}
        before = json.dumps(payload, sort_keys=True)
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        self.assertEqual(json.dumps(payload, sort_keys=True), before)
        changed = json.loads(before)
        changed["facts"][0]["value"] = "5"
        checks = diagnose_lane("luna", changed)["independent_checks"]
        condition_check = next(x for x in checks if x["check"] == "field_comparison" and x["field"] == "condition")
        self.assertEqual(condition_check["status"], "source_match")
        for key, value in (("target", "통신비"), ("action", "적립"), ("value", "5"),
                           ("condition", "전월 실적 500000원 이상"), ("condition", "전월 실적 300000원 초과")):
            changed = json.loads(before)
            changed["facts"][0][key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                validate_lane("luna", changed)
        payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("(자세한 내용은 안내 참고)", "제외")
        with self.assertRaises(ValueError):
            validate_lane("luna", payload)

    def test_non_numeric_condition_branches_must_not_disappear(self) -> None:
        for conjunction in ("또는", "및", "그리고", "or", "and"):
            payload = self._lane()
            condition = f"온라인 결제 {conjunction} 앱 결제 시"
            source = f"카페 {condition} 10% 할인"
            payload["pages"][0]["text"] = "Issuer Card\n" + source
            fact = payload["facts"][0]
            fact.update(dict.fromkeys(RELATION_FIELDS, ""))
            fact.update(benefit_type="할인", action="할인", target="카페", condition=condition, value="10", unit="%")
            fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
                "fragment": fact[key], "char_start": source.index(fact[key]),
                "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS}
            with self.subTest(conjunction=conjunction):
                self.assertEqual(len(validate_lane("luna", payload)), 1)
                for truncated in ("온라인 결제", f"온라인 결제 {conjunction}"):
                    fact["condition"] = truncated
                    fact["field_evidence"]["condition"][0].update(fragment=truncated, char_end=source.index(truncated) + len(truncated))
                    with self.assertRaises(ValueError):
                        validate_lane("luna", payload)

    def test_target_logical_operands_and_particles(self) -> None:
        for target in ("카페 또는 통신비", "카페 및 통신비"):
            payload = self._lane()
            source = f"{target} 10% 할인"
            payload["pages"][0]["text"] = "Issuer Card\n" + source
            fact = payload["facts"][0]
            fact.update(dict.fromkeys(RELATION_FIELDS, ""))
            fact.update(benefit_type="할인", action="할인", target=target, value="10", unit="%")
            fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
                "fragment": fact[key], "char_start": source.index(fact[key]),
                "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS}
            self.assertEqual(len(validate_lane("luna", payload)), 1)
            wrong_role = json.loads(json.dumps(payload))
            other = wrong_role["facts"][0]
            other["target"] = "카페"
            other["field_evidence"]["target"][0].update(fragment="카페", char_end=2)
            other["condition"] = target[3:]
            other["field_evidence"]["condition"] = [{"line_id": "P0001-L0002", "fragment": target[3:], "char_start": 3, "char_end": len(target)}]
            with self.assertRaises(ValueError):
                validate_lane("luna", wrong_role)
            fact["target"] = target.removesuffix(" 통신비")
            fact["field_evidence"]["target"][0].update(fragment=fact["target"], char_end=len(fact["target"]))
            with self.assertRaises(ValueError):
                validate_lane("luna", payload)

        payload = self._lane()
        source = "해당카드는 쿠폰서비스가 제공되지 않습니다."
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact.update(dict.fromkeys(RELATION_FIELDS, ""))
        fact.update(benefit_type="쿠폰서비스", action="제공되지 않습니다", target="쿠폰서비스", condition="해당카드")
        fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
            "fragment": fact[key], "char_start": source.index(fact[key]),
            "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS}
        self.assertEqual(len(validate_lane("luna", payload)), 1)

        source = "A 또는 B 등급 기본 서비스만 제공"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact.update(dict.fromkeys(RELATION_FIELDS, ""))
        fact.update(benefit_type="기본 서비스", target="A 또는 B 등급 기본 서비스", value="기본 서비스만", action="제공")
        fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
            "fragment": fact[key], "char_start": source.index(fact[key]),
            "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        fact["target"] = "A 또는"
        fact["field_evidence"]["target"][0].update(fragment="A 또는", char_end=4)
        with self.assertRaises(ValueError):
            validate_lane("luna", payload)

        source = "# 전월 이용실적 및 적립 제외 대상"
        payload["pages"][0]["text"] = "Issuer Card\n" + source + "\n상품권"
        fact.update(dict.fromkeys(RELATION_FIELDS, ""))
        fact.update(benefit_type="적립", target="상품권", condition="전월 이용실적 및 적립 제외 대상", action="제외")
        fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
            "fragment": fact[key], "char_start": source.index(fact[key]),
            "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS if key != "target"}
        fact["field_evidence"]["target"] = [{"line_id": "P0001-L0003", "fragment": "상품권", "char_start": 0, "char_end": 3}]
        self.assertEqual(len(validate_lane("luna", payload)), 1)

    def test_flattened_exclusion_roles_preserve_action_polarity(self) -> None:
        # An exclusion is one rule, not a second positive reward service.
        payload = self._lane()
        source = "상품권은 전월 실적 및 적립 제외 대상"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact.update(dict.fromkeys(RELATION_FIELDS, ""))
        fact.update(benefit_type="적립", target="상품권", condition="전월 실적 및 적립 제외 대상", action="제외")
        fact["field_evidence"] = {key: [] if not fact[key] else [{"line_id": "P0001-L0002",
            "fragment": fact[key], "char_start": source.index(fact[key]),
            "char_end": source.index(fact[key]) + len(fact[key])}] for key in RELATION_FIELDS}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        fact["action"] = "적립"
        fact["field_evidence"]["action"][0].update(fragment="적립", char_start=source.index("적립"), char_end=source.index("적립") + 2)
        with self.assertRaisesRegex(ValueError, "positive action cites a negated benefit"):
            validate_lane("luna", payload)

    def test_open_ended_units_bind_to_their_own_number(self) -> None:
        def lane(source, value, unit, action):
            payload = self._lane()
            payload["pages"][0]["text"] = "Issuer Card\n" + source
            fact = payload["facts"][0]
            fact.update(dict.fromkeys(RELATION_FIELDS, ""))
            fact.update(benefit_type=action, target="카페", action=action, value=value, unit=unit)
            fact["field_evidence"] = {field: [] for field in RELATION_FIELDS}
            for field in ("benefit_type", "target", "action", "value", "unit"):
                text = fact[field]
                start = source.index(text)
                fact["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": text,
                                                "char_start": start, "char_end": start + len(text)}]
            return payload

        for value, unit, action in (("10", "% M포인트", "적립"),
                                    ("5", "ALL 리워드 포인트", "적립"),
                                    ("2", "명", "이용"), ("1천", "원", "할인"),
                                    ("3~9", "%", "할인"), ("1만5천", "원", "할인")):
            source = f"카페 {value}{unit} {action}"
            payload = lane(source, value, unit, action)
            with self.subTest(unit=unit):
                self.assertEqual(len(validate_lane("luna", payload)), 1)
                wrong = json.loads(json.dumps(payload))
                wrong["facts"][0]["unit"] = "다른 단위"
                with self.assertRaises(ValueError):
                    validate_lane("luna", wrong)
                separated = json.loads(json.dumps(payload))
                separated["pages"][0]["text"] = "Issuer Card\n" + source.replace(value + unit, value + " 다른혜택 " + unit)
                with self.assertRaises(ValueError):
                    validate_lane("luna", separated)

        # Exact evidence must not make a predicate or multiple values a unit.
        for source, value, unit in (
            ("카페 10 다른혜택 적립 M포인트", "10", "다른혜택 적립 M포인트"),
            ("카페 10 다른혜택 적립 M포인트", "10 다른혜택 적립", "M포인트"),
            ("카페 10퍼센트 적립", "10퍼", "센트"),
            ("카페 10마일리지 적립", "10마일", "리지"),
            ("카페 10 20명 적립", "10 20", "명"),
            ("카페 10 20% 적립", "10 20", "%"),
        ):
            with self.subTest(source=source), self.assertRaises(ValueError):
                validate_lane("luna", lane(source, value, unit, "적립"))

    def test_ocr_and_json_share_numeric_equivalence_without_changing_source(self) -> None:
        payload = self._heading_table_lane()
        before_text = payload["pages"][0]["text"]
        for fact in payload["facts"]:
            fact["condition"] = fact["condition"].replace("30만원", "300000원").replace("50만원", "500000원")
            fact["cap"] = fact["cap"].replace("5천원", "5000원").replace("1만원", "10000원")
        validated = validate_lane("luna", payload)
        original = validate_lane("luna", self._heading_table_lane())
        self.assertEqual([relation_tuple(f["fact"]) for f in validated], [relation_tuple(f["fact"]) for f in original])
        self.assertEqual(payload["pages"][0]["text"], before_text)
        self.assertEqual(validated[0]["field_evidence"]["cap"]["fragments"][0]["fragment"], "5천원")
        for field, value in (("condition", "500000원 이상"), ("condition", "300000원 초과"),
                             ("cap", "50000원"), ("target", "통신비")):
            changed = json.loads(json.dumps(payload))
            changed["facts"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_lane("luna", changed)

    def test_value_unit_pair_is_compared_as_amount_not_separate_strings(self) -> None:
        payload = self._lane()
        source = "카페 monthly 1만원 할인"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact.update(value="10000", unit="원")
        for field, raw in (("value", "1"), ("unit", "만원")):
            fact["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": raw,
                "char_start": source.index(raw), "char_end": source.index(raw) + len(raw)}]
        result = validate_lane("luna", payload)
        self.assertEqual(result[0]["fact"]["typed_normalization"]["value"], [{"kind": "KRW", "decimal": "10000"}])
        for value, unit in (("1000", "원"), ("10000", "%"), ("10000", "만원")):
            changed = json.loads(json.dumps(payload))
            changed["facts"][0].update(value=value, unit=unit)
            with self.subTest(value=value, unit=unit), self.assertRaises(ValueError):
                validate_lane("luna", changed)

    def test_exclusion_lists_ignore_only_line_initial_bullets(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "청구할인 제외 항목", "- - 무이자할부 이용금액", "- 상품권 구매금액"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = dict.fromkeys(RELATION_FIELDS, "")
        values.update(benefit_type="청구할인", target="청구할인", action="제외", exceptions="무이자할부 이용금액 상품권 구매금액")
        fact.update(values)
        refs = {field: [] for field in RELATION_FIELDS}
        for field in ("benefit_type", "target", "action"):
            value = values[field]
            refs[field] = [{"line_id": "P0001-L0002", "fragment": value,
                "char_start": lines[1].index(value), "char_end": lines[1].index(value) + len(value)}]
        refs["exceptions"] = [{"line_id": f"P0001-L{n:04d}", "fragment": text,
            "char_start": lines[n - 1].index(text), "char_end": len(lines[n - 1])}
            for n, text in ((3, "무이자할부 이용금액"), (4, "상품권 구매금액"))]
        fact["field_evidence"] = refs
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0004"], "header_line_ids": [], "row_line_ids": []}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        for replacement in ("- 단, 상품권 구매금액", "- -5만원 상품권 구매금액", "| 상품권 구매금액"):
            changed = json.loads(json.dumps(payload))
            changed["pages"][0]["text"] = "\n".join([*lines[:3], replacement])
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                validate_lane("luna", changed)
        for labels in (('①', '②'), ('1.', '2.'), ('(1)', '(2)')):
            for include_labels in (False, True):
                changed = json.loads(json.dumps(payload))
                numbered_lines = [*lines[:2], '- ' + labels[0] + ' 무이자할부 이용금액', '- ' + labels[1] + ' 상품권 구매금액']
                changed['pages'][0]['text'] = '\n'.join(numbered_lines)
                for fragment in changed['facts'][0]['field_evidence']['exceptions']:
                    n = int(fragment['line_id'].split('L')[1])
                    fragment['char_start'] = numbered_lines[n - 1].index(fragment['fragment'])
                    fragment['char_end'] = len(numbered_lines[n - 1])
                if include_labels:
                    changed['facts'][0]['exceptions'] = labels[0] + ' 무이자할부 이용금액 ' + labels[1] + ' 상품권 구매금액'
                before = json.dumps(changed, ensure_ascii=False)
                with self.subTest(labels=labels, include_labels=include_labels):
                    self.assertEqual(len(validate_lane('luna', changed)), 1)
                    self.assertEqual(json.dumps(changed, ensure_ascii=False), before)
                    other = json.loads(json.dumps(changed)); other['provider'] = 'upstage'
                    other['facts'][0]['exceptions'] = payload['facts'][0]['exceptions']
                    outcomes, _, _ = validation_diagnostics({'luna': changed, 'upstage': other})
                    self.assertEqual(validation_summary({'luna': changed, 'upstage': other}, outcomes)['document_status'], 'pass')
        lost = json.loads(json.dumps(payload))
        lost["facts"][0]["action"] = "적용"
        with self.assertRaises(ValueError):
            validate_lane("luna", lost)

    def test_exclusion_preserved_inside_condition_is_not_lost_information(self) -> None:
        payload = self._lane()
        source = "카페 monthly 1% 할인 회원(가족회원 제외)에 한해"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact["condition"] = "회원(가족회원 제외)에 한해"
        fact["field_evidence"]["condition"] = [{"line_id": "P0001-L0002", "fragment": fact["condition"],
            "char_start": source.index("회원("), "char_end": len(source)}]
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        for condition in ("회원에 한해", "회원(가족회원 포함)에 한해"):
            changed = json.loads(json.dumps(payload))
            changed["facts"][0]["condition"] = condition
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                validate_lane("luna", changed)

    def test_negative_action_cannot_be_borrowed_from_another_target(self) -> None:
        payload = self._lane()
        source = "monthly 카페 할인, 통신비 제외"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        values = dict.fromkeys(RELATION_FIELDS, "")
        values.update(benefit_type="카페", target="카페 할인", action="제외", condition="monthly")
        fact.update(values)
        fact["field_evidence"] = {field: [] if not value else [{"line_id": "P0001-L0002", "fragment": value,
            "char_start": source.index(value), "char_end": source.index(value) + len(value)}] for field, value in values.items()}
        with self.assertRaisesRegex(ValueError, "multiple benefit"):
            validate_lane("luna", payload)

    def _heading_table_lane(self) -> dict[str, object]:
        payload = self._lane()
        lines = ["Issuer Card", "## 편의점 10% 할인", "| 전월 실적 | 월 한도 |",
                 "| --- | --- |", "| 30만원 이상 | 5천원 |", "| 50만원 이상 | 1만원 |"]
        payload["pages"][0]["text"] = "\n".join(lines)
        payload["facts"] = []
        for row, condition, cap in ((5, "30만원 이상", "5천원"), (6, "50만원 이상", "1만원")):
            values = dict.fromkeys(RELATION_FIELDS, "")
            values.update(benefit_type="편의점", target="편의점", action="할인", value="10", unit="%", condition=condition, cap=cap)
            evidence = {}
            for field, value in values.items():
                number = row if field in {"condition", "cap"} else 2
                source = lines[number - 1]
                evidence[field] = [] if not value else [{"line_id": f"P0001-L{number:04d}", "fragment": value,
                    "char_start": source.index(value), "char_end": source.index(value) + len(value)}]
            payload["facts"].append({**values, "field_evidence": evidence, "relation_scope": {
                "scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003", f"P0001-L{row:04d}"],
                "header_line_ids": ["P0001-L0003"], "row_line_ids": [f"P0001-L{row:04d}"]}})
        return payload

    def test_shared_heading_preserves_distinct_condition_cap_pairs_for_any_card(self) -> None:
        for document_id in ("issuer_a/card_a", "issuer_b/card_b", "new_issuer/new_card"):
            for provider in ("luna", "upstage"):
                with self.subTest(document_id=document_id, provider=provider):
                    payload = self._heading_table_lane()
                    payload.update(document_id=document_id, provider=provider)
                    before = json.dumps(payload, sort_keys=True)
                    facts = validate_lane(provider, payload)
                    self.assertEqual([(f["fact"]["condition"], f["fact"]["cap"]) for f in facts],
                                     [("30만원 이상", "5천원"), ("50만원 이상", "1만원")])
                    self.assertEqual(facts[0]["field_evidence"]["value"]["line_ids"], ["P0001-L0002"])
                    self.assertNotEqual(relation_tuple(facts[0]["fact"]), relation_tuple(facts[1]["fact"]))
                    self.assertEqual(json.dumps(payload, sort_keys=True), before)

    def test_shared_heading_never_guesses_plain_titles_competitors_or_exceptions(self) -> None:
        for replacement in ("편의점 10% 할인", "## 편의점 10% 할인 제외", "## 편의점 및 통신비 10% 할인"):
            payload = self._heading_table_lane()
            payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("## 편의점 10% 할인", replacement)
            with self.subTest(title=replacement), self.assertRaises(ValueError):
                validate_lane("luna", payload)
        for tail in ("\n추가 이용 조건 안내", "\n### 다른 혜택\n| 대상 | 한도 |\n| 카페 | 2천원 |",
                     "\n안내\n| 전월 실적 | 월 한도 |\n| 20만원 | 3천원 |"):
            payload = self._heading_table_lane()
            payload["pages"][0]["text"] += tail
            with self.subTest(tail=tail), self.assertRaises(ValueError):
                validate_lane("luna", payload)

    def test_shared_heading_does_not_replace_existing_value_column(self) -> None:
        payload = self._heading_table_lane()
        payload["pages"][0]["text"] = payload["pages"][0]["text"].replace(
            "| 전월 실적 | 월 한도 |", "| 전월 실적 | 월 한도 | 할인율 |"
        ).replace("| --- | --- |", "| --- | --- | --- |").replace(
            "| 30만원 이상 | 5천원 |", "| 30만원 이상 | 5천원 | 5% |"
        ).replace("| 50만원 이상 | 1만원 |", "| 50만원 이상 | 1만원 | 5% |")
        with self.assertRaisesRegex(ValueError, "matching data cell|table value material"):
            validate_lane("luna", payload)
        # Even choosing the row rate must not silently ignore the conflicting
        # title rate. The latter remains an unowned source number.
        for fact in payload["facts"]:
            row_id = fact["relation_scope"]["row_line_ids"][0]
            row_text = payload["pages"][0]["text"].splitlines()[int(row_id[-4:]) - 1]
            for field, value in (("value", "5"), ("unit", "%")):
                start = row_text.rindex("5%") + (field == "unit")
                fact[field] = value
                fact["field_evidence"][field] = [{"line_id": row_id, "fragment": value, "char_start": start, "char_end": start + 1}]
        with self.assertRaises(ValueError):
            validate_lane("luna", payload)

    def test_condition_cap_row_swaps_and_duplicate_rows_remain_blocked(self) -> None:
        payload = self._heading_table_lane()
        swapped = json.loads(json.dumps(payload))
        swapped["facts"][0]["cap"] = "1만원"
        swapped["facts"][0]["field_evidence"]["cap"] = swapped["facts"][1]["field_evidence"]["cap"]
        with self.assertRaisesRegex(ValueError, "escapes relation_scope|shared benefit heading|another table row|external rule applicability"):
            validate_lane("luna", swapped)
        duplicate = json.loads(json.dumps(payload))
        duplicate["facts"].append(json.loads(json.dumps(duplicate["facts"][0])))
        with self.assertRaisesRegex(ValueError, "reuse one broad"):
            validate_lane("luna", duplicate)

    def test_diagnostics_collect_all_fact_errors_without_partial_approval(self) -> None:
        luna = self._heading_table_lane()
        for fact in luna["facts"]:
            fact["value"] = "1"  # Both rows have incorrect structured values.
        upstage = self._heading_table_lane()
        upstage["provider"] = "upstage"
        outcomes, lanes, errors = validation_diagnostics({"luna": luna, "upstage": upstage})
        self.assertEqual(set(lanes), {"upstage"})
        diagnostic = outcomes["luna_text_to_json"]
        self.assertEqual(diagnostic["checked_fact_count"], 2)
        self.assertEqual([issue["fact_index"] for issue in diagnostic["issues"]], [0, 1])
        self.assertFalse(diagnostic["approval_eligible"])
        self.assertEqual(errors["luna"]["unresolved_fact_count"], 2)
        self.assertEqual({issue["category"] for issue in diagnostic["issues"]}, {"critical_content_mismatch"})
        unclear = self._heading_table_lane()
        unclear["pages"][0]["text"] = unclear["pages"][0]["text"].replace("## 편의점", "편의점")
        outcomes, _, _ = validation_diagnostics({"luna": unclear, "upstage": upstage})
        self.assertEqual({issue["category"] for issue in outcomes["luna_text_to_json"]["issues"]}, {"verification_unresolved"})

    def test_shared_heading_cannot_relabel_spending_requirement_as_discount_value(self) -> None:
        payload = self._heading_table_lane()
        title = "## 편의점 전월 실적 30만원 이상 할인"
        payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("## 편의점 10% 할인", title)
        for fact in payload["facts"]:
            fact.update(value="전월 실적 30만원 이상", unit="")
            for field in ("benefit_type", "target", "action", "value", "unit"):
                value = fact[field]
                fact["field_evidence"][field] = [] if not value else [{"line_id": "P0001-L0002", "fragment": value,
                    "char_start": title.index(value), "char_end": title.index(value) + len(value)}]
        with self.assertRaisesRegex(ValueError, "shared heading condition role|directly bound|one number"):
            validate_lane("luna", payload)

    def test_shared_heading_amount_must_bind_to_benefit_not_spending_action(self) -> None:
        for cue in ("이용시", "결제시", "사용시"):
            for action in ("할인", f"{cue} 할인"):
                payload = self._heading_table_lane()
                title = f"## 편의점 10만원 {cue} 할인"
                payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("## 편의점 10% 할인", title)
                for fact in payload["facts"]:
                    fact.update(value="10", unit="만원", action=action)
                    for field in ("benefit_type", "target", "action", "value", "unit"):
                        value = fact[field]
                        fact["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": value,
                            "char_start": title.index(value), "char_end": title.index(value) + len(value)}]
                with self.subTest(cue=cue, action=action), self.assertRaises(ValueError):
                    validate_lane("luna", payload)
                # The same spending cue must not be hidden in the value field.
                for fact in payload["facts"]:
                    fact.update(value=f"10만원 {cue}", unit="", action="할인")
                    for field in ("value", "unit", "action"):
                        value = fact[field]
                        fact["field_evidence"][field] = [] if not value else [{"line_id": "P0001-L0002", "fragment": value,
                            "char_start": title.index(value), "char_end": title.index(value) + len(value)}]
                with self.subTest(cue_in_value=cue), self.assertRaisesRegex(ValueError, "one number"):
                    validate_lane("luna", payload)
        for title in ("## 편의점 할인 10%", "## 편의점 10만원 할인"):
            payload = self._heading_table_lane()
            payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("## 편의점 10% 할인", title)
            for fact in payload["facts"]:
                if "만원" in title:
                    fact["unit"] = "만원"
                for field in ("benefit_type", "target", "action", "value", "unit"):
                    value = fact[field]
                    fact["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": value,
                        "char_start": title.index(value), "char_end": title.index(value) + len(value)}]
            with self.subTest(title=title):
                self.assertEqual(len(validate_lane("luna", payload)), 2)

    def test_fragment_gap_allows_heading_markup_but_not_hidden_content(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "카페 monthly 1% 할인", "전월 실적 30만원 이상", "## 50만원 미만"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        fact["condition"] = "전월 실적 30만원 이상 50만원 미만"
        fact["field_evidence"]["condition"] = [
            {"line_id": f"P0001-L{number:04d}", "fragment": text, "char_start": lines[number - 1].index(text),
             "char_end": lines[number - 1].index(text) + len(text)}
            for number, text in ((3, "전월 실적 30만원 이상"), (4, "50만원 미만"))]
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0004"], "header_line_ids": [], "row_line_ids": []}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        for text in ("## 제외 50만원 미만", "## -50만원 미만", "| 50만원 미만", "> 50만원 미만"):
            changed = json.loads(json.dumps(payload))
            changed["pages"][0]["text"] = "\n".join([*lines[:3], text])
            with self.subTest(text=text), self.assertRaises(ValueError):
                validate_lane("luna", changed)

    def test_fullwidth_percent_unit_preserves_source(self) -> None:
        payload = self._lane()
        payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("%", "％")
        payload["facts"][0]["field_evidence"]["unit"][0]["fragment"] = "％"
        result = validate_lane("luna", payload)
        self.assertEqual(result[0]["fact"]["unit"], "%")
        self.assertIn("％", result[0]["evidence"]["spans"][0]["quote"])

    def test_typed_numeric_dimensions_and_operators(self) -> None:
        self.assertEqual(typed_literals("30만원"), typed_literals("300000원"))
        self.assertEqual(typed_literals("300,000,000원"), [{"kind": "KRW", "decimal": "300000000"}])
        self.assertEqual(typed_literals("10.0%"), typed_literals("10%"))
        self.assertEqual(typed_literals("10%"), [{"kind": "ratio", "decimal": "0.1"}])
        self.assertNotEqual(typed_literals("10%"), typed_literals("1%"))
        self.assertNotEqual(typed_literals("1원"), typed_literals("1만원"))
        self.assertEqual(typed_literals("-10%"), [{"kind": "ratio", "decimal": "-0.1"}])
        fact = normalise_fact({"benefit_type": "할인", "action": "할인", "target": "카페", "condition": "전월 30만원 이상", "value": "10", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""})
        self.assertEqual(fact["typed_normalization"]["condition_operators"], ["gte"])
        comparable = {**fact, "value": "10.0"}
        self.assertEqual(relation_tuple(fact), relation_tuple(normalise_fact(comparable)))

    def test_schema_is_strict_and_all_object_properties_required(self) -> None:
        def walk(node: object) -> None:
            if not isinstance(node, dict):
                return
            if node.get("type") == "object":
                self.assertIs(node.get("additionalProperties"), False)
                self.assertEqual(set(node.get("properties", {})), set(node.get("required", [])))
            for value in node.values():
                if isinstance(value, list):
                    for item in value:
                        walk(item)
                else:
                    walk(value)
        walk(STRUCTURE_SCHEMA)

    def test_legacy_and_field_or_scope_swaps_do_not_autoapprove(self) -> None:
        legacy = self._lane()
        legacy.pop("structure_schema_version")
        with self.assertRaises(LaneRestructureRequired):
            validate_lane("luna", legacy)
        swapped = self._lane()
        swapped["facts"][0]["field_evidence"]["condition"][0]["fragment"] = "카페"
        with self.assertRaisesRegex(ValueError, "fragment|material claim"):
            validate_lane("luna", swapped)
        broad = self._lane()
        broad["facts"].append(json.loads(json.dumps(broad["facts"][0])))
        with self.assertRaisesRegex(ValueError, "reuse one broad"):
            validate_lane("luna", broad)
        ambiguous = self._lane()
        ambiguous["facts"][0]["relation_scope"]["scope_type"] = "unknown"
        self.assertEqual(len(validate_lane("luna", ambiguous)), 1)
        malformed = self._lane()
        malformed["facts"] = {}
        with self.assertRaisesRegex(ValueError, "non-empty facts arrays"):
            validate_lane("luna", malformed)

    def test_multi_benefit_and_uncovered_critical_lines_require_review(self) -> None:
        mixed = self._lane()
        source = "카페 10%, 통신비 5% 할인"
        mixed["pages"][0]["text"] = "Issuer Card\n" + source
        fact = mixed["facts"][0]
        fact["benefit_type"] = "카페"
        fact["action"] = "할인"
        fact["target"] = "카페"
        fact["condition"] = ""
        fact["value"] = "5"
        fact["unit"] = "%"
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": "P0001-L0002", "fragment": value, "char_start": source.index(value), "char_end": source.index(value) + len(value)}])
            for field, value in ((key, fact[key]) for key in ("benefit_type", "action", "target", "condition", "value", "unit", "cap", "frequency", "period", "exceptions"))
        }
        fact["relation_scope"] = {"scope_type": "sentence", "line_ids": ["P0001-L0002"], "header_line_ids": [], "row_line_ids": []}
        with self.assertRaisesRegex(ValueError, "multiple benefit candidates|source numeric range"):
            validate_lane("luna", mixed)
        missing = self._lane()
        missing["pages"][0]["text"] += "\n전월 실적 30만원 이상"
        with self.assertRaisesRegex(ValueError, "condition, cap, exception, or value"):
            validate_lane("luna", missing)

    def test_same_line_coordinate_recovery_is_unique_and_canonical(self) -> None:
        payload = self._lane()
        original = json.loads(json.dumps(payload))
        original_start = original["facts"][0]["field_evidence"]["value"][0]["char_start"]
        value_ref = payload["facts"][0]["field_evidence"]["value"][0]
        value_ref.update({"char_start": 0, "char_end": 1})
        supplied_before = dict(value_ref)
        validated = validate_lane("luna", payload)
        recovered = validated[0]["field_evidence"]["value"]["fragments"][0]
        self.assertEqual(recovered["char_start"], payload["pages"][0]["text"].splitlines()[1].index("1"))
        self.assertEqual(payload["facts"][0]["field_evidence"]["value"][0], supplied_before)
        self.assertEqual(original["facts"][0]["field_evidence"]["value"][0]["char_start"], original_start)

        formatted = self._lane()
        formatted["pages"][0]["text"] = "Issuer Card\n카페 monthly  1％ 할인"
        formatted_result = validate_lane("luna", formatted)
        self.assertEqual(formatted_result[0]["field_evidence"]["unit"]["fragments"][0]["fragment"], "％")

        ambiguous = self._lane()
        line = "카페 카페 monthly 1% 할인"
        ambiguous["pages"][0]["text"] = "Issuer Card\n" + line
        for field in ("benefit_type", "target"):
            ambiguous["facts"][0]["field_evidence"][field][0].update({"char_start": 1, "char_end": 2})
        with self.assertRaisesRegex(ValueError, "ambiguous repeated"):
            validate_lane("luna", ambiguous)

    def test_relation_key_ignores_layout_spacing_but_not_numeric_spacing(self) -> None:
        spaced = normalise_fact({"benefit_type": "할인", "action": "할인", "target": "카페", "condition": "전월 실적 30만원 이상", "value": "10", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""})
        compact = normalise_fact({**spaced, "condition": "전월실적 30만원 이상"})
        split_number = normalise_fact({**spaced, "condition": "전월실적 3 0만원 이상"})
        changed_sign = normalise_fact({**spaced, "value": "-10"})
        changed_operator = normalise_fact({**spaced, "condition": "전월실적 30만원 초과"})
        self.assertEqual(relation_tuple(spaced), relation_tuple(compact))
        self.assertNotEqual(relation_tuple(spaced), relation_tuple(split_number))
        self.assertNotEqual(relation_tuple(spaced), relation_tuple(changed_sign))
        self.assertNotEqual(relation_tuple(spaced), relation_tuple(changed_operator))

    def test_heading_plus_body_is_one_benefit_but_unmapped_benefit_is_not(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "### 청구할인 서비스", "편의점 매일할인 7%"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = {"benefit_type": "청구할인 서비스", "action": "할인", "target": "편의점", "condition": "매일", "value": "7", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        fact.update(values)
        line_for = {"benefit_type": 2, "action": 3, "target": 3, "condition": 3, "value": 3, "unit": 3}
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{line_for[field]:04d}", "fragment": value, "char_start": lines[line_for[field] - 1].index(value), "char_end": lines[line_for[field] - 1].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003"], "header_line_ids": [], "row_line_ids": []}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        unsafe = json.loads(json.dumps(payload))
        unsafe["pages"][0]["text"] += "\n연회비 면제"
        unsafe["facts"][0]["relation_scope"]["line_ids"].append("P0001-L0004")
        with self.assertRaisesRegex(ValueError, "multiple benefit candidates|lacks fact evidence"):
            validate_lane("luna", unsafe)

    def test_broad_benefit_type_cannot_absorb_competing_benefit_bodies(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "monthly", "카페 10% 할인", "통신비 5% 할인"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        fact.update({"benefit_type": "카페 10% 할인 통신비 5% 할인", "action": "할인", "target": "카페", "condition": "monthly", "value": "", "unit": "", "cap": "", "frequency": "", "period": "", "exceptions": ""})
        fact["field_evidence"] = {field: [] for field in RELATION_FIELDS}
        fact["field_evidence"].update({
            "benefit_type": [
                {"line_id": "P0001-L0003", "fragment": lines[2], "char_start": 0, "char_end": len(lines[2])},
                {"line_id": "P0001-L0004", "fragment": lines[3], "char_start": 0, "char_end": len(lines[3])},
            ],
            "action": [{"line_id": "P0001-L0003", "fragment": "할인", "char_start": lines[2].index("할인"), "char_end": len(lines[2])}],
            "target": [{"line_id": "P0001-L0003", "fragment": "카페", "char_start": 0, "char_end": 2}],
            "condition": [{"line_id": "P0001-L0002", "fragment": "monthly", "char_start": 0, "char_end": 7}],
        })
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0004"], "header_line_ids": [], "row_line_ids": []}
        with self.assertRaisesRegex(ValueError, "multiple benefit candidates|non-numeric field"):
            validate_lane("luna", payload)

        condition_absorbed = json.loads(json.dumps(payload))
        absorbed = condition_absorbed["facts"][0]
        absorbed["benefit_type"] = "할인"
        absorbed["condition"] = "monthly 카페 10% 할인 통신비 5% 할인"
        absorbed["field_evidence"]["benefit_type"] = [dict(absorbed["field_evidence"]["action"][0])]
        absorbed["field_evidence"]["condition"] = [
            {"line_id": f"P0001-L{line_number:04d}", "fragment": lines[line_number - 1], "char_start": 0, "char_end": len(lines[line_number - 1])}
            for line_number in (2, 3, 4)
        ]
        with self.assertRaisesRegex(ValueError, "multiple benefit candidates|wrong field role"):
            validate_lane("luna", condition_absorbed)

    def test_list_marker_between_field_fragments_is_format_only(self) -> None:
        payload = self._lane()
        source = "카페 monthly ※ daily 1% 할인"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        fact["condition"] = "monthly daily"
        fact["field_evidence"]["condition"] = [
            {"line_id": "P0001-L0002", "fragment": value, "char_start": source.index(value), "char_end": source.index(value) + len(value)}
            for value in ("monthly", "daily")
        ]
        for field in ("benefit_type", "action", "target", "value", "unit"):
            value = fact[field]
            fact["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": value, "char_start": source.index(value), "char_end": source.index(value) + len(value)}]
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        unsafe = json.loads(json.dumps(payload))
        unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("※", ">")
        with self.assertRaisesRegex(ValueError, "skip non-whitespace|material operator|predicate has unaccounted"):
            validate_lane("luna", unsafe)

    def test_noun_labels_cannot_hide_two_non_numeric_benefit_targets(self) -> None:
        lines = ["Issuer Card", "monthly", "카페 할인 대상", "통신비 할인 대상"]
        def ref(index, text):
            start = lines[index].index(text)
            return {"line_id": f"P0001-L{index + 1:04d}", "fragment": text,
                    "char_start": start, "char_end": start + len(text)}
        for absorbed_field in ("benefit_type", "condition"):
            payload = self._lane()
            payload["pages"][0]["text"] = "\n".join(lines)
            fact = payload["facts"][0]
            fact.update({key: "" for key in RELATION_FIELDS})
            fact.update(benefit_type="할인", action="할인", target="카페", condition="monthly")
            fact["field_evidence"] = {key: [] for key in RELATION_FIELDS}
            fact["field_evidence"].update(benefit_type=[ref(2, "할인")], action=[ref(2, "할인")],
                                          target=[ref(2, "카페")], condition=[ref(1, "monthly")])
            indices = (2, 3) if absorbed_field == "benefit_type" else (1, 2, 3)
            fact[absorbed_field] = " ".join(lines[i] for i in indices)
            fact["field_evidence"][absorbed_field] = [ref(i, lines[i]) for i in indices]
            fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0004"], "header_line_ids": [], "row_line_ids": []}
            with self.subTest(absorbed_field=absorbed_field), self.assertRaisesRegex(ValueError, "multiple benefit candidates"):
                validate_lane("luna", payload)

    def test_explicit_clock_range_is_condition_evidence(self) -> None:
        payload = self._lane()
        source = "카페 (오전11시~오후2시) 10% 할인"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        values = {"benefit_type": "할인", "action": "할인", "target": "카페", "condition": "오전11시~오후2시", "value": "10", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        fact = payload["facts"][0]
        fact.update(values)
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": "P0001-L0002", "fragment": value, "char_start": source.index(value), "char_end": source.index(value) + len(value)}])
            for field, value in values.items()
        }
        self.assertEqual(len(validate_lane("luna", payload)), 1)

    def test_common_context_tokens_are_not_money_or_blanket_exemptions(self) -> None:
        def make(context: str, field: str = "condition"):
            payload = self._lane()
            source = f"카페 {context} 7% 할인"
            payload["pages"][0]["text"] = "Issuer Card\n" + source
            fact = payload["facts"][0]
            values = {key: "" for key in fact["field_evidence"]}
            values.update(benefit_type="할인", action="할인", target="카페", value="7", unit="%")
            values[field] = context
            fact.update(values)
            fact["field_evidence"] = {
                key: ([] if not value else [{
                    "line_id": "P0001-L0002", "fragment": value,
                    "char_start": source.index("7%" if key == "value" else value),
                    "char_end": source.index("7%" if key == "value" else value) + len(value),
                }]) for key, value in values.items()
            }
            return payload

        for context, field in (
            ("09:00~18:00", "condition"), ("11시~14시", "condition"),
            ("오전 11시~오후 2시", "period"),
            ("2024년 2월 29일 이후 발급", "exceptions"),
            ("2024-02-29", "period"),
            ("고객센터 1588-1688", "exceptions"),
            ("전화 02-950-8510", "exceptions"),
        ):
            with self.subTest(context=context, field=field):
                payload = make(context, field)
                before = json.loads(json.dumps(payload))
                self.assertEqual(len(validate_lane("luna", payload)), 1)
                self.assertEqual(payload, before)
                payload["facts"][0][field] = context.replace("2", "3", 1) if "2" in context else context + " 추가"
                with self.assertRaises(ValueError):
                    validate_lane("luna", payload)
        for context in ("29:80~31:70", "오전23시", "2023년 2월 29일", "1588-1688", "문의 A-2006-0302-9227-00204"):
            with self.subTest(invalid=context), self.assertRaises(ValueError):
                validate_lane("luna", make(context, "exceptions"))
        for context in ("09:00~18:00 10%", "고객센터 1588-1688 10%"):
            with self.subTest(unrelated_rate=context), self.assertRaises(ValueError):
                validate_lane("luna", make(context, "exceptions"))

    def test_noun_labels_preserve_explanatory_rules_without_new_benefits(self) -> None:
        cases = [
            ("적립 대상이 중복인 경우 적립률이 높은 서비스 우선 적용",
             {"benefit_type": "적립률", "action": "우선 적용", "target": "적립률이 높은 서비스", "condition": "적립 대상이 중복인 경우"}),
            ("카페 할인/적립 서비스 제공",
             {"benefit_type": "카페", "action": "서비스 제공", "target": "카페", "value": "할인/적립"}),
        ]
        for source, values in cases:
            with self.subTest(source=source):
                payload = self._lane()
                payload["pages"][0]["text"] = "Issuer Card\n" + source
                fact = payload["facts"][0]
                values = {key: values.get(key, "") for key in fact["field_evidence"]}
                fact.update(values)
                fact["field_evidence"] = {
                    key: ([] if not value else [{"line_id": "P0001-L0002", "fragment": value,
                        "char_start": source.index(value), "char_end": source.index(value) + len(value)}])
                    for key, value in values.items()
                }
                self.assertEqual(len(validate_lane("luna", payload)), 1)

    def test_heading_relationship_is_structural_not_card_specific(self) -> None:
        for document_id in ("issuer_a/card_a", "issuer_b/card_b", "issuer_c/card_c"):
            payload = self._lane()
            payload["document_id"] = document_id
            lines = ["Issuer Card", "## 혜택", "안내", "### 카페", "monthly 7% 할인"]
            payload["pages"][0]["text"] = "\n".join(lines)
            fact = payload["facts"][0]
            fact.update(benefit_type="혜택", target="카페", condition="monthly", value="7", action="할인")
            positions = {"benefit_type": 2, "target": 4, "condition": 5, "value": 5, "action": 5, "unit": 5}
            fact["field_evidence"] = {
                key: ([] if not fact[key] else [{"line_id": f"P0001-L{positions[key]:04d}", "fragment": fact[key],
                    "char_start": lines[positions[key] - 1].index(fact[key]),
                    "char_end": lines[positions[key] - 1].index(fact[key]) + len(fact[key])}])
                for key in fact["field_evidence"]
            }
            fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0004", "P0001-L0005"], "header_line_ids": [], "row_line_ids": []}
            with self.subTest(document_id=document_id):
                self.assertEqual(len(validate_lane("luna", payload)), 1)
                payload["pages"][0]["text"] = payload["pages"][0]["text"].replace("### 카페", "## 카페")
                with self.assertRaisesRegex(ValueError, "sibling heading"):
                    validate_lane("luna", payload)

    def test_adjacent_provider_heading_markers_do_not_invent_a_section_conflict(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "# 카페", "# monthly 1% 할인"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        for field, refs in fact["field_evidence"].items():
            if not refs:
                continue
            number = 2 if field in {"benefit_type", "target"} else 3
            value = fact[field]
            fact["field_evidence"][field] = [{"line_id": f"P0001-L{number:04d}", "fragment": value,
                "char_start": lines[number - 1].index(value), "char_end": lines[number - 1].index(value) + len(value)}]
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003"], "header_line_ids": [], "row_line_ids": []}
        self.assertEqual(len(validate_lane("luna", payload)), 1)

    def test_table_common_benefit_title_uses_header_action_and_condition(self) -> None:
        payload = self._lane()
        title = "하나머니 적립 서비스: 지난달 실적과 관계없이 서비스 제공"
        header = "| 대상 | 적립률 |"
        separator = "| --- | --- |"
        row = "| 국내외 전 가맹점 | 1.0% |"
        lines = ["Issuer Card", title, header, separator, row]
        payload["pages"][0]["text"] = "\n".join(lines)
        values = {"benefit_type": "하나머니 적립 서비스", "action": "적립", "target": "국내외 전 가맹점", "condition": "지난달 실적과 관계없이", "value": "1.0", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        locations = {"benefit_type": (title, 2), "action": (header, 3), "target": (row, 5), "condition": (title, 2), "value": (row, 5), "unit": (row, 5)}
        fact = payload["facts"][0]
        fact.update(values)
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{locations[field][1]:04d}", "fragment": value, "char_start": locations[field][0].index(value), "char_end": locations[field][0].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0005"], "header_line_ids": ["P0001-L0003"], "row_line_ids": ["P0001-L0005"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)

    def test_table_row_accepts_adjacent_common_condition_only(self) -> None:
        payload = self._lane()
        common = "지난달 실적 30만원 이상"
        header = "| 대상 | 할인율 |"
        separator = "| --- | --- |"
        row = "| 카페 | 10% |"
        lines = ["Issuer Card", common, header, separator, row]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = {"benefit_type": "대상", "action": "할인율", "target": "카페", "condition": common, "value": "10", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        fact.update(values)
        locations = {"benefit_type": (header, 3), "action": (header, 3), "target": (row, 5), "condition": (common, 2), "value": (row, 5), "unit": (row, 5)}
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{locations[field][1]:04d}", "fragment": value, "char_start": locations[field][0].index(value), "char_end": locations[field][0].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0005"], "header_line_ids": ["P0001-L0003"], "row_line_ids": ["P0001-L0005"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        contaminated = json.loads(json.dumps(payload))
        contaminated["pages"][0]["text"] = "\n".join([*lines, "| 편의점 | 20% |"])
        contaminated["facts"][0]["relation_scope"]["line_ids"].append("P0001-L0006")
        with self.assertRaisesRegex(ValueError, "table context|multiple benefit|source numeric|lacks grounded relation"):
            validate_lane("luna", contaminated)

    def test_single_benefit_bounded_block_preserves_conditions_and_caps(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "편의점 10% 할인", "전월 실적 30만원 이상", "월 할인한도 5천원"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = {"benefit_type": "할인", "action": "할인", "target": "편의점", "condition": "전월 실적 30만원 이상", "value": "10", "unit": "%", "cap": "월 할인한도 5천원", "frequency": "", "period": "", "exceptions": ""}
        line_for = {"benefit_type": 2, "action": 2, "target": 2, "value": 2, "unit": 2, "condition": 3, "cap": 4}
        fact.update(values)
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{line_for[field]:04d}", "fragment": value, "char_start": lines[line_for[field] - 1].index(value), "char_end": lines[line_for[field] - 1].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "bounded_block", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0004"], "header_line_ids": [], "row_line_ids": []}
        validated = validate_lane("luna", payload)
        self.assertEqual(validated[0]["fact"]["condition"], values["condition"])
        self.assertEqual(validated[0]["fact"]["cap"], values["cap"])
        omitted = json.loads(json.dumps(payload))
        omitted_fact = omitted["facts"][0]
        for field in ("condition", "cap"):
            omitted_fact[field] = ""
            omitted_fact["field_evidence"][field] = []
        with self.assertRaisesRegex(ValueError, "marked condition|multiple benefit candidates|lacks fact evidence"):
            validate_lane("luna", omitted)
        swapped_roles = json.loads(json.dumps(payload))
        swapped_fact = swapped_roles["facts"][0]
        for field, other in (("condition", "cap"), ("cap", "condition")):
            swapped_fact[field] = values[other]
            source_line = line_for[other]
            swapped_fact["field_evidence"][field] = [{
                "line_id": f"P0001-L{source_line:04d}",
                "fragment": values[other],
                "char_start": lines[source_line - 1].index(values[other]),
                "char_end": lines[source_line - 1].index(values[other]) + len(values[other]),
            }]
        with self.assertRaisesRegex(ValueError, "marked condition|multiple benefit candidates"):
            validate_lane("luna", swapped_roles)
        unsafe = self._lane()
        unsafe_line = "카페 monthly 10% 할인"
        unsafe["pages"][0]["text"] = "Issuer Card\n" + unsafe_line
        unsafe["facts"][0]["value"] = "1"
        for field in ("benefit_type", "action", "target", "condition", "unit"):
            fragment = unsafe["facts"][0][field]
            start = unsafe_line.index(fragment)
            unsafe["facts"][0]["field_evidence"][field] = [{"line_id": "P0001-L0002", "fragment": fragment, "char_start": start, "char_end": start + len(fragment)}]
        start = unsafe_line.index("10")
        unsafe["facts"][0]["field_evidence"]["value"] = [{"line_id": "P0001-L0002", "fragment": "1", "char_start": start, "char_end": start + 1}]
        with self.assertRaisesRegex(ValueError, "source numeric range"):
            validate_lane("luna", unsafe)

    def test_named_table_notes_bind_by_unique_target_not_distance(self) -> None:
        payload = self._lane()
        lines = ['Issuer Card', '| 대상 | 할인율 | 한도 |', '| --- | --- | --- |',
                 '| 카페 | 10% | 월 1만원 |', '| 통신비 | 5% | 월 2만원 |',
                 '※ 카페 할인 대상: 오프라인 결제', '※ 통신비 할인 대상: 자동이체 결제']
        payload['pages'][0]['text'] = '\n'.join(lines)
        facts=[]
        for target, value, cap, condition, row, note in [('카페','10','월 1만원','오프라인 결제',4,6), ('통신비','5','월 2만원','자동이체 결제',5,7)]:
            f={field:'' for field in RELATION_FIELDS}
            f.update(benefit_type='할인율',action='할인율',target=target,value=value,unit='%',cap=cap,condition=condition)
            mapping={'benefit_type':2,'action':2,'target':row,'value':row,'unit':row,'cap':row,'condition':note}
            f['field_evidence']={key:[] if not val else [{'line_id':f'P0001-L{mapping[key]:04d}','fragment':val,'char_start':lines[mapping[key]-1].index(val),'char_end':lines[mapping[key]-1].index(val)+len(val)}] for key,val in f.items()}
            f['relation_scope']={'scope_type':'table_row','line_ids':[f'P0001-L{i:04d}' for i in (2,row,note)],'header_line_ids':['P0001-L0002'],'row_line_ids':[f'P0001-L{row:04d}']}
            facts.append(f)
        payload['facts']=facts
        self.assertEqual(len(validate_lane('luna',payload)),2)
        wrong=json.loads(json.dumps(payload))
        wrong['facts'][1]['condition']=facts[0]['condition']
        wrong['facts'][1]['field_evidence']['condition']=facts[0]['field_evidence']['condition']
        wrong['facts'][1]['relation_scope']['line_ids'][-1]='P0001-L0006'
        with self.assertRaises(ValueError):validate_lane('luna',wrong)
        duplicate=json.loads(json.dumps(payload))
        duplicate['pages'][0]['text']=duplicate['pages'][0]['text'].replace('| 카페 |','| 통신비 |')
        with self.assertRaises(ValueError):validate_lane('luna',duplicate)

    def test_provider_fragment_order_and_presentation_do_not_change_fact(self) -> None:
        payload=self._lane()
        source='카페 전월 실적은 30만원 이상 10% 할인입니다'
        values={'benefit_type':'할인','action':'할인입니다','target':'카페','condition':'전월 실적 30만원 이상','value':'10','unit':'%','cap':'','frequency':'','period':'','exceptions':''}
        payload['pages'][0]['text']='Issuer Card\n'+source
        f=payload['facts'][0];f.update(values)
        def ref(text):
            start=source.index(text)
            return {'line_id':'P0001-L0002','fragment':text,'char_start':start,'char_end':start+len(text)}
        f['field_evidence']={key:[] if not value else [ref(value)] for key,value in values.items() if key!='condition'}
        f['field_evidence']['condition']=[ref('30만원 이상'),ref('전월 실적')]
        f['relation_scope']={'scope_type':'sentence','line_ids':['P0001-L0002'],'header_line_ids':[],'row_line_ids':[]}
        self.assertEqual(len(validate_lane('luna',payload)),1)
        f['condition']='전월 실적 30만원 초과'
        with self.assertRaises(ValueError):validate_lane('luna',payload)

    def test_table_scope_requires_exactly_one_header_and_one_row(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "| 대상 | 할인율 |", "| 카페 | 10% |"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = {"benefit_type": "대상", "action": "할인율", "target": "카페", "condition": "", "value": "10", "unit": "%", "cap": "", "frequency": "", "period": "", "exceptions": ""}
        line_for = {"benefit_type": 2, "action": 2, "target": 3, "value": 3, "unit": 3}
        fact.update(values)
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{line_for[field]:04d}", "fragment": value, "char_start": lines[line_for[field] - 1].index(value), "char_end": lines[line_for[field] - 1].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003"], "header_line_ids": ["P0001-L0002"], "row_line_ids": ["P0001-L0003"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        payload["facts"][0]["relation_scope"]["row_line_ids"] = ["P0001-L0002", "P0001-L0003"]
        with self.assertRaisesRegex(ValueError, "one header and one row"):
            validate_lane("luna", payload)
        crossed = self._lane()
        crossed["pages"][0]["text"] = "Issuer Card\n| 대상 | 할인율 |\n| 카페 | 10% |"
        crossed_fact = crossed["facts"][0]
        crossed_fact.update(values)
        crossed_fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{line_for[field]:04d}", "fragment": value, "char_start": lines[line_for[field] - 1].index(value), "char_end": lines[line_for[field] - 1].index(value) + len(value)}])
            for field, value in values.items()
        }
        crossed_fact["field_evidence"]["value"][0]["line_id"] = "P0001-L0002"
        crossed_fact["field_evidence"]["value"][0]["fragment"] = "할인율"
        crossed_fact["field_evidence"]["value"][0]["char_start"] = lines[1].index("할인율")
        crossed_fact["field_evidence"]["value"][0]["char_end"] = lines[1].index("할인율") + len("할인율")
        crossed_fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003"], "header_line_ids": ["P0001-L0002"], "row_line_ids": ["P0001-L0003"]}
        with self.assertRaisesRegex(ValueError, "reconstruct|data row|material claim"):
            validate_lane("luna", crossed)

    def test_all_four_diagnostics_complete_when_one_lane_is_invalid(self) -> None:
        luna, upstage = self._lane(), line_id_lane("issuer/card", "upstage")
        luna["facts"][0]["field_evidence"]["condition"] = []
        outcomes, lanes, errors = validation_diagnostics({"luna": luna, "upstage": upstage})
        self.assertEqual(set(outcomes), {"ocr_comparison", "luna_text_to_json", "upstage_text_to_json", "normalized_json_diagnostic_comparison"})
        self.assertIn("luna", errors)
        self.assertIn("upstage", lanes)

    def test_list_markers_never_remove_rates_durations_or_inline_references(self) -> None:
        registry = {f'P0001-L{i:04d}': (1, text) for i, text in enumerate(
            ['1.5% 할인', '2.5% 할인', '1일 1회', '2개월', '1+1 행사', '-10% 변동', '혜택 ① 적용', '혜택 ② 제외'], 1)}
        self.assertEqual(_list_marker_ranges(registry), {})

    def test_role_bundles_preserve_label_values_and_deadline_meaning(self) -> None:
        def make(source, values):
            payload = self._lane()
            payload['pages'][0]['text'] = 'Issuer Card\n' + source
            fact = payload['facts'][0]
            fact.update({field: values.get(field, '') for field in RELATION_FIELDS})
            fact['field_evidence'] = {
                field: [] if not fact[field] else [{'line_id': 'P0001-L0002', 'fragment': fact[field],
                    'char_start': source.index(fact[field]), 'char_end': source.index(fact[field]) + len(fact[field])}]
                for field in RELATION_FIELDS}
            return payload

        source = '해외 결제금액 1% 포인트 적립'
        payload = make(source, dict(benefit_type=source, target='해외 결제금액', action='포인트 적립', value='1', unit='%'))
        payload['facts'][0]['benefit_type'] = '해외 결제금액 포인트 적립'
        before = json.loads(json.dumps(payload))
        self.assertEqual(len(validate_lane('luna', payload)), 1)
        self.assertEqual(payload, before)
        wrong = json.loads(json.dumps(payload)); wrong['facts'][0]['action'] = '할인'
        with self.assertRaises(ValueError):
            validate_lane('luna', wrong)

        # Equal digits from a DIFFERENT occurrence cannot justify label loss.
        wrong = json.loads(json.dumps(payload))
        wrong['pages'][0]['text'] += ' 별도 1% 포인트 적립'
        for field in ('value', 'unit'):
            ref = wrong['facts'][0]['field_evidence'][field][0]
            at = wrong['pages'][0]['text'].splitlines()[1].rindex(ref['fragment'])
            ref.update(char_start=at, char_end=at + len(ref['fragment']))
        with self.assertRaises(ValueError):
            validate_lane('luna', wrong)

        source = '연회비 10영업일 이내에 반환'
        payload = make(source, dict(benefit_type='연회비', target='연회비', action='반환', period='10영업일 이내에'))
        payload['facts'][0]['period'] = '10영업일 이내'
        self.assertEqual(len(validate_lane('luna', payload)), 1)
        for wrong in ('10일 이내', '10영업일 이후'):
            payload['facts'][0]['period'] = wrong
            with self.assertRaises(ValueError):
                validate_lane('luna', payload)

        for suffix in ('서비스', '혜택'):
            source = f'카페 할인 {suffix} 통신비 할인 적용 1%'
            payload = make(source, dict(benefit_type=f'카페 할인 {suffix}',
                condition=f'할인 {suffix}', target='통신비', action='할인 적용', value='1', unit='%'))
            with self.assertRaises(ValueError):
                validate_lane('luna', payload)

        source = '카페 할인 서비스 카페 적용 monthly 1%'
        payload = make(source, dict(benefit_type='카페 할인 서비스', target='카페',
            action='할인', condition='monthly', value='1', unit='%'))
        payload['facts'][0]['benefit_type'] = '할인 서비스'
        ref = payload['facts'][0]['field_evidence']['target'][0]
        ref.update(char_start=source.rindex('카페'), char_end=source.rindex('카페') + 2)
        # A repeated name for the same benefit is not a wrong relationship.
        self.assertEqual(len(validate_lane('luna', payload)), 1)

    def test_bundle_values_do_not_override_unresolved_relationship(self) -> None:
        luna, upstage = self._lane(), self._lane()
        # Evidence values remain equal, but the condition is on another page
        # with no established applicability. An unknown scope label alone is
        # only a hint defect, not an unresolved actual relationship.
        luna['pages'].append({'page': 2, 'text': 'monthly'})
        luna['facts'][0]['field_evidence']['condition'] = [
            {'line_id': 'P0002-L0001', 'fragment': 'monthly', 'char_start': 0, 'char_end': 7}]
        outcomes, _, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        checks = outcomes['luna_text_to_json']['independent_checks']
        self.assertTrue(any(x['check'] == 'value_bundle' and x['bundle'] == 'benefit' and x['status'] == 'source_match' for x in checks))
        self.assertEqual(validation_summary({'luna': luna, 'upstage': upstage}, outcomes)['document_status'], 'review')

    def test_json_alignment_is_order_independent_and_review_pair_is_not_equality(self) -> None:
        luna = self._heading_table_lane()
        upstage = json.loads(json.dumps(luna)); upstage['provider'] = 'upstage'; upstage['facts'].reverse()
        result = diagnostic_json_comparison(luna, upstage)
        self.assertEqual(result['comparison_status'], 'pass')
        self.assertEqual(result['comparisons'][0]['upstage_fact_index'], 1)
        for fact in upstage['facts']:
            fact['value'] = '99'
        result = diagnostic_json_comparison(luna, upstage)
        self.assertEqual(result['comparison_status'], 'review')
        self.assertTrue(any(x['match'] == 'ambiguous' for x in result['comparisons']))
        one = lane('issuer/card', 'luna'); two = lane('issuer/card', 'upstage', value='5')
        result = diagnostic_json_comparison(one, two)
        self.assertEqual(result['comparison_status'], 'review')
        self.assertEqual(result['comparisons'][0]['match'], 'paired_for_review')
        diff = next(d for d in result['comparisons'][0]['differences'] if d['field'] == 'value')
        self.assertEqual((diff['luna'], diff['upstage']), ('1', '5'))
        self.assertTrue(diff['luna_locations'][0]['location_verified'])

    def test_three_checks_do_not_conflate_json_agreement_with_grounding(self) -> None:
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage')
        for payload in (luna, upstage): payload['facts'][0]['value'] = '5'
        outcomes, _, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        result = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)
        self.assertEqual(result['checks']['1_json_to_json']['status'], 'pass')
        self.assertFalse(result['checks']['1_json_to_json']['grounding_confirmed'])
        self.assertEqual(result['checks']['2_ocr_to_json']['status'], 'review')
        self.assertEqual(result['document_status'], 'review')
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage', value='5')
        outcomes, _, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        result = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)
        self.assertEqual(result['checks']['1_json_to_json']['status'], 'review')
        self.assertEqual(result['checks']['2_ocr_to_json']['status'], 'pass')
        self.assertTrue(result['checks']['3_ocr_to_ocr']['auxiliary'])
        self.assertEqual(result['document_status'], 'review')

    def test_ocr_format_difference_alone_does_not_reject_pdf(self) -> None:
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage')
        upstage['pages'][0]['text'] += '\n추가 설명'
        outcomes, _, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        result = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)
        self.assertEqual(result['checks']['3_ocr_to_ocr']['status'], 'different')
        self.assertEqual(result['document_status'], 'pass')

    def test_identity_and_target_errors_do_not_hide_other_field_mismatches(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["identity"]["card_name"] = "Unknown card"
        fact = luna["facts"][0]
        fact["target"] = "편의점"
        fact["value"] = "5"
        outcomes, lanes, errors = validation_diagnostics({"luna": luna, "upstage": upstage})
        result = outcomes["luna_text_to_json"]
        self.assertFalse(result["approval_eligible"])
        self.assertEqual(set(lanes), {"upstage"})
        checks = result["independent_checks"]
        self.assertTrue(any(c["check"] == "identity.card" and c["status"] == "review" for c in checks))
        fields = {c["field"]: c for c in checks if c["check"] == "field_comparison"}
        self.assertEqual(fields["target"]["status"], "review")
        self.assertEqual(fields["value"]["category"], "critical_content_mismatch")
        self.assertEqual(fields["condition"]["status"], "source_match")
        self.assertFalse(fields["condition"]["relationship_proven"])
        self.assertIn("luna", errors)

    def test_scope_failure_does_not_hide_fields_or_unclaimed_lines(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["facts"][0]["relation_scope"]["scope_type"] = "unknown"
        luna["facts"][0]["value"] = "5"
        luna["pages"][0]["text"] += "\n통신비 10% 할인"
        outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
        checks = outcomes["luna_text_to_json"]["independent_checks"]
        self.assertTrue(any(c.get("field") == "value" and c.get("category") == "critical_content_mismatch" for c in checks))
        self.assertTrue(any(c["check"] == "unclaimed_source_line" and c["line_id"] == "P0001-L0004" for c in checks))
        self.assertTrue(any(c["check"] == "declared_coverage" and c["status"] == "review" for c in checks))
        self.assertNotIn("luna", lanes)

    def test_missing_value_evidence_skips_only_dependent_unit_comparison(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["facts"][0]["field_evidence"]["value"] = []
        outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
        checks = outcomes["luna_text_to_json"]["independent_checks"]
        self.assertTrue(any(c.get("field") == "unit" and c["status"] == "not_checked" for c in checks))
        self.assertTrue(any(c.get("field") == "condition" and c["status"] == "source_match" for c in checks))
        self.assertNotIn("luna", lanes)

    def test_values_relationships_and_coverage_are_independent_not_approvals(self) -> None:
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage')
        luna['facts'][0]['value'] = '5'  # Correct source locations, wrong JSON amount.
        outcomes, lanes, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        summary = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)
        groups = summary['checks']['2_ocr_to_json']['providers']['luna']['groups']
        self.assertEqual(groups['A_values']['status'], 'review')
        self.assertEqual(groups['B_relationships']['status'], 'pass')
        self.assertEqual(groups['C_coverage']['status'], 'pass')
        self.assertEqual(summary['document_status'], 'review')
        self.assertNotIn('luna', lanes)
        luna = lane('issuer/card', 'luna')
        luna['pages'][0]['text'] += '\n통신비 10% 할인'
        outcomes, lanes, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        groups = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)['checks']['2_ocr_to_json']['providers']['luna']['groups']
        self.assertEqual(groups['A_values']['status'], 'pass')
        self.assertEqual(groups['B_relationships']['status'], 'pass')
        self.assertEqual(groups['C_coverage']['status'], 'review')
        self.assertNotIn('luna', lanes)

    def test_presentation_recovery_uses_original_offsets_and_rejects_ambiguity(self) -> None:
        from pickcardu_indexer.pipeline import _same_line_fragment_range
        source = '혜택: ‘나한테 진심’ CU・GS25'
        start, end = _same_line_fragment_range(source, "'나한테 진심' CU·GS25", 0, 0)
        self.assertEqual(source[start:end], '‘나한테 진심’ CU・GS25')
        with self.assertRaises(ValueError):
            _same_line_fragment_range('‘A’ ‘A’', "'A'", 0, 0)

    def test_coverage_inventories_only_resolved_field_references_outside_scope(self) -> None:
        for valid in (True, False):
            payload = lane('issuer/card', 'luna')
            payload['pages'].append({'page': 2, 'text': '온라인 제외'})
            fact = payload['facts'][0]
            fact['exceptions'] = '온라인 제외'
            fact['field_evidence']['exceptions'] = [{'line_id': 'P0002-L0001',
                'fragment': '온라인 제외' if valid else '존재하지 않는 내용', 'char_start': 0, 'char_end': 6}]
            result = diagnose_lane('luna', payload)
            self.assertFalse(result['approval_eligible'])  # Applicability still unresolved.
            missing = [x for x in result['independent_checks'] if x['check'] == 'unclaimed_source_line' and x['line_id'] == 'P0002-L0001']
            self.assertEqual(bool(missing), not valid)

    def test_scope_declaration_cannot_hide_unquoted_critical_condition(self) -> None:
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage')
        luna['pages'][0]['text'] += '\n전월실적 30만원 이상'
        luna['facts'][0]['relation_scope']['line_ids'].append('P0001-L0004')
        outcomes, lanes, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        own = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)['checks']['2_ocr_to_json']['providers']
        self.assertEqual(own['luna']['groups']['C_coverage']['status'], 'review')
        self.assertTrue(any(x.get('line_id') == 'P0001-L0004' for x in own['luna']['issues']))
        self.assertNotIn('luna', lanes)
        keys = {'status', 'check_count', 'issue_count', 'checked_count', 'matched_count', 'not_checked_count', 'counts_overlap'}
        for provider in own.values():
            for group in provider['groups'].values():
                self.assertTrue(keys <= group.keys())

    def test_strict_duplicate_rejection_is_visible_when_observations_match(self) -> None:
        luna, upstage = lane('issuer/card', 'luna'), lane('issuer/card', 'upstage')
        luna['facts'].append(json.loads(json.dumps(luna['facts'][0])))
        outcomes, lanes, _ = validation_diagnostics({'luna': luna, 'upstage': upstage})
        own = validation_summary({'luna': luna, 'upstage': upstage}, outcomes)['checks']['2_ocr_to_json']['providers']['luna']
        self.assertNotIn('luna', lanes)
        self.assertEqual(own['groups']['prerequisites']['status'], 'review')
        self.assertTrue(any(x['check'] == 'strict_lane_rejection' for x in own['issues']))

    def test_invalid_fact_or_ignore_list_does_not_hide_independent_diagnostics(self) -> None:
        for ignored in (None, "invalid"):
            luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
            luna["ignored_risky_lines"] = ignored
            luna["facts"][0]["target"] = 123
            luna["facts"][0]["value"] = "5"
            luna["pages"][0]["text"] += "\n통신비 10% 할인"
            outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
            checks = outcomes["luna_text_to_json"]["independent_checks"]
            self.assertTrue(any(c["check"] == "unclaimed_source_line" and c["line_id"] == "P0001-L0004" for c in checks))
            self.assertTrue(any(c.get("field") == "value" and c.get("category") == "critical_content_mismatch" for c in checks))
            self.assertNotIn("luna", lanes)

    def test_diagnostic_source_registry_failure_is_explicit_not_checked(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["pages"].append(dict(luna["pages"][0]))
        outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
        self.assertEqual(outcomes["luna_text_to_json"]["independent_checks"], [{
            "check": "source_checks", "status": "not_checked",
            "error": "source page number/text is invalid or duplicated",
        }])
        self.assertNotIn("luna", lanes)

    def test_missing_facts_still_inventory_unclaimed_source_lines(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["facts"] = None
        outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
        checks = outcomes["luna_text_to_json"]["independent_checks"]
        self.assertTrue(any(c["check"] == "unclaimed_source_line" for c in checks))
        self.assertNotIn("luna", lanes)

    def test_supplemental_unit_comparison_normalizes_source_whitespace(self) -> None:
        luna, upstage = lane("issuer/card", "luna"), lane("issuer/card", "upstage")
        luna["identity"]["card_name"] = "Unknown card"
        fragment = luna["facts"][0]["field_evidence"]["unit"][0]
        fragment["fragment"] += " "
        fragment["char_end"] += 1
        outcomes, lanes, _ = validation_diagnostics({"luna": luna, "upstage": upstage})
        checks = outcomes["luna_text_to_json"]["independent_checks"]
        for field in ("value", "unit"):
            self.assertTrue(any(c.get("field") == field and c["status"] == "source_match" for c in checks))
        self.assertNotIn("luna", lanes)

    def test_diagnostic_execution_failure_blocks_automatic_approval(self) -> None:
        luna, upstage = self._lane(), line_id_lane("issuer/card", "upstage")
        with mock.patch("pickcardu_indexer.pipeline.compare_ocr_outputs", side_effect=RuntimeError("comparison exploded")):
            outcomes, lanes, errors = validation_diagnostics({"luna": luna, "upstage": upstage})
        self.assertEqual(outcomes["ocr_comparison"]["status"], "review")
        self.assertIn("ocr_comparison", errors)
        self.assertEqual(set(lanes), {"luna", "upstage"})

    def test_exact_one_line_role_ranges_and_numeric_ownership(self) -> None:
        payload = self._lane()
        source = "편의점 10% 할인 전월 실적 30만원 이상 월 한도 5천원"
        payload["pages"][0]["text"] = "Issuer Card\n" + source
        fact = payload["facts"][0]
        values = {
            "benefit_type": "편의점", "action": "할인", "target": "편의점",
            "condition": "전월 실적 30만원 이상", "value": "10", "unit": "%",
            "cap": "월 한도 5천원", "frequency": "", "period": "", "exceptions": "",
        }
        fact.update(values)
        fact["field_evidence"] = {
            field: ([] if not value else [{
                "line_id": "P0001-L0002", "fragment": value,
                "char_start": source.index(value), "char_end": source.index(value) + len(value),
            }])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "sentence", "line_ids": ["P0001-L0002"], "header_line_ids": [], "row_line_ids": []}
        self.assertEqual(len(validate_lane("luna", payload)), 1)

        missing_unit = json.loads(json.dumps(payload))
        missing_unit["facts"][0]["unit"] = ""
        missing_unit["facts"][0]["field_evidence"]["unit"] = []
        with self.assertRaisesRegex(ValueError, "source numeric unit"):
            validate_lane("luna", missing_unit)
        inline_unit = json.loads(json.dumps(missing_unit))
        inline_unit["facts"][0]["value"] = "10%"
        inline_unit["facts"][0]["field_evidence"]["value"][0].update({"fragment": "10%", "char_end": source.index("10%") + 3})
        self.assertEqual(len(validate_lane("luna", inline_unit)), 1)

        omitted_middle = json.loads(json.dumps(payload))
        omitted_middle["facts"][0]["condition"] = "전월 실적 30 이상"
        omitted_middle["facts"][0]["field_evidence"]["condition"] = [
            {"line_id": "P0001-L0002", "fragment": fragment,
             "char_start": source.index(fragment), "char_end": source.index(fragment) + len(fragment)}
            for fragment in ("전월 실적 30", "이상")
        ]
        with self.assertRaisesRegex(ValueError, "skip non-whitespace|source numeric unit|predicate has unaccounted"):
            validate_lane("luna", omitted_middle)

        swapped = json.loads(json.dumps(payload))
        for field, other in (("condition", "cap"), ("cap", "condition")):
            swapped["facts"][0][field] = values[other]
            swapped["facts"][0]["field_evidence"][field][0].update({
                "fragment": values[other],
                "char_start": source.index(values[other]),
                "char_end": source.index(values[other]) + len(values[other]),
            })
        with self.assertRaisesRegex(ValueError, "marked condition source range"):
            validate_lane("luna", swapped)

        broad = json.loads(json.dumps(payload))
        broad["facts"][0]["field_evidence"]["condition"].append({
            "line_id": "P0001-L0002", "fragment": source,
            "char_start": 0, "char_end": len(source),
        })
        with self.assertRaisesRegex(ValueError, "non-overlapping|reconstruct|overlapping evidence"):
            validate_lane("luna", broad)

        missing_condition_number = json.loads(json.dumps(payload))
        missing_condition_number["facts"][0]["condition"] = "전월 실적"
        missing_condition_number["facts"][0]["field_evidence"]["condition"][0].update({
            "fragment": "전월 실적", "char_start": source.index("전월 실적"), "char_end": source.index("전월 실적") + len("전월 실적"),
        })
        with self.assertRaisesRegex(ValueError, "marked condition source range|source numeric range|material operator|predicate has unaccounted"):
            validate_lane("luna", missing_condition_number)

        wrong_value = json.loads(json.dumps(payload))
        wrong_value["facts"][0]["value"] = "5"
        wrong_value["facts"][0]["field_evidence"]["value"][0].update({
            "fragment": "5", "char_start": source.rindex("5"), "char_end": source.rindex("5") + 1,
        })
        with self.assertRaisesRegex(ValueError, "source numeric range"):
            validate_lane("luna", wrong_value)

        punctuated = json.loads(json.dumps(payload))
        punctuated_source = source.replace("이상", "이상,").replace("5천원", "5천원,")
        punctuated["pages"][0]["text"] = "Issuer Card\n" + punctuated_source
        for field in ("condition", "cap"):
            value = values[field] + ","
            punctuated["facts"][0][field] = value
            punctuated["facts"][0]["field_evidence"][field][0].update({
                "fragment": value, "char_start": punctuated_source.index(value), "char_end": punctuated_source.index(value) + len(value),
            })
        self.assertEqual(len(validate_lane("luna", punctuated)), 1)

    def test_fee_label_value_table_preserves_fee_and_exception(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "# 연회비 정보", "| 구 분 | 국내전용 / 해외겸용 |",
                 "| --- | --- |", "| 연회비 | 없음 |",
                 "Card의 별도 연회비는 없으며, 보유 카드의 연회비는 각각 청구됩니다."]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = dict.fromkeys(RELATION_FIELDS, "")
        values.update(benefit_type="연회비", target="Card의 별도 연회비", action="없으며",
                      value="없음", exceptions="보유 카드의 연회비는 각각 청구됩니다.")
        fact.update(values)
        positions = {"benefit_type": 5, "value": 5, "target": 6, "action": 6, "exceptions": 6}
        fact["field_evidence"] = {
            field: ([] if not value else [{"line_id": f"P0001-L{positions[field]:04d}", "fragment": value,
                "char_start": lines[positions[field] - 1].index(value),
                "char_end": lines[positions[field] - 1].index(value) + len(value)}])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0005", "P0001-L0006"],
                                  "header_line_ids": ["P0001-L0003"], "row_line_ids": ["P0001-L0005"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        for field, replacement in (("value", "5천원"), ("target", "다른 수수료")):
            unsafe = json.loads(json.dumps(payload))
            unsafe["facts"][0][field] = replacement
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_lane("luna", unsafe)
        unsafe = json.loads(json.dumps(payload))
        unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("# 연회비 정보", "# 연회비 30만원 이상 면제")
        with self.assertRaises(ValueError):
            validate_lane("luna", unsafe)
        for header in ("전월 실적 30만원 이상", "가족 제외", "월 한도", "연간", "가족카드", "국내전용"):
            unsafe = json.loads(json.dumps(payload))
            unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("국내전용 / 해외겸용", header)
            with self.subTest(header=header), self.assertRaises(ValueError):
                validate_lane("luna", unsafe)
        unsafe = json.loads(json.dumps(payload))
        unsafe["facts"][0]["exceptions"] = ""
        unsafe["facts"][0]["field_evidence"]["exceptions"] = []
        with self.assertRaisesRegex(ValueError, "fee condition or exception"):
            validate_lane("luna", unsafe)
        # Omitting both the context and its evidence must not hide its rules.
        unsafe["facts"][0]["relation_scope"]["line_ids"].remove("P0001-L0006")
        for field, value in (("target", "연회비"), ("action", "없음")):
            unsafe["facts"][0][field] = value
            unsafe["facts"][0]["field_evidence"][field] = [{"line_id": "P0001-L0005", "fragment": value,
                "char_start": lines[4].index(value), "char_end": lines[4].index(value) + len(value)}]
        with self.assertRaisesRegex(ValueError, "adjacent fee rules|table value material"):
            validate_lane("luna", unsafe)
        unsafe = json.loads(json.dumps(payload))
        unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("Card의 별도", "Other의 별도")
        unsafe["facts"][0]["target"] = "Other의 별도 연회비"
        unsafe["facts"][0]["field_evidence"]["target"][0].update(fragment="Other의 별도 연회비", char_end=len("Other의 별도 연회비"))
        with self.assertRaisesRegex(ValueError, "fee target"):
            validate_lane("luna", unsafe)
        unsafe = json.loads(json.dumps(payload))
        unsafe["pages"][0]["text"] += "\n다만, 재발급은 청구됩니다."
        with self.assertRaisesRegex(ValueError, "adjacent fee rules"):
            validate_lane("luna", unsafe)

    def test_table_parenthesized_common_condition_can_join_row_condition(self) -> None:
        payload = self._lane()
        lines = ["Issuer Card", "# 할인 서비스(온라인 결제 시)",
                 "| 조건 | 대상 | 할인율 |", "| --- | --- | --- |",
                 "| 전월 실적 30만원 이상 | 카페 | 10% |"]
        payload["pages"][0]["text"] = "\n".join(lines)
        fact = payload["facts"][0]
        values = dict.fromkeys(RELATION_FIELDS, "")
        values.update(benefit_type="할인 서비스", action="할인", target="카페",
                      condition="온라인 결제 시 전월 실적 30만원 이상", value="10", unit="%")
        fact.update(values)
        parts = {"benefit_type": [(2, "할인 서비스")], "action": [(2, "할인")],
                 "target": [(5, "카페")], "condition": [(2, "온라인 결제 시"), (5, "전월 실적 30만원 이상")],
                 "value": [(5, "10")], "unit": [(5, "%")]}
        fact["field_evidence"] = {
            field: [{"line_id": f"P0001-L{number:04d}", "fragment": fragment,
                     "char_start": lines[number - 1].index(fragment),
                     "char_end": lines[number - 1].index(fragment) + len(fragment)}
                    for number, fragment in parts.get(field, [])] for field in RELATION_FIELDS
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003", "P0001-L0005"],
                                  "header_line_ids": ["P0001-L0003"], "row_line_ids": ["P0001-L0005"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        for omitted in ("제외", "50만원 미만", "에 한하지 않음"):
            unsafe = json.loads(json.dumps(payload))
            unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("온라인 결제 시)", f"온라인 결제 시 {omitted})")
            with self.subTest(omitted=omitted), self.assertRaises(ValueError):
                validate_lane("luna", unsafe)
        unsafe = json.loads(json.dumps(payload))
        unsafe["facts"][0]["condition"] = "온라인 결제 시 전월 실적 50만원 이상"
        with self.assertRaises(ValueError):
            validate_lane("luna", unsafe)
        for header in ("조건 50만원 이상", "조건 제외"):
            unsafe = json.loads(json.dumps(payload))
            unsafe["pages"][0]["text"] = unsafe["pages"][0]["text"].replace("| 조건 |", f"| {header} |")
            with self.subTest(header=header), self.assertRaises(ValueError):
                validate_lane("luna", unsafe)

    def test_four_column_table_binds_field_roles_to_row_cells(self) -> None:
        payload = self._lane()
        header = "| 대상 | 할인율 | 전월 실적 | 월 한도 |"
        row = "| 편의점 | 10% | 30만원 이상 | 5천원 |"
        payload["pages"][0]["text"] = f"Issuer Card\n{header}\n{row}"
        fact = payload["facts"][0]
        values = {
            "benefit_type": "대상", "action": "할인율", "target": "편의점",
            "condition": "30만원 이상", "value": "10", "unit": "%",
            "cap": "5천원", "frequency": "", "period": "", "exceptions": "",
        }
        fact.update(values)
        locations = {"benefit_type": (header, "대상", 2), "action": (header, "할인율", 2), "target": (row, "편의점", 3), "condition": (row, "30만원 이상", 3), "value": (row, "10", 3), "unit": (row, "%", 3), "cap": (row, "5천원", 3)}
        fact["field_evidence"] = {
            field: ([] if not value else [{
                "line_id": f"P0001-L{locations[field][2]:04d}", "fragment": value,
                "char_start": locations[field][0].index(value), "char_end": locations[field][0].index(value) + len(value),
            }])
            for field, value in values.items()
        }
        fact["relation_scope"] = {"scope_type": "table_row", "line_ids": ["P0001-L0002", "P0001-L0003"], "header_line_ids": ["P0001-L0002"], "row_line_ids": ["P0001-L0003"]}
        self.assertEqual(len(validate_lane("luna", payload)), 1)
        truncated_condition = json.loads(json.dumps(payload))
        truncated_condition["facts"][0]["condition"] = "30만원"
        truncated_condition["facts"][0]["field_evidence"]["condition"][0].update({"fragment": "30만원", "char_end": row.index("30만원") + len("30만원")})
        with self.assertRaisesRegex(ValueError, "condition cell is only partially|table condition material"):
            validate_lane("luna", truncated_condition)
        truncated_unit = json.loads(json.dumps(payload))
        truncated_unit["facts"][0]["unit"] = ""
        truncated_unit["facts"][0]["field_evidence"]["unit"] = []
        with self.assertRaisesRegex(ValueError, "value cell is only partially|table value material"):
            validate_lane("luna", truncated_unit)
        swapped = json.loads(json.dumps(payload))
        for field, other in (("condition", "cap"), ("cap", "condition")):
            swapped["facts"][0][field] = values[other]
            swapped["facts"][0]["field_evidence"][field][0].update({
                "fragment": values[other], "char_start": row.index(values[other]), "char_end": row.index(values[other]) + len(values[other]),
            })
        with self.assertRaisesRegex(ValueError, "matching data cell|candidate on its source line|wrong table role"):
            validate_lane("luna", swapped)


if __name__ == "__main__":
    unittest.main()
