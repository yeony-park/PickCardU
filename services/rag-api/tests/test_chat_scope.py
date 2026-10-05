import unittest

from pickcardu_rag.answering import RewriteOutput
from pickcardu_rag_api import chat
from pickcardu_rag_api.chat_scope import recall_answer


class ChatScopeTest(unittest.TestCase):
    def test_legacy_source_fallback_identifies_snapshot_turn(self):
        legacy_card = {'card_key': 'a', 'card_name': 'A', 'issuer': '발급사'}
        sourced_card = {'card_key': 'b', 'card_name': 'B', 'issuer': '발급사',
                        'release_id': 'original', 'profile': 'parent_child_bundle'}
        turn = {'state': 'completed', 'answer': {
            'release_id': 'snapshot', 'profile': 'card_page_section_benefit',
            'usage': {'answer': {'conversation_scope': {
                'scope': 'previous', 'reference_groups': [[legacy_card], [sourced_card]]}}}}}
        refs = chat.card_references([turn])
        legacy = recall_answer(['t1r1'], refs)
        sourced = recall_answer(['t2r1'], refs)
        self.assertEqual((legacy.release_id, legacy.profile), ('snapshot', 'card_page_section_benefit'))
        self.assertEqual((sourced.release_id, sourced.profile), ('original', 'parent_child_bundle'))

    def test_recall_selects_the_exact_group_when_card_id_occurs_twice(self):
        refs = [{'ref': 't1r1', 'card_key': 'a', 'card_name': '옛 이름', 'issuer': '발급사',
                 'release_id': 'old', 'profile': 'card_page_section_benefit'},
                {'ref': 't2r2', 'card_key': 'a', 'card_name': '새 목록 이름', 'issuer': '발급사',
                 'release_id': 'new', 'profile': 'card_page_section_benefit'}]
        answer = recall_answer(['t2r2'], refs)
        self.assertEqual(answer.answer, '이전에 안내한 카드 목록입니다.\n2. 새 목록 이름 · 발급사')
        self.assertEqual(answer.release_id, 'new')

    def test_refs_follow_recommendation_order_and_server_maps_ids(self):
        turn = {'seq': 1, 'state': 'completed', 'answer': {'answer_status': 'answered',
                'cards': [{'card_key': 'a', 'card_name': 'A', 'issuer': '카드사'},
                          {'card_key': 'b', 'card_name': 'B', 'issuer': '카드사'}],
                'recommendations': [{'card_key': 'b'}, {'card_key': 'a'}]}}
        refs = chat.card_references([turn])
        self.assertEqual([(r['ref'], r['card_key']) for r in refs], [('t1r1', 'b'), ('t1r2', 'a')])
        output = RewriteOutput(standalone_query='두 번째 카드 전월실적?', scope='previous',
                               selected_refs=['t1r2'], clarification_question='')
        self.assertEqual(chat.resolve_scope(output, refs), (('a',), None))
        output.selected_refs = ['t1r2', 't1r1']
        self.assertEqual(chat.resolve_scope(output, refs), (('b', 'a'), None))

    def test_unknown_duplicate_mixed_group_refs_clarify_without_global_fallback(self):
        refs = [{'ref': 't1r1', 'card_key': 'a'}, {'ref': 't2r1', 'card_key': 'b'}]
        for selected in (['fake'], ['t1r1', 't1r1'], ['t1r1', 't2r1'], []):
            output = RewriteOutput(standalone_query='저것들 중?', scope='previous',
                                   selected_refs=selected, clarification_question='')
            keys, question = chat.resolve_scope(output, refs)
            self.assertIsNone(keys)
            self.assertTrue(question)

    def test_scope_anchor_survives_insufficient_and_single_card_answer(self):
        cards = [{'card_key': 'a', 'card_name': 'A', 'issuer': '발급사'},
                 {'card_key': 'b', 'card_name': 'B', 'issuer': '발급사'}]
        turn = {'state': 'completed', 'answer': {'answer_status': 'insufficient_evidence',
                'cards': [], 'recommendations': [], 'usage': {'answer': {'conversation_scope': {
                    'scope': 'previous', 'reference_groups': [cards]}}}}}
        self.assertEqual([r['card_key'] for r in chat.card_references([turn])], ['a', 'b'])
        turn['answer']['answer_status'] = 'answered'
        turn['answer']['cards'] = [cards[0]]
        turn['answer']['recommendations'] = [{'card_key': 'a'}]
        self.assertEqual([r['card_key'] for r in chat.card_references([turn])], ['a', 'b'])

    def test_latest_scope_snapshot_keeps_original_group_order(self):
        a = {'card_key': 'a', 'card_name': 'A', 'issuer': '발급사'}
        b = {'card_key': 'b', 'card_name': 'B', 'issuer': '발급사'}
        history = [{'state': 'completed', 'answer': {'answer_status': 'answered',
                    'cards': [b], 'recommendations': [{'card_key': 'b'}]}},
                   {'state': 'completed', 'answer': {'answer_status': 'insufficient_evidence',
                    'usage': {'answer': {'conversation_scope': {'scope': 'previous', 'reference_groups': [[a], [b]]}}}}}]
        refs = chat.card_references(history)
        self.assertEqual([(r['ref'], r['card_key']) for r in refs], [('t1r1', 'a'), ('t2r1', 'b')])
