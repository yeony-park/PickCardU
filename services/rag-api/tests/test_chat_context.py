import importlib
import unittest


class ChatContextTest(unittest.TestCase):
    def setUp(self):
        try:
            self.build = importlib.import_module('pickcardu_rag_api.chat').build_chat_context
        except (ModuleNotFoundError, AttributeError):
            self.fail('chat context is not implemented')

    def test_recommendation_order_not_search_order(self):
        turn = {'query': '카드 추천', 'state': 'completed', 'answer': {
            'answer_status': 'answered', 'answer': '두 카드를 추천합니다.',
            'cards': [{'card_key': 'a', 'card_name': 'A', 'issuer': '카드사'},
                      {'card_key': 'b', 'card_name': 'B', 'issuer': '카드사'}],
            'recommendations': [{'card_key': 'b', 'reason': '혜택 B'}, {'card_key': 'a', 'reason': '혜택 A'}]}}
        context = self.build([turn], '첫 번째 카드 연회비는?')
        self.assertEqual(len(context), 3)
        self.assertIn('1. B', context[1]['content'])
        self.assertLess(context[1]['content'].index('1. B'), context[1]['content'].index('2. A'))
        self.assertEqual(context[-1]['content'], '첫 번째 카드 연회비는?')

    def test_context_filters_and_caps_pairs_and_characters(self):
        def turn(state, status, text):
            return {'query': text, 'state': state, 'answer': {'answer_status': status, 'answer': text, 'cards': [], 'recommendations': []}}
        turns = [turn('completed', 'answered', x) for x in ('old', 'middle', 'recent')]
        turns += [turn('failed', 'answered', 'failed'), turn('pending', 'answered', 'pending'), turn('completed', 'insufficient_evidence', 'insufficient')]
        context = self.build(turns, 'new')
        self.assertEqual([m['content'] for m in context if m['role'] == 'user'], ['middle', 'recent', 'new'])
        huge = self.build([turn('completed', 'answered', 'a'*5000)], 'b'*500)
        self.assertLessEqual(sum(len(m['content']) for m in huge), 6000)
        self.assertEqual(huge[-1]['content'], 'b'*500)
