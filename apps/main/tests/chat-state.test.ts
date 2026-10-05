import assert from 'node:assert/strict';
import test from 'node:test';
import type { components } from '../../../packages/contracts/generated/api.ts';

type Message = components['schemas']['ChatMessage'];
const message = (id: string, seq: number, content = ''): Message => ({id, seq, content, role:'assistant', status:'pending', turn_id:'t', client_request_id:'r', created_at:'now'});

test('messages merge by ID, update pending result and remain chronological', async () => {
  const imported = await import('../lib/chat-state.ts').catch(() => null);
  assert.ok(imported, 'chat state is not implemented');
  const messages = imported.mergeMessages([message('b', 2), message('a', 1)], [message('b', 2, '답변'), message('c', 3)]);
  assert.deepEqual(messages.map(m => m.id), ['a', 'b', 'c']);
  assert.equal(messages[1].content, '답변');
  assert.equal(imported.isCurrentConversation('old', 'new'), false);
  assert.equal(imported.isCurrentConversation('old', null), false);
  assert.equal(imported.isCurrentConversation('new', 'new'), true);
});

test('server turn replaces optimistic pair by request ID without duplicating question', async () => {
  const imported = await import('../lib/chat-state.ts');
  assert.equal(typeof imported.reconcileMessages, 'function', 'optimistic reconciliation is not implemented');
  const optimistic = [message('local-user', 1), message('local-assistant', 2)];
  const result = imported.reconcileMessages(optimistic, [message('server-user', 1), message('server-assistant', 2, '답변')]);
  assert.deepEqual(result.map(m => m.id), ['server-user', 'server-assistant']);
});

test('late pending poll cannot overwrite a completed server response', async () => {
  const { reconcileMessages } = await import('../lib/chat-state.ts');
  const completed = { ...message('server-assistant', 2, '완료된 답변'), status: 'completed' as const };
  const result = reconcileMessages([completed], [message('server-assistant', 2)]);
  assert.equal(result[0].status, 'completed');
  assert.equal(result[0].content, '완료된 답변');
});

test('explicit pre-reservation rejection is distinct from uncertain storage or network failure', async () => {
  const state = await import('../lib/chat-state.ts');
  assert.equal(typeof state.isDefiniteRejection, 'function');
  for (const code of ['CONVERSATION_BUSY', 'REQUEST_ID_CONFLICT', 'INVALID_REQUEST', 'ORIGIN_NOT_ALLOWED', 'BROWSER_SESSION_REQUIRED', 'CONVERSATION_NOT_FOUND', 'REQUEST_TOO_LARGE']) {
    assert.equal(state.isDefiniteRejection(code), true, code);
  }
  for (const code of ['TURN_IN_PROGRESS', 'RESULT_UNKNOWN', 'API_UNREACHABLE', 'API_TIMEOUT', 'CHAT_STORAGE_UNAVAILABLE', 'LLM_UNAVAILABLE']) {
    assert.equal(state.isDefiniteRejection(code), false, code);
  }
});

test('failed send restores its question only when no newer draft exists', async () => {
  const { restoreDraftAfterFailedSend } = await import('../lib/chat-state.ts');
  assert.equal(typeof restoreDraftAfterFailedSend, 'function');
  assert.equal(restoreDraftAfterFailedSend('미리 작성한 다음 질문', '실패한 질문'), '미리 작성한 다음 질문');
  assert.equal(restoreDraftAfterFailedSend('', '실패한 질문'), '실패한 질문');
});
