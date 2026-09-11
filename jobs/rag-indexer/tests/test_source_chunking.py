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
    def test_original_heading_groups_raw_spans_and_source_tampering_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "normalized.json"
            source.write_text(json.dumps({"pages": [{"page": 1, "text":
                "Issuer Card\n## 생활 혜택\n### 일상 할인\n편의점  10% 할인\n전월 실적 30만원 이상\n통신비 5% 할인"
            }]}), encoding="utf-8")
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            def fact(ids, quote):
                ref = {"page": 1, "quote": quote}
                return {"fact": {"target": "SYNTHETIC MUST NOT APPEAR"},
                        "evidence_refs": {"upstage": ref, "luna": ref},
                        "relation_scope_refs": {"upstage": {"line_ids": ids, "evidence": ref}}}
            canonical = root / "canonical.json"
            canonical.write_text(json.dumps({
                "pipeline_contract": OCR_PIPELINE_CONTRACT,
                "chunking_contract": CHUNKING_CONTRACT,
                "structure_provider": "upstage",
                "identity": {"issuer_name": "Issuer", "card_name": "Card"},
                "facts": [fact(["P0001-L0006"], "통신비 5% 할인"),
                          fact(["P0001-L0004", "P0001-L0005"], "편의점 10% 할인 전월 실적 30만원 이상")],
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
            self.assertEqual(len(sections), 1)
            self.assertEqual(sections[0]["metadata"]["section"], "생활 혜택 > 일상 할인")
            self.assertEqual(sections[0]["text"], "편의점  10% 할인\n전월 실적 30만원 이상\n통신비 5% 할인")
            self.assertEqual(len(benefits), 2)
            self.assertIn("편의점  10% 할인\n전월 실적 30만원 이상", [chunk["text"] for chunk in benefits])
            self.assertNotIn("SYNTHETIC", json.dumps(chunks))
            self.assertIn("생활 혜택 > 일상 할인", sections[0]["metadata"]["reranker_text"])
            source.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "source hash mismatch"):
                indexer._chunks("fixture", documents)


if __name__ == "__main__":
    unittest.main()
