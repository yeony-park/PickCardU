import importlib
import unittest

from pickcardu_rag.answering import RewriteOutput
from pickcardu_rag_api.chat_models import SurveyContext


class PersonalizationTest(unittest.TestCase):
    survey = {'monthly_spending': '30-50', 'spending_categories': ['카페'], 'preferred_benefits': ['할인']}
    wallet = {'status': 'ready', 'card_keys': ['issuer/a']}
    active = ('issuer/a', 'issuer/b')

    def module(self):
        try:
            return importlib.import_module('pickcardu_rag_api.chat_personalization')
        except ModuleNotFoundError:
            self.fail('personalization resolution is not implemented')

    def resolve(self, output, **overrides):
        args = {'original_query': '그러면 주유 혜택 추천', 'survey_context': self.survey,
                'wallet_context': self.wallet, 'active_card_keys': self.active, 'references': []}
        args.update(overrides)
        return self.module().resolve_personalization(output, **args)

    def test_generic_followup_uses_raw_question_and_ignores_unused_stale_wallet(self):
        result = self.resolve(RewriteOutput(standalone_query='카페 위주로 30만원 소비하며 주유 카드 추천'),
                              wallet_context={'status': 'ready', 'card_keys': ['no-longer-active']})
        self.assertEqual(result.standalone_query, '그러면 주유 혜택 추천')
        self.assertEqual(result.retrieval_query, '그러면 주유 혜택 추천')
        self.assertIsNone(result.personalization_context)
        self.assertIsNone(result.target_card_keys)

    def test_survey_is_applied_only_after_decision_and_current_dimensions_override(self):
        self.assertIn('use_survey', RewriteOutput.model_fields)
        output = RewriteOutput(standalone_query='내게 맞는 여행 마일리지 카드', use_survey=True,
                               overridden_survey_fields=['spending_categories', 'preferred_benefits'])
        result = self.resolve(output)
        effective = result.personalization_context.survey_context
        self.assertEqual(effective.monthly_spending, '30-50')
        self.assertEqual(effective.spending_categories, [])
        self.assertEqual(effective.preferred_benefits, [])
        self.assertEqual(result.retrieval_query, '내게 맞는 여행 마일리지 카드')
        self.assertEqual(self.survey['spending_categories'], ['카페'])

    def test_owned_lookup_and_owned_new_comparison_have_different_candidate_sets(self):
        self.assertIn('use_wallet', RewriteOutput.model_fields)
        lookup = self.resolve(RewriteOutput(standalone_query='내 카드 카페 혜택', use_wallet=True, wallet_operation='lookup'))
        self.assertEqual(lookup.personalization_context.wallet_context.card_keys, ['issuer/a'])
        self.assertEqual(lookup.wallet_operation, 'lookup')
        compare = self.resolve(RewriteOutput(standalone_query='내 카드보다 좋은 새 카드', use_wallet=True, wallet_operation='compare'))
        self.assertEqual(compare.wallet_operation, 'compare')
        self.assertIsNone(compare.target_card_keys)
        full = self.resolve(RewriteOutput(standalone_query='새 카드와 비교', use_wallet=True, wallet_operation='compare'),
                            wallet_context={'status': 'ready', 'card_keys': list(self.active)})
        self.assertEqual(full.scope, 'clarification')
        self.assertIsNone(full.retrieval_query)

    def test_all_owned_precision_comparison_does_not_silently_drop_sixth_card(self):
        self.assertIn('wallet_compare_scope', RewriteOutput.model_fields)
        output = RewriteOutput(standalone_query='내 카드 모두 전월실적 비교', use_wallet=True,
                               wallet_operation='compare', wallet_compare_scope='all_owned')
        keys = [f'issuer/{i}' for i in range(6)]
        result = self.resolve(output, wallet_context={'status': 'ready', 'card_keys': keys}, active_card_keys=keys)
        self.assertEqual(result.scope, 'clarification')
        self.assertIsNone(result.target_card_keys)
        result = self.resolve(output)
        self.assertEqual(result.target_card_keys, ['issuer/a'])
        lookup = RewriteOutput(standalone_query='내 카드 전체 전월실적', use_wallet=True,
            wallet_operation='lookup', wallet_compare_scope='all_owned')
        result = self.resolve(lookup, wallet_context={'status': 'ready', 'card_keys': keys}, active_card_keys=keys)
        self.assertEqual(result.scope, 'clarification')

    def test_all_owned_precision_boundary_one_five_six_and_full_catalog(self):
        active = [f'issuer/{i}' for i in range(106)]
        for count in (1, 5, 6, 106):
            with self.subTest(count=count):
                output = RewriteOutput(standalone_query='내 카드 모두 전월실적 비교', use_wallet=True,
                    wallet_operation='lookup', wallet_compare_scope='all_owned')
                result = self.resolve(output, wallet_context={'status': 'ready', 'card_keys': active[:count]},
                                      active_card_keys=active)
                self.assertEqual(result.scope, 'global' if count <= 5 else 'clarification')
                self.assertEqual(result.target_card_keys, active[:count] if count <= 5 else None)

    def test_needed_missing_settings_are_clarified_without_global_fallback(self):
        self.assertIn('use_survey', RewriteOutput.model_fields)
        for output, overrides in (
            (RewriteOutput(standalone_query='내 패턴 추천', use_survey=True), {'survey_context': None}),
            (RewriteOutput(standalone_query='내 카드', use_wallet=True, wallet_operation='lookup'), {'wallet_context': None}),
            (RewriteOutput(standalone_query='내 카드', use_wallet=True, wallet_operation='lookup'),
             {'wallet_context': {'status': 'ready', 'card_keys': ['missing']}}),
        ):
            with self.subTest(output=output):
                result = self.resolve(output, **overrides)
                self.assertEqual(result.scope, 'clarification')
                self.assertIsNone(result.retrieval_query)
                self.assertIsNone(result.personalization_context)

    def test_recall_order_is_owned_server_order_not_model_selection_order(self):
        references = [{'ref': 't1r1', 'card_key': 'issuer/b', 'card_name': 'B', 'issuer': '발급사',
                       'release_id': 'old', 'profile': 'card_page_section_benefit'},
                      {'ref': 't1r2', 'card_key': 'issuer/a', 'card_name': 'A', 'issuer': '발급사',
                       'release_id': 'old', 'profile': 'card_page_section_benefit'}]
        output = RewriteOutput(standalone_query='이전 목록', scope='previous', operation='recall', selected_refs=['t1r2', 't1r1'])
        result = self.resolve(output, references=references, active_card_keys=())
        self.assertEqual([item.card_key for item in result.recall_items], ['issuer/b', 'issuer/a'])
        self.assertIsNone(result.retrieval_query)

    def test_missing_first_question_rules_only_ask_for_explicit_missing_data(self):
        module = self.module()
        self.assertIsNotNone(module.missing_personalization_clarification('내 카드 혜택', False, 'empty'))
        self.assertIsNotNone(module.missing_personalization_clarification('내 소비 패턴으로 추천', False, None))
        self.assertIsNone(module.missing_personalization_clarification('편의점 카드 추천', False, 'empty'))
        self.assertIsNone(module.missing_personalization_clarification('내게 좋은 카드 추천', False, 'empty'))
        self.assertIsNone(module.missing_personalization_clarification('국내 카드 혜택 추천', False, 'empty'))
        self.assertIsNone(module.missing_personalization_clarification('미등록 카드 추천', False, 'empty'))

    def test_search_hint_preserves_full_public_question_and_never_uses_spending_as_performance(self):
        module = self.module()
        survey = SurveyContext(monthly_spending='over-200',
            spending_categories=['쇼핑', '배달·외식', '카페', '교통', '주유', '여행'],
            preferred_benefits=['할인', '포인트 적립', '항공 마일리지'])
        question = '가' * 500
        query = module.build_retrieval_query(question, survey)
        self.assertTrue(query.startswith(question))
        self.assertLessEqual(len(query), 1024)
        self.assertIn('검색 힌트:', query)
        self.assertNotIn('over-200', query)


if __name__ == '__main__':
    unittest.main()
