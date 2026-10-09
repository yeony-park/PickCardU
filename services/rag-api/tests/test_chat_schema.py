from pathlib import Path
import sqlite3
import tempfile
import unittest
from uuid import uuid4

from pydantic import ValidationError
from pickcardu_rag_api import chat_models


SCHEMA = Path(__file__).resolve().parents[1] / 'src/pickcardu_rag_api/chat_schema.sql'


class ChatSchemaTest(unittest.TestCase):
    def test_create_survey_and_turn_wallet_use_separate_strict_contracts(self):
        self.assertIn('survey_context', chat_models.CreateConversationRequest.model_fields)
        survey = {'monthly_spending': '30-50', 'spending_categories': ['교통', '카페'],
                  'preferred_benefits': ['항공 마일리지', '할인']}
        request = chat_models.CreateConversationRequest(client_conversation_id=uuid4(), survey_context=survey)
        self.assertEqual(request.survey_context.spending_categories, ['카페', '교통'])
        self.assertEqual(request.survey_context.preferred_benefits, ['할인', '항공 마일리지'])
        empty = chat_models.CreateConversationRequest(client_conversation_id=uuid4(), survey_context={})
        self.assertIsNone(empty.survey_context)
        for invalid in ({**survey, 'spending_categories': ['카페', '카페']},
                        {**survey, 'monthly_spending': 'unknown'}, {**survey, 'unexpected': True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                chat_models.CreateConversationRequest(client_conversation_id=uuid4(), survey_context=invalid)
        keys = [f'issuer/card-{i}' for i in range(106)]
        turn = chat_models.ChatRequest(query='내 카드 혜택', client_request_id=uuid4(),
            wallet_context={'status': 'ready', 'card_keys': keys})
        self.assertEqual(turn.wallet_context.card_keys, keys)
        self.assertIsNone(turn.profile)
        for wallet in ({'status': 'ready', 'card_keys': []}, {'status': 'empty', 'card_keys': ['a']},
                       {'status': 'ready', 'card_keys': ['a', 'a']},
                       {'status': 'ready', 'card_keys': keys + ['issuer/another']},
                       {'status': 'needs_review', 'card_keys': ['a']},
                       {'status': 'ready', 'card_keys': ['  a  ']}):
            with self.subTest(wallet=wallet), self.assertRaises(ValidationError):
                chat_models.ChatRequest(query='내 카드', client_request_id=uuid4(), wallet_context=wallet)

    def test_execution_snapshot_rejects_invalid_combinations_and_retains_recall_order(self):
        self.assertTrue(hasattr(chat_models, 'ExecutionContext'))
        base = {'standalone_query': '카드 이름', 'scope': 'previous', 'operation': 'recall',
                'retrieval_query': None, 'recall_items': [
                    {'source_ref': 't1r2', 'card_key': 'b', 'card_name': 'B', 'issuer': '발급사'},
                    {'source_ref': 't1r1', 'card_key': 'a', 'card_name': 'A', 'issuer': '발급사'}]}
        result = chat_models.ExecutionContext.model_validate(base)
        self.assertEqual([item.card_key for item in result.recall_items], ['b', 'a'])
        for invalid in ({**base, 'scope': 'global'}, {**base, 'recall_items': []},
                        {**base, 'retrieval_query': '검색하면 안 됨'},
                        {**base, 'use_wallet': True, 'wallet_operation': 'lookup'},
                        {'standalone_query': '확인', 'scope': 'clarification', 'operation': 'retrieve',
                         'clarification': '카드를 지정해 주세요.', 'target_card_keys': ['a']},
                        {'standalone_query': '검색', 'scope': 'global', 'operation': 'retrieve',
                         'retrieval_query': '가' * 1025},
                        {'standalone_query': '검색', 'retrieval_query': '검색', 'wallet_operation': 'lookup'},
                        {'standalone_query': '검색', 'retrieval_query': '검색', 'scope': 'previous'},
                        {'standalone_query': '검색', 'retrieval_query': '검색', 'use_survey': True},
                        {'standalone_query': '검색', 'retrieval_query': '검색',
                         'personalization_context': {'survey_context': {'monthly_spending': '30-50'}}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                chat_models.ExecutionContext.model_validate(invalid)

    def test_schema_preserves_owner_and_blocks_parallel_pending_turns(self):
        self.assertTrue(SCHEMA.is_file(), 'chat-only schema has not been implemented')
        with tempfile.TemporaryDirectory() as temporary, sqlite3.connect(Path(temporary) / 'chat.sqlite') as database:
            database.execute('PRAGMA foreign_keys=ON')
            database.executescript(SCHEMA.read_text())
            database.execute(
                'INSERT INTO conversations(id,owner_hash,client_conversation_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                ('chat-a', 'owner-a', 'draft-a', '새 채팅', '2026-10-03T00:00:00Z', '2026-10-03T00:00:00Z'),
            )
            values = ('t1', 'chat-a', 1, 'r1', '{}', '카드 추천', 'pending', 'attempt-1', '2026-10-03T00:00:00Z', 9999999999.0)
            sql = 'INSERT INTO turns(id,conversation_id,seq,client_request_id,request_json,query,state,attempt_id,created_at,lease_expires_at) VALUES(?,?,?,?,?,?,?,?,?,?)'
            database.execute(sql, values)
            with self.assertRaises(sqlite3.IntegrityError):
                database.execute(sql, ('t2', 'chat-a', 2, 'r2', '{}', '후속 질문', 'pending', 'attempt-2', values[-2], values[-1]))
            self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(database.execute('SELECT owner_hash FROM conversations').fetchone()[0], 'owner-a')
            self.assertEqual(database.execute('SELECT count(*) FROM turns').fetchone()[0], 1)
            self.assertEqual(database.execute('PRAGMA foreign_key_check').fetchall(), [])
