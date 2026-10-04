from pathlib import Path
import sqlite3
import tempfile
import unittest


SCHEMA = Path(__file__).resolve().parents[1] / 'src/pickcardu_rag_api/chat_schema.sql'


class ChatSchemaTest(unittest.TestCase):
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
            self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], 1)
            self.assertEqual(database.execute('SELECT owner_hash FROM conversations').fetchone()[0], 'owner-a')
            self.assertEqual(database.execute('SELECT count(*) FROM turns').fetchone()[0], 1)
            self.assertEqual(database.execute('PRAGMA foreign_key_check').fetchall(), [])
