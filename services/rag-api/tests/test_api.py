from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "services/rag-api/src"), str(ROOT / "packages/rag-core/src")]

from pickcardu_rag import EmbeddingUnavailable, LlmUnavailable  # noqa: E402
from pickcardu_rag_api.main import create_app  # noqa: E402
from pickcardu_rag_api import storage  # noqa: E402
from support import FakeProvider, FakeReranker, build_release, settings  # noqa: E402


SEEDS = '[{"username":"user","password":"short","role":"user"},{"username":"dev","password":"short","role":"developer"}]'


class ApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        build_release(self.root / "runtime")
        self.provider = FakeProvider()
        self.app = create_app(settings(self.root), provider=self.provider, reranker=FakeReranker())
        self.environment = patch.dict(os.environ, {"PICKCARDU_ENV": "development", "PICKCARDU_SEED_ACCOUNTS_JSON": SEEDS}, clear=False)
        self.environment.start()
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://testserver", headers={"Origin": "http://testserver"})
        await self.client.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.client.__aexit__(None, None, None)
        await self.lifespan.__aexit__(None, None, None)
        self.environment.stop()
        for path in self.root.rglob("*"):
            try:
                os.chmod(path, 0o755 if path.is_dir() else 0o644)
            except FileNotFoundError:
                pass
        self.temporary.cleanup()

    async def login(self, username: str = "user") -> None:
        response = await self.client.post("/v1/auth/login", json={"username": username, "password": "short"})
        self.assertEqual(response.status_code, 200, response.text)

    async def test_auth_profile_catalog_and_role_boundary(self) -> None:
        response = await self.client.post("/v1/auth/login", json={"username": "user", "password": "short"})
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("SameSite=lax", response.headers["set-cookie"])
        self.assertEqual((await self.client.get("/v1/auth/session")).json()["profile_state"], "required")
        profile = {"display_name": "테스터", "benefit_categories": ["cafe"], "age_band": "30s"}
        self.assertEqual((await self.client.put("/v1/profile", json=profile)).json()["profile_state"], "complete")
        cards = (await self.client.get("/v1/catalog/cards")).json()["cards"]
        self.assertEqual(cards, [{"card_key": "issuer/card-a", "card_name": "Card A", "issuer": "Issuer"}, {"card_key": "issuer/card-b", "card_name": "Card B", "issuer": "Issuer"}])
        ready = await self.client.get("/v1/health/ready")
        self.assertEqual(ready.status_code, 200)
        self.assertEqual((ready.json()["deployment"], ready.json()["process_scope"]), ("local_internal", "single_process"))
        self.assertEqual((await self.client.get("/internal/v1/lab/options")).status_code, 403)
        self.assertEqual((await self.client.post("/v1/auth/logout")).status_code, 200)
        self.assertEqual((await self.client.get("/v1/auth/session")).status_code, 401)

    async def test_origin_rate_limit_expiry_and_uniform_login_failure(self) -> None:
        forbidden = await self.client.post("/v1/auth/login", headers={"Origin": "http://evil.test"}, json={"username": "user", "password": "short"})
        self.assertEqual(forbidden.status_code, 403)
        unknown = await self.client.post("/v1/auth/login", json={"username": "missing", "password": "wrong"})
        wrong = await self.client.post("/v1/auth/login", json={"username": "user", "password": "wrong"})
        self.assertEqual((unknown.status_code, unknown.json()["code"]), (wrong.status_code, wrong.json()["code"]))
        for _ in range(4):
            await self.client.post("/v1/auth/login", json={"username": "blocked", "password": "wrong"})
        fifth = await self.client.post("/v1/auth/login", json={"username": "blocked", "password": "wrong"})
        limited = await self.client.post("/v1/auth/login", json={"username": "blocked", "password": "wrong"})
        self.assertEqual((fifth.status_code, limited.status_code), (401, 429))
        await self.login()
        connection = storage.connect(self.root / "service.sqlite3")
        connection.execute("UPDATE sessions SET expires_at='2000-01-01T00:00:00Z'")
        connection.close()
        self.assertEqual((await self.client.get("/v1/auth/session")).status_code, 401)

    async def test_conversation_ownership_and_account_lock(self) -> None:
        await self.login()
        conversation = (await self.client.post("/v1/conversations", json={})).json()["conversation"]
        connection = storage.connect(self.root / "service.sqlite3")
        user_id = storage.get_user(connection, username="user")["id"]
        connection.close()
        self.app.state.account_locks.acquire(user_id, "user_chat")
        try:
            self.assertEqual((await self.client.post(f"/v1/conversations/{conversation['id']}/messages", json={"query": "카페"})).status_code, 409)
            self.assertEqual((await self.client.delete(f"/v1/conversations/{conversation['id']}")).status_code, 409)
        finally:
            self.app.state.account_locks.release(user_id, "user_chat")
        await self.login("dev")
        self.assertEqual((await self.client.get(f"/v1/conversations/{conversation['id']}")).status_code, 404)

    async def test_chat_persists_grounded_result_and_never_sends_profile(self) -> None:
        await self.login()
        await self.client.put("/v1/profile", json={"display_name": "SECRET_PROFILE", "benefit_categories": ["cafe"]})
        conversation = (await self.client.post("/v1/conversations", json={"title": "카드"})).json()["conversation"]
        response = await self.client.post(f"/v1/conversations/{conversation['id']}/messages", json={"query": "카페 혜택 좋은 카드"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["claims"][0]["citations"], [result["evidence"][0]["chunk_id"]])
        restored = (await self.client.get(f"/v1/conversations/{conversation['id']}")).json()
        self.assertEqual(restored["messages"][-1]["result"]["run_id"], result["run_id"])
        payload = str(self.provider.embedding_queries) + str(self.provider.answer_inputs)
        self.assertNotIn("SECRET_PROFILE", payload)

    async def test_developer_lab_history_detail_delete(self) -> None:
        await self.login("dev")
        options = (await self.client.get("/internal/v1/lab/options")).json()
        self.assertNotIn("tokenizer", options)
        self.assertNotIn("mmr", str(options).casefold())
        invalid = await self.client.post("/internal/v1/lab/runs", json={"query": "카페", "config": {"tokenizer": "kiwi"}})
        self.assertEqual((invalid.status_code, invalid.json()["code"]), (422, "RUN_CONFIG_INVALID"))
        response = await self.client.post("/internal/v1/lab/runs", json={"query": "카페 혜택", "config": {"reranker": "off", "include_llm": False}})
        self.assertEqual(response.status_code, 200, response.text)
        run_id = response.json()["run_id"]
        self.assertEqual(len((await self.client.get("/internal/v1/lab/runs")).json()["runs"]), 1)
        self.assertEqual((await self.client.get(f"/internal/v1/lab/runs/{run_id}")).json()["run"]["trace"]["query_type"], "semantic")
        second = await self.client.post("/internal/v1/lab/runs", json={"query": "주유 혜택", "config": {"reranker": "off", "include_llm": False}})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["counts"], {"total_run_count": 2, "unique_config_count": 1, "config_seen_count": 2})
        self.assertEqual((await self.client.delete(f"/internal/v1/lab/runs/{run_id}")).status_code, 200)

    async def test_lab_delete_requires_lock_and_terminal_status(self) -> None:
        await self.login("dev")
        connection = storage.connect(self.root / "service.sqlite3")
        developer = storage.get_user(connection, username="dev")
        created = storage.create_lab_run(connection, developer["id"], "카페", {})
        connection.close()
        self.app.state.account_locks.acquire(developer["id"], "developer_lab")
        try:
            self.assertEqual((await self.client.delete(f"/internal/v1/lab/runs/{created['id']}")).status_code, 409)
        finally:
            self.app.state.account_locks.release(developer["id"], "developer_lab")
        self.assertEqual((await self.client.delete(f"/internal/v1/lab/runs/{created['id']}")).status_code, 409)
        connection = storage.connect(self.root / "service.sqlite3")
        storage.update_run(connection, developer["id"], created["id"], status="failed")
        connection.close()
        self.assertEqual((await self.client.delete(f"/internal/v1/lab/runs/{created['id']}")).status_code, 200)

    async def test_lab_terminal_storage_failures_are_stable_and_recorded_once(self) -> None:
        await self.login("dev")
        for failure, expected_status, expected_code in (
            (LookupError("gone"), 404, "RUN_NOT_FOUND"),
            (sqlite3.OperationalError("offline"), 503, "PERSISTENCE_UNAVAILABLE"),
        ):
            with self.subTest(code=expected_code), patch.object(storage, "complete_lab", side_effect=failure), patch.object(storage, "fail_run", wraps=storage.fail_run) as record_failure:
                response = await self.client.post("/internal/v1/lab/runs", json={"query": "카페", "config": {"reranker": "off", "include_llm": False}})
                self.assertEqual((response.status_code, response.json()["code"]), (expected_status, expected_code), response.text)
                self.assertEqual(record_failure.call_count, 1)

        with patch.object(self.provider, "embed", side_effect=EmbeddingUnavailable("offline")), patch.object(storage, "fail_run", side_effect=sqlite3.OperationalError("storage offline")) as record_failure:
            response = await self.client.post("/internal/v1/lab/runs", json={"query": "카페", "config": {"reranker": "off"}})
            self.assertEqual((response.status_code, response.json()["code"]), (503, "PERSISTENCE_UNAVAILABLE"), response.text)
            self.assertEqual(record_failure.call_count, 1)

    async def test_llm_failure_returns_and_restores_retrieval_preview(self) -> None:
        await self.login()
        conversation = (await self.client.post("/v1/conversations", json={})).json()["conversation"]
        original = self.provider.answer
        self.provider.answer = lambda query, evidence: (_ for _ in ()).throw(LlmUnavailable("offline"))
        response = await self.client.post(f"/v1/conversations/{conversation['id']}/messages", json={"query": "카페 혜택"})
        self.provider.answer = original
        self.assertEqual(response.status_code, 503)
        preview = response.json()["retrieval_preview"]
        self.assertEqual(preview["status"], "retrieval_only")
        restored = (await self.client.get(f"/v1/conversations/{conversation['id']}")).json()["messages"][-1]["result"]
        self.assertEqual(restored, preview)

    async def test_auth_survives_missing_active_release(self) -> None:
        (self.root / "runtime/active-index.json").unlink()
        await self.login()
        self.assertEqual((await self.client.get("/v1/profile")).status_code, 200)
        self.assertEqual((await self.client.get("/v1/catalog/cards")).status_code, 503)
        self.assertEqual((await self.client.get("/v1/health/ready")).status_code, 503)


if __name__ == "__main__":
    unittest.main()
