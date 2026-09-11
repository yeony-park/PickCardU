"""Comparison-key regressions; no provider/model calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pickcardu_indexer.grounding import normalise_fact, relation_key


class NumericGroundingTests(unittest.TestCase):
    def setUp(self):
        self.raw = {"benefit_type": "할인", "action": "할인", "target": "편의점",
                    "condition": "전월 실적 30만원 이상", "value": "10", "unit": "%",
                    "cap": "", "frequency": "", "period": "", "exceptions": ""}

    def key(self, **changes):
        return relation_key(normalise_fact({**self.raw, **changes}))

    def test_equivalent_money_decimal_and_inline_units(self):
        original = dict(self.raw)
        for changes in ({"condition": "전월 실적 300000원 이상"},
                        {"condition": "전월 실적 300,000.0원 이상"},
                        {"value": "10%"}, {"value": "10.0%", "unit": "퍼센트"},
                        {"condition": "전월 실적 30만원 이상."}, {"condition": "전월 실적 30만원 이상,"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.key(), self.key(**changes))
        self.assertEqual(self.raw, original)
        self.assertEqual(self.key(value="30만원", unit="만원"), self.key(value="300000.0", unit="원"))

    def test_critical_changes_remain_distinct(self):
        for changes in ({"condition": "전월 실적 50만원 이상"},
                        {"condition": "전월 실적 30만원 초과"},
                        {"value": "1"}, {"target": "통신비"}, {"action": "적립"},
                        {"value": "0.1", "unit": ""}, {"exceptions": "온라인 제외"}):
            with self.subTest(changes=changes):
                self.assertNotEqual(self.key(), self.key(**changes))
        with self.assertRaisesRegex(ValueError, "incompatible dimensions"):
            self.key(value="10%", unit="원")
        with self.assertRaisesRegex(ValueError, "strings"):
            self.key(value=10)


if __name__ == "__main__":
    unittest.main()
