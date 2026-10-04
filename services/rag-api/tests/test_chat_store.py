from __future__ import annotations

import importlib
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


if __name__ == '__main__':
    unittest.main()
