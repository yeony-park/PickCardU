"""Comparison-key regressions; no provider/model calls."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from pickcardu_indexer.grounding import normalise_fact, relation_key, fact_bundles, compare_role_field, compare_fact_groups


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

    def test_compound_money_preserves_total_sign_and_raw_value(self):
        from pickcardu_indexer.grounding import typed_literals
        for source, amount in [('1만5천원','15000'), ('1만 5000원','15000'),
                               ('3만 5천원','35000'), ('1만5원','10005'),
                               ('1만5백원','10500'), ('-1만5천원','-15000'),
                               ('2만3천원','23000'), ('5천원','5000'), ('1.5만원','15000'),
                               ('1억5천만원','150000000'), ('1천5백만2천원','15002000'),
                               ('2억3천4백5십6만원','234560000'), ('2조3억4만원','2000300040000'),
                               ('0만원','0'), ('1억0만원','100000000'), ('+2만원','20000')]:
            with self.subTest(source=source):
                self.assertEqual(typed_literals(source), [{'kind':'KRW','decimal':amount}])
                self.assertEqual(self.key(cap='월 '+source),self.key(cap='월 '+amount+'원'))
        self.assertNotEqual(self.key(cap='월 1만5천원'),self.key(cap='월 5천원'))
        fact=normalise_fact({**self.raw,'cap':'월 1만5천원'})
        self.assertEqual(fact['cap'],'월 1만5천원')
        self.assertEqual(fact['typed_normalization']['cap'],[{'kind':'KRW','decimal':'15000'}])

    def test_affirmative_endings_and_complete_cap_atoms_only(self):
        self.assertEqual(self.key(action='할인'),self.key(action='할인입니다'))
        self.assertEqual(self.key(condition='전월 실적 30만원 이상'),self.key(condition='전월 실적은 300000원 이상입니다'))
        self.assertEqual(self.key(cap='월 1만5천원'),self.key(cap='15000원 / 월'))
        self.assertEqual(self.key(cap='월 최대 15000원'),self.key(cap='월 1만5천원'))
        for changes in ({'action':'할인되지 않습니다'}, {'condition':'전월 실적 30만원 초과'},
                        {'condition':'전월 실적 30만원 이상만'}):
            self.assertNotEqual(self.key(),self.key(**changes))
        self.assertNotEqual(self.key(cap='월 1만원'),self.key(cap='월 통합 1만원'))
        self.assertNotEqual(self.key(cap='월 1만원'),self.key(cap='월 1만원 온라인 제외'))

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
        self.assertEqual(self.key(condition="전월 실적 30만원 이상 및 건당 1만원 이상"),
                         self.key(condition="건당 10000원 이상 및 전월 실적 300000원 이상"))
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

    def test_layered_comparison_never_promotes_equal_numbers_alone(self):
        left = normalise_fact(self.raw)
        changed = normalise_fact({**self.raw, 'condition': '건당 결제금액 30만원 이상'})
        result = compare_fact_groups(left, changed)
        self.assertEqual(result['benefit']['status'], 'pass')
        condition = result['condition']['fields']['condition']
        self.assertEqual(condition['numeric_observation'], 'same')
        self.assertEqual(condition['status'], 'unresolved')
        for fields in ({'condition': '전월 실적 50만원 이상'},
                       {'condition': '전월 실적 30만원 초과'},
                       {'value': '1'}, {'unit': '원'}):
            result = compare_fact_groups(left, normalise_fact({**self.raw, **fields}))
            self.assertTrue(any(x['status'] == 'difference' for x in result.values()))
        for fields in ({'target': '마트'}, {'action': '적립'},
                       {'exceptions': '온라인 제외'}, {'cap': '월 통합 5천원'}):
            result = compare_fact_groups(left, normalise_fact({**self.raw, **fields}))
            self.assertTrue(any(x['status'] != 'pass' for x in result.values()))
        self.assertEqual(compare_role_field(left, normalise_fact({**self.raw, 'condition': '전월 실적 300000원 이상입니다'}), 'condition')['status'], 'pass')

    def test_numeric_order_does_not_override_bound_predicates(self):
        a = normalise_fact({**self.raw, 'condition': '전월 실적 30만원 이상 및 건당 1만원 이상'})
        b = normalise_fact({**self.raw, 'condition': '건당 10000원 이상 및 전월 실적 300000원 이상'})
        self.assertEqual(compare_role_field(a, b, 'condition')['status'], 'pass')
        self.assertEqual(compare_role_field(a, b, 'condition')['numeric_observation'], 'same_after_supported_reordering')
        for text in ('전월 실적 1만원 이상 및 건당 30만원 이상',
                     '전월 실적 30만원 이상 또는 건당 1만원 이상',
                     '전월 실적 30만원 이상 및 건당 1만원 이상 온라인 제외'):
            b = normalise_fact({**self.raw, 'condition': text})
            self.assertNotEqual(compare_role_field(a, b, 'condition')['status'], 'pass')

    def test_punctuation_spelling_preserves_meaningful_signs(self):
        self.assertEqual(self.key(target='‘나한테 진심’'), self.key(target="'나한테 진심'"))
        self.assertEqual(self.key(target='CU・GS25'), self.key(target='CU·GS25'))
        self.assertNotEqual(self.key(cap='월 -1000원'), self.key(cap='월 1000원'))
        self.assertNotEqual(self.key(frequency='월/연 1회'), self.key(frequency='월연 1회'))
        self.assertNotEqual(self.key(exceptions='온라인 | 오프라인'), self.key(exceptions='온라인 오프라인'))


if __name__ == "__main__":
    unittest.main()
