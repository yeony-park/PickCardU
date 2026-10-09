from __future__ import annotations

import importlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path


class ChatStoreTest(unittest.TestCase):
    def setUp(self):
        try:
            module = importlib.import_module('pickcardu_rag_api.chat_store')
        except ModuleNotFoundError:
            self.fail('ChatStore is not implemented')
        self.Store, self.Error = module.ChatStore, module.ChatStoreError
        self.temporary = tempfile.TemporaryDirectory(prefix='pickcardu-chat-test-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'chat.sqlite'
        self.store = self.Store(self.path)
        self.request = {'query': '편의점 카드 추천', 'top_k': 3, 'profile': None}

    def chat(self, owner='a', draft='draft'):
        return self.store.create_conversation(owner, draft)

    def reserve(self, chat, rid='request', **kwargs):
        return self.store.reserve_turn('a', chat['id'], rid, self.request, **kwargs)

    def finish(self, reservation, status='answered'):
        return self.store.complete_turn(reservation['turn']['id'], reservation['attempt_id'],
            {'answer_status': status, 'answer': '답변', 'recommendations': []}, standalone_query='독립 질문')

    def assert_code(self, code, operation):
        with self.assertRaises(self.Error) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def test_lazy_reopen_owner_and_multiple_chats(self):
        self.assertFalse(self.path.exists())
        chat = self.chat()
        self.assertEqual(self.chat()['id'], chat['id'])
        self.assertEqual(self.store.list_conversations('a')['conversations'], [])
        reserved = self.reserve(chat)
        page = self.Store(self.path).list_turns('a', chat['id'])
        self.assertEqual(page['turns'][0]['query'], self.request['query'])
        self.assertEqual(page['turns'][0]['id'], reserved['turn']['id'])
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.get_conversation('b', chat['id']))
        self.finish(reserved)
        other = self.chat(draft='another')
        self.finish(self.reserve(other))
        self.assertEqual(len(self.store.list_conversations('a')['conversations']), 2)
        self.assertEqual(self.store.list_conversations('b')['conversations'], [])

    def test_idempotency_conflict_and_failed_explicit_retry(self):
        chat = self.chat()
        first = self.reserve(chat)
        self.assert_code('TURN_IN_PROGRESS', lambda: self.reserve(chat))
        self.assert_code('CONVERSATION_BUSY', lambda: self.reserve(chat, 'other'))
        self.assert_code('REQUEST_ID_CONFLICT', lambda: self.store.reserve_turn('a', chat['id'], 'request',
            {**self.request, 'top_k': 5}))
        self.store.fail_turn(first['turn']['id'], first['attempt_id'],
            {'code': 'LLM_UNAVAILABLE', 'message': '실패', 'retryable': True, 'request_id': 'x'})
        replay = self.reserve(chat)
        self.assertFalse(replay['should_run'])
        retry = self.reserve(chat, retry_failed=True)
        self.assertNotEqual(retry['attempt_id'], first['attempt_id'])
        self.assert_code('TURN_ATTEMPT_STALE', lambda: self.finish(first))
        self.finish(retry)
        replay = self.reserve(chat)
        self.assertFalse(replay['should_run'])
        self.assertEqual(replay['turn']['id'], first['turn']['id'])
        self.assertEqual(len(self.store.list_turns('a', chat['id'])['turns']), 1)

    def test_lease_and_pagination_and_answered_context(self):
        chat = self.chat()
        first = self.reserve(chat, 'r1')
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE turns SET lease_expires_at=? WHERE id=?', (time.time()-1, first['turn']['id']))
        self.assertEqual(self.store.list_turns('a', chat['id'])['turns'][0]['error']['code'], 'TURN_INTERRUPTED')
        self.finish(self.reserve(chat, 'r2'), 'insufficient_evidence')
        self.finish(self.reserve(chat, 'r3'))
        self.finish(self.reserve(chat, 'r4'))
        page = self.store.list_turns('a', chat['id'], limit=2)
        self.assertEqual([t['seq'] for t in page['turns']], [3, 4])
        older = self.store.list_turns('a', chat['id'], limit=2, before_seq=page['next_before_seq'])
        self.assertEqual([t['seq'] for t in older['turns']], [1, 2])
        self.assertIsNone(older['next_before_seq'])
        self.assertEqual([t['seq'] for t in self.store.completed_turns('a', chat['id'], before_seq=5)], [3, 4])
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.completed_turns('b', chat['id'], before_seq=5))

    def test_schema_corruption_and_lock_non_destructive(self):
        self.path.write_bytes(b'not a sqlite database')
        before = self.path.read_bytes()
        self.assert_code('CHAT_STORAGE_UNAVAILABLE', lambda: self.chat())
        self.assertEqual(self.path.read_bytes(), before)
        alternate = self.path.with_name('version.sqlite')
        with sqlite3.connect(alternate) as db:
            db.execute('PRAGMA user_version=99')
        self.assert_code('CHAT_STORAGE_UNAVAILABLE', lambda: self.Store(alternate).create_conversation('a', 'd'))
        with sqlite3.connect(alternate) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 99)

    def test_cursor_is_owner_scoped_and_backup_is_consistent(self):
        for draft in ('d1', 'd2', 'd3'):
            self.finish(self.reserve(self.chat(draft=draft)))
        first = self.store.list_conversations('a', limit=2)
        second = self.store.list_conversations('a', limit=2, cursor=first['next_cursor'])
        ids = [c['id'] for c in first['conversations'] + second['conversations']]
        self.assertEqual(len(set(ids)), 3)
        self.assertEqual(self.store.list_conversations('b', cursor=first['next_cursor'])['conversations'], [])
        self.assert_code('INVALID_REQUEST', lambda: self.store.list_conversations('a', cursor='not-json'))
        with sqlite3.connect(self.path) as source, sqlite3.connect(self.path.with_name('backup.sqlite')) as target:
            source.backup(target)
            self.assertEqual(target.execute('SELECT count(*) FROM turns').fetchone()[0], 3)

    def test_wal_reader_does_not_reserve_write_lock(self):
        chat = self.chat()
        self.finish(self.reserve(chat))
        with sqlite3.connect(self.path) as writer:
            writer.execute('BEGIN IMMEDIATE')
            self.assertEqual(self.store.get_conversation('a', chat['id'])['id'], chat['id'])
            self.assertEqual(len(self.store.list_conversations('a')['conversations']), 1)
            self.assertEqual(len(self.store.completed_turns('a', chat['id'], before_seq=2)), 1)
            self.assertEqual(len(self.store.list_turns('a', chat['id'])['turns']), 1)

    def test_delete_is_owner_scoped_and_removes_only_that_conversation_and_turns(self):
        chat, other = self.chat(), self.chat(draft='other')
        self.finish(self.reserve(chat))
        self.finish(self.reserve(other))
        self.assertTrue(callable(getattr(self.store, 'delete_conversation', None)))
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.delete_conversation('b', chat['id']))
        self.assertEqual(len(self.store.list_turns('a', chat['id'])['turns']), 1)
        self.store.delete_conversation('a', chat['id'])
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.list_turns('a', chat['id']))
        self.assertEqual([c['id'] for c in self.Store(self.path).list_conversations('a')['conversations']], [other['id']])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM turns WHERE conversation_id=?', (chat['id'],)).fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT count(*) FROM turns WHERE conversation_id=?', (other['id'],)).fetchone()[0], 1)
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.delete_conversation('a', chat['id']))

    def test_delete_pending_conversation_is_rejected_without_removing_rows(self):
        chat = self.chat()
        reservation = self.reserve(chat)
        self.assertTrue(callable(getattr(self.store, 'delete_conversation', None)))
        self.assert_code('CONVERSATION_BUSY', lambda: self.store.delete_conversation('a', chat['id']))
        self.assertEqual(self.store.list_turns('a', chat['id'])['turns'][0]['state'], 'pending')
        self.finish(reservation)
        self.store.delete_conversation('a', chat['id'])
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.reserve(chat, 'new-request'))

    def test_survey_is_immutable_canonical_and_conversation_scoped(self):
        self.assertIn('survey_context', self.chat(), 'conversation survey is missing')
        survey = {'monthly_spending': '30-50', 'spending_categories': ['교통', '카페'],
                  'preferred_benefits': ['할인']}
        saved = self.store.create_conversation('a', 'survey', survey)
        expected = {**survey, 'spending_categories': ['카페', '교통']}
        self.assertEqual(saved['survey_context'], expected)
        replay = self.Store(self.path).create_conversation('a', 'survey', expected)
        self.assertEqual(replay['id'], saved['id'])
        self.assertFalse(replay['_created'])
        self.assert_code('CONVERSATION_CONTEXT_CONFLICT',
                         lambda: self.store.create_conversation('a', 'survey', None))
        self.assertIsNone(self.store.create_conversation('a', 'empty', {
            'monthly_spending': None, 'spending_categories': [], 'preferred_benefits': []})['survey_context'])
        self.assertIsNone(self.store.create_conversation('a', 'next')['survey_context'])
        self.assert_code('CONVERSATION_NOT_FOUND', lambda: self.store.get_conversation('b', saved['id']))

    def install_v1_fixture(self):
        fixture = Path(__file__).with_name('fixtures') / 'chat_schema_v1.sql'
        raw = '{"profile":null,"query":"카페","top_k":3}'
        with sqlite3.connect(self.path) as db:
            db.executescript(fixture.read_text(encoding='utf-8'))
            db.execute('INSERT INTO conversations VALUES(?,?,?,?,?,?)',
                       ('legacy-chat', 'a', 'legacy-draft', '기존 대화', 'created', 'updated'))
            for seq, state in ((1, 'completed'), (2, 'failed')):
                db.execute('INSERT INTO turns(id,conversation_id,seq,client_request_id,request_json,query,state,'
                           'attempt_id,answer_json,error_json,created_at,completed_at,lease_expires_at) '
                           'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                           (f'legacy-{seq}', 'legacy-chat', seq, f'request-{seq}', raw, '카페', state,
                            f'attempt-{seq}', json.dumps({'answer': '기존 답변', 'answer_status': 'answered',
                                                       'recommendations': []}) if seq == 1 else None,
                            json.dumps({'code': 'LLM_UNAVAILABLE', 'message': '실패', 'retryable': True,
                                        'request_id': 'fixture'}) if seq == 2 else None,
                            'created', 'completed', 0.0))
        return raw

    def test_v1_rows_and_absent_wallet_request_survive_migration_and_replay(self):
        raw = self.install_v1_fixture()
        chat = self.store.get_conversation('a', 'legacy-chat')
        self.assertIn('survey_context', chat, 'v1 conversation has not migrated')
        self.assertIsNone(chat['survey_context'])
        self.assertEqual((chat['title'], chat['created_at'], chat['updated_at']),
                         ('기존 대화', 'created', 'updated'))
        request = {'query': '카페', 'profile': None, 'top_k': 3, 'wallet_context': None}
        replay = self.store.reserve_turn('a', chat['id'], 'request-1', request)
        self.assertFalse(replay['should_run'])
        self.assertEqual(replay['turn']['answer']['answer'], '기존 답변')
        self.assertIsNone(replay['turn']['execution_context'])
        retry = self.store.reserve_turn('a', chat['id'], 'request-2', request, retry_failed=True)
        self.assertTrue(retry['should_run'])
        self.assertEqual(retry['turn']['seq'], 2)
        self.assert_code('REQUEST_ID_CONFLICT', lambda: self.store.reserve_turn('a', chat['id'], 'request-1',
            {**request, 'wallet_context': {'status': 'ready', 'card_keys': ['issuer/card']}}))
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT request_json FROM turns ORDER BY seq').fetchall(), [(raw,), (raw,)])
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])

    def execution_context(self):
        return {'standalone_query': '편의점 카드 추천', 'retrieval_query': '편의점 카드 추천',
                'scope': 'global', 'operation': 'retrieve', 'target_card_keys': None,
                'use_survey': False, 'use_wallet': False, 'wallet_operation': 'none',
                'wallet_compare_scope': 'relevant', 'clarification': None, 'recall_items': [],
                'overridden_survey_fields': [], 'personalization_context': None}

    def test_execution_snapshot_is_write_once_attempt_guarded_and_retained_on_retry(self):
        self.assertTrue(callable(getattr(self.store, 'save_execution_context', None)),
                        'execution snapshot persistence is missing')
        chat = self.chat()
        first = self.reserve(chat)
        context = self.execution_context()
        self.store.save_execution_context(first['turn']['id'], first['attempt_id'], context)
        self.store.fail_turn(first['turn']['id'], first['attempt_id'],
            {'code': 'LLM_UNAVAILABLE', 'message': '실패', 'retryable': True, 'request_id': 'x'})
        retry = self.reserve(chat, retry_failed=True)
        self.assertEqual(retry['turn']['execution_context'], context)
        self.assert_code('TURN_ATTEMPT_STALE', lambda: self.store.save_execution_context(
            first['turn']['id'], first['attempt_id'], context))
        self.store.save_execution_context(retry['turn']['id'], retry['attempt_id'], context)
        self.assert_code('TURN_EXECUTION_CONFLICT', lambda: self.store.save_execution_context(
            retry['turn']['id'], retry['attempt_id'], {**context, 'standalone_query': '다른 질문'}))
        self.finish(retry)
        self.assertEqual(self.store.list_turns('a', chat['id'])['turns'][0]['execution_context'], context)
        self.store.delete_conversation('a', chat['id'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM turns').fetchone()[0], 0)

    def test_expired_attempt_cannot_save_execution_snapshot(self):
        self.assertTrue(callable(getattr(self.store, 'save_execution_context', None)))
        reserved = self.reserve(self.chat())
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE turns SET lease_expires_at=0 WHERE id=?', (reserved['turn']['id'],))
        self.assert_code('TURN_ATTEMPT_STALE', lambda: self.store.save_execution_context(
            reserved['turn']['id'], reserved['attempt_id'], self.execution_context()))

    def test_unknown_existing_schema_is_rejected_before_any_alter(self):
        for version in (0, 1, 2, 99):
            path = self.path.with_name(f'unknown-{version}.sqlite')
            with self.subTest(version=version):
                with sqlite3.connect(path) as db:
                    db.execute('CREATE TABLE unrelated(value TEXT)')
                    db.execute("INSERT INTO unrelated VALUES('keep')")
                    db.execute(f'PRAGMA user_version={version}')
                self.assert_code('CHAT_STORAGE_UNAVAILABLE', lambda: self.Store(path).create_conversation('a', 'd'))
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], version)
                    self.assertEqual(db.execute('SELECT value FROM unrelated').fetchall(), [('keep',)])
                    self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),
                                     [('unrelated',)])


if __name__ == '__main__':
    unittest.main()
