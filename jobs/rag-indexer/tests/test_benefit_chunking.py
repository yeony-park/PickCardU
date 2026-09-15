from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "jobs/rag-indexer/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_indexer.benefit import BENEFIT_CHUNKING_CONTRACT, build_benefit_chunks


def fact(target: str, value: str, line_id: str = "P0001-L0004") -> dict[str, object]:
    return {
        "target": target,
        "action": "할인",
        "value": value,
        "condition": "SYNTHETIC JSON VALUE",
        "field_evidence": {
            "target": [{"line_id": line_id, "fragment": target}],
            "value": [{"line_id": line_id, "fragment": value}],
        },
        "relation_scope": {"line_ids": [line_id]},
    }


class BenefitChunkingTests(unittest.TestCase):
    def test_raw_levels_and_label_assisted_neighbor_window(self) -> None:
        raw = (
            "=== PAGE 1 ===\nIssuer Card\n\n## 생활 혜택\n\n"
            "이용 전 안내 문단\n\n편의점 10% 할인\n전월 실적 30만원\n\n월 5천원 한도\n\n"
            "검증과 무관하지만 페이지에 존재하는 원문\n"
        )
        chunks, audit = build_benefit_chunks(
            raw,
            document_id="issuer/card",
            issuer="Issuer",
            card_name="Card",
            facts=[fact("편의점", "10%")],
        )
        by_level = {
            level: [chunk for chunk in chunks if chunk["level"] == level]
            for level in ("card", "page", "section", "benefit")
        }
        self.assertEqual(audit["chunking_contract"], BENEFIT_CHUNKING_CONTRACT)
        self.assertEqual(audit["mapped_facts"], 1)
        self.assertIn("검증과 무관하지만 페이지에 존재하는 원문", by_level["page"][0]["text"])
        benefit = by_level["benefit"][0]
        self.assertIn("이용 전 안내 문단", benefit["text"])
        self.assertIn("편의점 10% 할인", benefit["text"])
        self.assertIn("월 5천원 한도", benefit["text"])
        self.assertNotIn("SYNTHETIC JSON VALUE", json.dumps(chunks, ensure_ascii=False))
        self.assertTrue(all(chunk["metadata"]["ocr_provider"] == "luna" for chunk in chunks))
        self.assertEqual(by_level["card"][0]["metadata"]["chunking_audit"]["mapped_facts"], 1)
        parent = next(chunk for chunk in by_level["section"] if chunk["chunk_id"] == benefit["metadata"]["parent_id"])
        self.assertIn(benefit["chunk_id"], parent["metadata"]["child_ids"])

    def test_zero_and_tied_scores_are_not_silently_mapped(self) -> None:
        raw = "=== PAGE 1 ===\nA 할인\n\nB 할인\n"
        chunks, audit = build_benefit_chunks(
            raw,
            document_id="issuer/card",
            issuer="Issuer",
            card_name="Card",
            facts=[
                {**fact("없는 대상", "77%"), "action": "적립"},
                fact("", "", "P0001-L0001"),
            ],
        )
        self.assertEqual([chunk for chunk in chunks if chunk["level"] == "benefit"], [])
        self.assertEqual(audit["unmapped_fact_indices"], [0])
        self.assertEqual(audit["ambiguous_fact_indices"], [1])


if __name__ == "__main__":
    unittest.main()
