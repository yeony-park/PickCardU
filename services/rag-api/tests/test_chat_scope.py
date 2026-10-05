import unittest

from pickcardu_rag.answering import RewriteOutput
from pickcardu_rag_api import chat


class ChatScopeTest(unittest.TestCase):
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
