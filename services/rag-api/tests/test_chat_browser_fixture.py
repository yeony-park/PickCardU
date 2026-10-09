import unittest

from chat_browser_fixture import BrowserProvider
from pickcardu_rag.answering import RewriteOutput


class BrowserProviderTest(unittest.TestCase):
    def test_fixture_accepts_followup_provider_contract(self):
        provider = BrowserProvider()
        rewritten, usage = provider.rewrite([{'role': 'user', 'content': '주유 카드 추천'}], references=[])
        self.assertIsInstance(rewritten, RewriteOutput)
        self.assertEqual(rewritten.standalone_query, '주유 카드 추천')
        self.assertEqual(usage['model'], 'fixture')
        answer, _ = provider.answer('주유 카드 추천', [{'card_key': 'card_a', 'chunk_id': 'a1'}], comparison=True)
        self.assertEqual(answer.recommendations[0].card_key, 'card_a')
