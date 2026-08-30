from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag_api import storage  # noqa: E402


SHORT_SEED = '[{"username":"local","password":"x","role":"user"}]'


class StorageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "service.sqlite3"
        self.connection = storage.connect(self.path)
        storage.init(self.connection)

    def tearDown(self) -> None:
        self.connection.close()
        self.temporary.cleanup()

    def test_short_seed_requires_explicit_development(self) -> None:
        for environment in (None, "unknown", "production"):
            with self.subTest(environment=environment):
                source = {"PICKCARDU_SEED_ACCOUNTS_JSON": SHORT_SEED}
                if environment is not None:
                    source["PICKCARDU_ENV"] = environment
                with self.assertRaises(ValueError):
                    storage.seed_from_env(self.connection, source)
        self.assertEqual(storage.seed_from_env(self.connection, {"PICKCARDU_ENV": "development", "PICKCARDU_SEED_ACCOUNTS_JSON": SHORT_SEED}), 1)
        self.assertTrue(storage.verify_password(storage.get_user(self.connection, username="local"), "x"))

    def test_session_is_opaque_and_only_hash_is_stored(self) -> None:
        user = storage.create_user(self.connection, "secure", "123456789012")
        token = storage.create_session(self.connection, user["id"])
        self.assertGreaterEqual(len(token), 43)
        row = self.connection.execute("SELECT token_hash FROM sessions").fetchone()
        self.assertNotEqual(row[0], token)
        self.assertEqual(len(row[0]), 64)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_owned_conversation_delete_cascades_messages_and_runs(self) -> None:
        owner = storage.create_user(self.connection, "owner", "123456789012")
        other = storage.create_user(self.connection, "other", "123456789012")
        conversation = storage.create_conversation(self.connection, owner["id"], None)
        created = storage.create_chat_request(self.connection, owner["id"], conversation["id"], "query", {})
        self.assertIsNone(storage.get_conversation(self.connection, other["id"], conversation["id"]))
        self.assertFalse(storage.delete_conversation(self.connection, other["id"], conversation["id"]))
        self.assertTrue(storage.delete_conversation(self.connection, owner["id"], conversation["id"]))
        self.assertIsNone(storage.get_run(self.connection, owner["id"], created["run"]["id"]))
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM messages").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
