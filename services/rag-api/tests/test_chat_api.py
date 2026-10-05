from __future__ import annotations

import inspect
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from pickcardu_rag.answering import AnswerOutput, AtomicClaim, Recommendation, RewriteOutput
from pickcardu_rag.errors import LlmUnavailable
from pickcardu_rag_api.chat_store import ChatStore, ChatStoreError
from pickcardu_rag_api.main import create_app
from support import FakeProvider, FakeReranker, build_release, settings


class ChatProvider(FakeProvider):
    def __init__(self):
        super().__init__()
        self.rewrites = []
        self.rewrite_result = RewriteOutput(standalone_query='Card A 연회비는?')
        self.fail_answer = False
        self.recommend_all = False
        self.insufficient = False
        self.references = []

    def rewrite(self, context, *, references=None):
        self.rewrites.append(context)
        self.references.append(references)
        return self.rewrite_result, {'model': self.llm_model, 'usage': {'total_tokens': 3}}

    def answer(self, query, evidence, *, comparison=False):
        if self.fail_answer:
            raise LlmUnavailable('secret provider detail')
        if self.recommend_all or self.insufficient:
            self.answer_inputs.append((query, evidence))
            if self.insufficient:
                return AnswerOutput(answer_status='insufficient_evidence', answer_text='전월실적 조건은 확인하지 못했습니다.'), {'attempt_count': 1}
            return AnswerOutput(answer_text='두 카드를 안내합니다.',
                                recommendations=[Recommendation(card_key=e['card_key'], reason=e['text'], citations=[e['chunk_id']]) for e in evidence],
                                claims=[AtomicClaim(card_key=e['card_key'], text=e['text'], citations=[e['chunk_id']]) for e in evidence]), {'attempt_count': 1}
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

    def test_delete_requires_owner_and_origin_and_removes_only_one_chat(self):
        self.session()
        cid, kept = self.conversation().json()['id'], self.conversation().json()['id']
        self.send(cid)
        self.send(kept)
        path = f'/v1/conversations/{cid}'
        foreign = TestClient(self.app)
        self.addCleanup(foreign.close)
        self.session(foreign)
        self.assertEqual(foreign.delete(path, headers=self.headers).status_code, 404)
        self.assertEqual(self.client.delete(path, headers={'Origin': 'http://evil.example'}).status_code, 403)
        anonymous = TestClient(self.app)
        self.addCleanup(anonymous.close)
        self.assertEqual(anonymous.delete(path, headers=self.headers).status_code, 401)
        self.assertEqual(self.client.get(f'{path}/messages').status_code, 200)
        result = self.client.delete(path, headers=self.headers)
        self.assertEqual(result.status_code, 204, result.text)
        self.assertEqual(result.content, b'')
        self.assertEqual(result.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get(f'{path}/messages').status_code, 404)
        self.assertEqual([c['id'] for c in self.client.get('/v1/conversations').json()['conversations']], [kept])
        self.assertEqual(self.client.delete(path, headers=self.headers).status_code, 404)
        self.assertEqual(len(self.provider.answer_inputs), 2)

    def test_delete_pending_returns_conflict_without_provider_call(self):
        import hashlib
        self.session()
        cid = self.conversation().json()['id']
        owner = hashlib.sha256(self.client.cookies.get('pickcardu_browser').encode()).hexdigest()
        self.store.reserve_turn(owner, cid, str(uuid.uuid4()), {'query': '처리 중', 'top_k': 3, 'profile': None})
        result = self.client.delete(f'/v1/conversations/{cid}', headers=self.headers)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(result.json()['code'], 'CONVERSATION_BUSY')
        self.assertTrue(self.client.get(f'/v1/conversations/{cid}/messages').json()['has_pending'])
        self.assertEqual(self.provider.answer_inputs, [])

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

    def test_list_recall_uses_stored_order_without_index_or_answer_calls(self):
        from pickcardu_rag_api.index import ActiveIndexLoader
        self.session()
        cid = self.conversation().json()['id']
        self.provider.recommend_all = True
        self.assertEqual(self.send(cid).status_code, 200)
        self.provider.rewrite_result = {'standalone_query': '과거 목록 이름 확인', 'scope': 'previous',
                                        'operation': 'recall', 'selected_refs': ['t1r2', 't1r1']}
        payload = {'query': '위에 카드 두 개가 뭐지?', 'client_request_id': str(uuid.uuid4())}
        with patch.object(ActiveIndexLoader, 'load', side_effect=RuntimeError('index unavailable')):
            response = self.send(cid, payload)
            self.assertEqual(response.status_code, 200, response.text)
            recalled = response.json()['messages'][1]['answer']
            self.assertEqual(recalled['answer'], '이전에 안내한 카드 목록입니다.\n1. Card A · Issuer\n2. Card B · Issuer')
            self.assertEqual(recalled['answer_status'], 'answered')
            self.assertEqual(recalled['claims'], [])
            self.assertEqual(recalled['recommendations'], [])
            self.assertEqual(recalled['evidence'], [])
            self.assertEqual(recalled['usage']['answer']['reason'], 'conversation_card_recall')
            self.assertEqual(recalled['release_id'], 'release_fixture')
            self.assertEqual(recalled['profile'], 'card_page_section_benefit')
            self.assertEqual(self.send(cid, payload).json(), response.json())
            restored = self.client.get(f'/v1/conversations/{cid}/messages').json()['messages'][-1]['answer']
            self.assertEqual(restored, recalled)
            self.provider.rewrite_result['selected_refs'] = ['t1r2']
            single = self.send(cid).json()['messages'][1]['answer']
            self.assertEqual(single['answer'], '이전에 안내한 카드 목록입니다.\n2. Card B · Issuer')
            self.assertNotIn('할인', single['answer'])
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(len(self.provider.answer_inputs), 1)
        self.assertEqual(len(self.provider.rewrites), 2)
        self.provider.rewrite_result = {'standalone_query': 'Card B 연회비와 이름?', 'scope': 'previous',
                                        'operation': 'retrieve', 'selected_refs': ['t1r2']}
        result = self.send(cid, {'query': '두번째 카드 이름하고 연회비는?', 'client_request_id': str(uuid.uuid4())})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(len(self.provider.embedding_queries), 2)
        self.assertEqual(len(self.provider.answer_inputs), 2)

    def test_recall_cannot_bypass_scope_validation(self):
        self.session()
        cid = self.conversation().json()['id']
        self.send(cid)
        for scope, refs in (('global', []), ('previous', []), ('previous', ['t1r1', 't1r1'])):
            with self.subTest(scope=scope, refs=refs):
                self.provider.rewrite_result = {'standalone_query': '이름 확인', 'scope': scope,
                                                'operation': 'recall', 'selected_refs': refs}
                response = self.send(cid)
                self.assertEqual(response.status_code, 200, response.text)
                answer = response.json()['messages'][1]['answer']
                self.assertEqual(answer['answer_status'], 'insufficient_evidence')
                self.assertEqual(answer['usage']['answer']['reason'], 'clarification_required')
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(len(self.provider.answer_inputs), 1)

    def test_scoped_followup_keeps_both_targets_then_global_search_is_unrestricted(self):
        self.session()
        cid = self.conversation().json()['id']
        self.provider.recommend_all = True
        self.assertEqual(self.send(cid).status_code, 200)
        self.provider.rewrite_result = RewriteOutput(standalone_query='Card B 전월실적은?', scope='previous',
                                                    selected_refs=['t1r2'])
        result = self.send(cid, {'query': '두번째 것 조건은?', 'top_k': 1, 'client_request_id': str(uuid.uuid4())})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual({e['card_key'] for e in result.json()['messages'][1]['answer']['evidence']}, {'issuer/card-b'})
        self.assertEqual({e['card_key'] for e in self.provider.answer_inputs[-1][1]}, {'issuer/card-b'})
        self.provider.rewrite_result = RewriteOutput(standalone_query='주유 혜택 카드 추천')
        result = self.send(cid)
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual({e['card_key'] for e in self.provider.answer_inputs[-1][1]}, {'issuer/card-a', 'issuer/card-b'})
        self.assertIsNone(result.json()['messages'][1]['answer']['usage']['answer']['conversation_scope']['target_card_keys'])

    def test_clarification_is_saved_replayed_without_search_or_answer_calls(self):
        self.session()
        cid = self.conversation().json()['id']
        self.send(cid)
        self.provider.rewrite_result = RewriteOutput(standalone_query='그 카드?', scope='clarification',
                                                    clarification_question='어떤 카드를 말씀하시나요?')
        payload = {'query': '그거 어때?', 'client_request_id': str(uuid.uuid4())}
        response, replay = self.send(cid, payload), self.send(cid, payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), replay.json())
        answer = response.json()['messages'][1]['answer']
        self.assertEqual(answer['answer'], '어떤 카드를 말씀하시나요?')
        self.assertEqual(answer['answer_status'], 'insufficient_evidence')
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(len(self.provider.answer_inputs), 1)
        self.assertEqual(len(self.provider.rewrites), 1)

    def test_insufficient_followups_preserve_targets_and_diagnostics_for_more_than_two_turns(self):
        self.session()
        cid = self.conversation().json()['id']
        self.provider.recommend_all = True
        self.send(cid)
        self.provider.insufficient = True
        self.provider.rewrite_result = RewriteOutput(standalone_query='Card A와 Card B 전월실적?', scope='previous',
                                                    selected_refs=['t1r1', 't1r2'])
        for question in ('저 카드 중 실적은?', '저것들 모두 없어?', '두 개가 뭐라고?'):
            result = self.send(cid, {'query': question, 'top_k': 1, 'client_request_id': str(uuid.uuid4())})
            self.assertEqual(result.status_code, 200, result.text)
            answer = result.json()['messages'][1]['answer']
            self.assertEqual(answer['cards'], [])
            self.assertEqual(answer['evidence'], [])
            diagnostic = answer['usage']['answer']['retrieval']
            self.assertEqual(diagnostic['target_card_keys'], ['issuer/card-a', 'issuer/card-b'])
            self.assertEqual(diagnostic['evidence_counts'], {'issuer/card-a': 1, 'issuer/card-b': 1})
        self.assertEqual([r['card_name'] for r in self.provider.references[-1]], ['Card A', 'Card B'])

    def test_invalid_ref_asks_for_target_without_global_fallback(self):
        self.session()
        cid = self.conversation().json()['id']
        self.send(cid)
        self.provider.rewrite_result = RewriteOutput(standalone_query='없는 카드?', scope='previous', selected_refs=['invented'])
        response = self.send(cid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['messages'][1]['answer']['answer_status'], 'insufficient_evidence')
        self.assertEqual(len(self.provider.embedding_queries), 1)
        self.assertEqual(len(self.provider.answer_inputs), 1)

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

    def test_insufficient_response_is_saved_restored_and_not_treated_as_confirmed_facts(self):
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
        self.assertEqual(len(self.provider.rewrites), 1)
        self.assertIn('조건이 없다고 확인한 것이 아닙니다.', self.provider.rewrites[0][1]['content'])
        self.assertEqual(self.provider.references[0], [])

    def test_partial_comparison_evidence_abstains_without_answer_llm(self):
        from pickcardu_rag_api.index import ReleaseHandle
        self.session()
        cid = self.conversation().json()['id']
        self.provider.recommend_all = True
        self.send(cid)
        self.provider.rewrite_result = RewriteOutput(standalone_query='두 카드 비교', scope='previous',
                                                    selected_refs=['t1r1', 't1r2'])
        original = ReleaseHandle.search
        def drop_second(handle, *args):
            result = original(handle, *args)
            result['evidence'] = [e for e in result['evidence'] if e['card_key'] != 'issuer/card-b']
            return result
        with patch.object(ReleaseHandle, 'search', drop_second):
            response = self.send(cid)
        self.assertEqual(response.status_code, 200, response.text)
        answer = response.json()['messages'][1]['answer']
        self.assertEqual(answer['answer_status'], 'insufficient_evidence')
        self.assertIn('Card B', answer['answer'])
        self.assertEqual(answer['usage']['answer']['reason'], 'missing_target_evidence')
        self.assertEqual(answer['usage']['answer']['retrieval']['missing_card_keys'], ['issuer/card-b'])
        self.assertEqual(len(self.provider.answer_inputs), 1)
        self.assertEqual(len(self.provider.embedding_queries), 2)

    def test_legacy_recommendations_are_available_after_two_unscoped_insufficient_turns(self):
        self.session()
        cid = self.conversation().json()['id']
        self.provider.recommend_all = True
        self.send(cid)
        self.provider.insufficient = True
        self.provider.rewrite_result = RewriteOutput(standalone_query='전월실적 비교')
        self.send(cid)
        self.send(cid)
        self.provider.rewrite_result = RewriteOutput(standalone_query='Card A와 Card B 비교', scope='previous',
                                                    selected_refs=['t1r1', 't1r2'])
        response = self.send(cid)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([r['card_name'] for r in self.provider.references[-1]], ['Card A', 'Card B'])
        self.assertEqual(response.json()['messages'][1]['answer']['usage']['answer']['conversation_scope']['scope'], 'previous')
        self.assertEqual(len(self.provider.embedding_queries), 4)
