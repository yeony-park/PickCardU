from __future__ import annotations

import inspect
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pickcardu_rag.answering import AnswerOutput
from pickcardu_rag.errors import LlmUnavailable
from pickcardu_rag_api.chat_store import ChatStore, ChatStoreError
from pickcardu_rag_api.main import create_app
from support import FakeProvider, FakeReranker, build_release, settings


class ChatProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.rewrites = []
        self.rewrite_result = 'Card A 연회비는?'
        self.fail_answer = False

    def rewrite(self, context):
        self.rewrites.append(context)
        return self.rewrite_result, {'model': self.llm_model, 'usage': {'total_tokens': 3}}

    def answer(self, query, evidence):
        if self.fail_answer:
            raise LlmUnavailable('secret provider detail')
        return super().answer(query, evidence)


class ChatApiTest(unittest.TestCase):
    def setUp(self):
        self.assertIn('chat_store', inspect.signature(create_app).parameters, 'chat API is not implemented')
        self.temporary = tempfile.TemporaryDirectory(prefix='pickcardu-chat-http-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        build_release(self.root / 'runtime')
        self.store = ChatStore(self.root / 'chat.sqlite')
        self.provider = ChatProvider()
        self.app = create_app(settings(self.root), provider=self.provider, reranker=FakeReranker(), chat_store=self.store)
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.headers = {'Origin': 'http://testserver'}

    def session(self, client=None):
        return (client or self.client).post('/v1/browser-session', json={}, headers=self.headers)

    def conversation(self, client=None, draft=None):
        return (client or self.client).post('/v1/conversations', json={'client_conversation_id': draft or str(uuid.uuid4())}, headers=self.headers)

    def send(self, cid, payload=None):
        return self.client.post(f'/v1/conversations/{cid}/messages', json=payload or {'query': '카페 카드 추천', 'client_request_id': str(uuid.uuid4())}, headers=self.headers)

    def test_cookie_ownership_origin_validation_and_lazy_openapi(self):
        self.app.openapi()
        self.assertFalse(self.store.path.exists())
        self.assertEqual(self.client.get('/v1/conversations').status_code, 401)
        rejected = self.client.post('/v1/browser-session', json={}, headers={'Origin': 'http://evil.example'})
        self.assertEqual(rejected.status_code, 403)
        response = self.session()
        self.assertEqual(response.json(), {'status': 'ready'})
        cookie = response.headers['set-cookie']
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=lax', cookie)
        token = self.client.cookies.get('pickcardu_browser')
        self.session()
        self.assertEqual(self.client.cookies.get('pickcardu_browser'), token)
        cid = self.conversation().json()['id']
        other = TestClient(self.app)
        self.addCleanup(other.close)
        self.session(other)
        self.assertEqual(other.get(f'/v1/conversations/{cid}/messages').status_code, 404)
        bad = self.send(cid, {'query': '  ', 'client_request_id': str(uuid.uuid4())})
        self.assertEqual(bad.status_code, 422)
        self.assertEqual(bad.headers['cache-control'], 'no-store')
        self.assertEqual(self.provider.embedding_queries, [])
        self.client.cookies.clear()
        self.session()
        self.assertEqual(self.client.get(f'/v1/conversations/{cid}/messages').status_code, 404)

    def test_create_replay_messages_restore_and_no_provider_replay(self):
        self.session()
        draft = str(uuid.uuid4())
        first, repeated = self.conversation(draft=draft), self.conversation(draft=draft)
        self.assertEqual((first.status_code, repeated.status_code), (201, 200))
        cid = first.json()['id']
        self.assertEqual(cid, repeated.json()['id'])
        self.assertEqual(self.client.get('/v1/conversations').json()['conversations'], [])
        payload = {'query': '카페 카드 추천', 'client_request_id': str(uuid.uuid4())}
        first, replay = self.send(cid, payload), self.send(cid, payload)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), replay.json())
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(len(self.provider.answer_inputs), 1)
        page = self.client.get(f'/v1/conversations/{cid}/messages').json()
        self.assertEqual([m['role'] for m in page['messages']], ['user', 'assistant'])
        self.assertEqual([m['seq'] for m in page['messages']], [1, 2])
        self.assertEqual(page['messages'][0]['client_request_id'], payload['client_request_id'])
        self.assertFalse(page['messages'][1]['rewrite_usage']['provider_called'])
        self.assertEqual(self.client.get('/v1/conversations').json()['conversations'][0]['title'], payload['query'])
        conflict = self.send(cid, {**payload, 'top_k': 5})
        self.assertEqual(conflict.status_code, 409)

    def test_followup_rewrites_once_uses_fresh_evidence_and_isolates_other_chat(self):
        self.session()
        cid = self.conversation().json()['id']
        self.assertEqual(self.send(cid).status_code, 200)
        self.assertEqual(len(self.provider.rewrites), 0)
        second = self.send(cid, {'query': '첫 번째 카드 연회비는?', 'client_request_id': str(uuid.uuid4())})
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(len(self.provider.rewrites), 1)
        self.assertEqual(self.provider.embedding_queries[-1], 'Card A 연회비는?')
        self.assertEqual(self.provider.answer_inputs[-1][0], 'Card A 연회비는?')
        self.assertIn('1. Card A', self.provider.rewrites[0][1]['content'])
        self.assertTrue(second.json()['messages'][1]['rewrite_usage']['provider_called'])
        other = self.conversation().json()['id']
        self.send(other)
        self.assertEqual(len(self.provider.rewrites), 1)

    def test_invalid_rewrite_saves_failure_without_embedding_or_fallback(self):
        self.session()
        cid = self.conversation().json()['id']
        self.send(cid)
        for invalid in ('   ', 'x'*501):
            self.provider.rewrite_result = invalid
            result = self.send(cid)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.json()['code'], 'LLM_UNAVAILABLE')
        self.assertEqual(len(self.provider.embedding_queries), 1)
        messages = self.client.get(f'/v1/conversations/{cid}/messages').json()['messages']
        self.assertEqual(messages[-1]['status'], 'failed')
        self.assertTrue(messages[-1]['rewrite_usage']['provider_called'])

    def test_failure_replay_and_explicit_retry_preserve_question(self):
        self.session()
        cid = self.conversation().json()['id']
        payload = {'query': '카페', 'client_request_id': str(uuid.uuid4())}
        self.provider.fail_answer = True
        failure = self.send(cid, payload)
        self.assertEqual(failure.status_code, 503)
        self.assertNotIn('secret', failure.text)
        replay = self.send(cid, payload)
        self.assertEqual(replay.status_code, 200)
        self.assertEqual(replay.json()['messages'][1]['status'], 'failed')
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.provider.fail_answer = False
        retry = self.send(cid, {**payload, 'retry_failed': True})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()['turn_id'], replay.json()['turn_id'])
        self.assertEqual(len(self.client.get(f'/v1/conversations/{cid}/messages').json()['messages']), 2)

    def test_provider_success_storage_failure_remains_pending_without_rerun(self):
        self.session()
        cid = self.conversation().json()['id']
        payload = {'query': '카페', 'client_request_id': str(uuid.uuid4())}
        error = ChatStoreError(503, 'CHAT_STORAGE_UNAVAILABLE', '저장 실패', True)
        with patch.object(self.store, 'complete_turn', side_effect=error):
            self.assertEqual(self.send(cid, payload).status_code, 503)
        replay = self.send(cid, payload)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertTrue(self.client.get(f'/v1/conversations/{cid}/messages').json()['has_pending'])

    def test_insufficient_response_is_saved_restored_and_excluded_from_rewrite(self):
        self.session()
        cid = self.conversation().json()['id']
        insufficient = AnswerOutput(answer_status='insufficient_evidence', answer_text='근거가 부족합니다.')
        with patch.object(self.provider, 'answer', return_value=(insufficient, {'model': 'fixture'})):
            self.assertEqual(self.send(cid).status_code, 200)
        page = self.client.get(f'/v1/conversations/{cid}/messages').json()
        assistant = page['messages'][1]
        self.assertEqual(assistant['status'], 'completed')
        self.assertEqual(assistant['answer']['answer_status'], 'insufficient_evidence')
        self.assertEqual(assistant['content'], '근거가 부족합니다.')
        self.assertEqual(assistant['answer']['recommendations'], [])
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(self.send(cid).status_code, 200)
        self.assertEqual(self.provider.rewrites, [])
