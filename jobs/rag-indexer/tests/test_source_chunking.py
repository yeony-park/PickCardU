"""Local renderer checks: source layout is not reconstructed from JSON values."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "jobs/rag-indexer/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_indexer.pipeline import Indexer, OCR_PIPELINE_CONTRACT, CHUNKING_CONTRACT


class SourceChunkingTests(unittest.TestCase):
    def test_historical_label_window_uses_raw_ocr_and_source_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "ocr.txt"
            source.write_text(
                "=== PAGE 1 ===\nIssuer Card\n\n## 생활 혜택\n\n### 일상 할인\n"
                "편의점  10% 할인\n전월 실적 30만원 이상\n\n통신비 5% 할인",
                encoding="utf-8",
            )
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            def fact(ids, target, value):
                refs = {
                    "target": [{"line_id": ids[0], "fragment": target}],
                    "value": [{"line_id": ids[0], "fragment": value}],
                }
                return {
                    "fact": {"target": target, "value": value, "condition": "SYNTHETIC MUST NOT APPEAR"},
                    "field_evidence_refs": {"upstage": refs},
                    "relation_scope_refs": {"upstage": {"line_ids": ids}},
                }
            canonical = root / "canonical.json"
            canonical.write_text(json.dumps({
                "pipeline_contract": OCR_PIPELINE_CONTRACT,
                "chunking_contract": CHUNKING_CONTRACT,
                "structure_provider": "upstage",
                "identity": {"issuer_name": "Issuer", "card_name": "Card"},
                "facts": [fact(["P0001-L0008"], "통신비", "5%"),
                          fact(["P0001-L0006", "P0001-L0007"], "편의점", "10%")],
            }), encoding="utf-8")
            canonical_hash = hashlib.sha256(canonical.read_bytes()).hexdigest()
            indexer = Indexer.__new__(Indexer)
            indexer.state = SimpleNamespace(
                artifact_path=lambda *_: source,
                artifact_hash=lambda _run, _doc, kind, _provider: canonical_hash if kind == "canonical" else source_hash,
            )
            documents = [{"document_id": "issuer/card", "canonical_path": str(canonical), "canonical_sha256": canonical_hash}]
            chunks, _ = indexer._chunks("fixture", documents)
            sections = [chunk for chunk in chunks if chunk["level"] == "section"]
            benefits = [chunk for chunk in chunks if chunk["level"] == "benefit"]
            section = next(chunk for chunk in sections if chunk["metadata"]["section"] == "일상 할인")
            self.assertIn("편의점  10% 할인", section["text"])
            self.assertEqual(len(benefits), 2)
            self.assertTrue(all("편의점  10% 할인" in chunk["text"] for chunk in benefits))
            self.assertTrue(all("통신비 5% 할인" in chunk["text"] for chunk in benefits))
            self.assertNotIn("SYNTHETIC", json.dumps(chunks))
            self.assertIn("일상 할인", section["metadata"]["reranker_text"])
            source.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "OCR text hash mismatch"):
                indexer._chunks("fixture", documents)


if __name__ == "__main__":
    unittest.main()
