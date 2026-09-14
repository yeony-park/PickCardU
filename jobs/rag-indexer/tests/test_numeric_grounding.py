"""Comparison-key regressions; no provider/model calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pickcardu_indexer.grounding import normalise_fact, relation_key, fact_bundles


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

    def test_named_bundles_ignore_key_order_not_roles(self):
        original = normalise_fact(self.raw)
        reordered = normalise_fact(dict(reversed(list(self.raw.items()))))
        self.assertEqual(fact_bundles(original), fact_bundles(reordered))
        self.assertEqual(self.key(period="10영업일 이내에"), self.key(period="10영업일 이내"))
        self.assertNotEqual(self.key(period="10영업일 이내"), self.key(period="10일 이내"))
        self.assertNotEqual(self.key(period="10영업일 이내"), self.key(period="10영업일 이후"))
        self.assertNotEqual(self.key(period="10영업일 이내에만"), self.key(period="10영업일 이내"))

    def test_only_complete_explicit_and_atoms_can_commute(self):
        left = "전월 실적 30만원 이상 및 건당 결제금액 1만원 이상"
        right = "10000원 이상 건당 결제금액 그리고 300000원 이상 전월 실적"
        self.assertEqual(self.key(condition=left), self.key(condition=right))
        for wrong in (left.replace("및", "또는"), left.replace("30만원 이상", "30만원 초과"),
                      "전월 실적 1만원 이상 및 건당 결제금액 30만원 이상", left + " 단 온라인 제외"):
            self.assertNotEqual(self.key(condition=left), self.key(condition=wrong))

    def test_label_duplicates_do_not_erase_unique_context(self):
        self.assertEqual(self.key(benefit_type="편의점 10% 할인"), self.key(benefit_type="편의점 할인"))
        self.assertNotEqual(self.key(benefit_type="편의점 5% 할인"), self.key(benefit_type="편의점 할인"))
        self.assertNotEqual(self.key(benefit_type="온라인 편의점 할인"), self.key(benefit_type="편의점 할인"))
        self.assertNotEqual(self.key(benefit_type="편의점 적립"), self.key(benefit_type="편의점 할인"))

    def test_qualifier_edges_cannot_be_swapped_between_benefits(self):
        from collections import Counter
        a = normalise_fact(self.raw)
        b = normalise_fact({**self.raw, "target": "통신비", "value": "5", "condition": "전월 실적 50만원 이상"})
        correct = Counter((relation_key(a), relation_key(b)))
        wrong = Counter((relation_key(normalise_fact({**a, "condition": b['condition']})),
                         relation_key(normalise_fact({**b, "condition": a['condition']}))))
        self.assertNotEqual(correct, wrong)
        self.assertNotEqual(self.key(condition="전월 실적 30만원 이상", cap="월 1만원"),
                            self.key(cap="전월 실적 30만원 이상", condition="월 1만원"))
        self.assertNotEqual(self.key(exceptions="온라인 제외"), self.key(exceptions="온라인 포함"))


if __name__ == "__main__":
    unittest.main()
