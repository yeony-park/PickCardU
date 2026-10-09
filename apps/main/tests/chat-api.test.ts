import assert from 'node:assert/strict';
import test from 'node:test';
import { cardProducts } from '../lib/card-products.ts';
import type { ChatMessage, SurveyContext, TurnInputSnapshot } from '../lib/chat-api.ts';

async function client() {
  const imported = await import('../lib/chat-api.ts').catch(() => null);
  assert.ok(imported, 'chat client is not implemented');
  return imported;
}

test('conversation creation sends survey once in the existing endpoint', async () => {
  const { createChatClient } = await client();
  const survey: SurveyContext = { monthly_spending: '30-50', spending_categories: ['카페'], preferred_benefits: ['할인'] };
  const bodies: unknown[] = [];
  const api = createChatClient({ fetchImpl: async (_input, options) => {
    bodies.push(JSON.parse(String(options?.body)));
    return Response.json({ id: 'c1', title: '새 채팅', created_at: 'now', updated_at: 'now' });
  } });
  await api.createConversation('00000000-0000-4000-8000-000000000001', survey);
  assert.deepEqual(bodies[0], { client_conversation_id: '00000000-0000-4000-8000-000000000001', survey_context: survey });
});

test('retry builder ignores current draft and wallet and retains original ID/profile/top_k', async () => {
  const imported = await client();
  assert.equal(typeof imported.buildTurnRequest, 'function', 'turn input snapshot builder is missing');
  const snapshot: TurnInputSnapshot = { query: '원래 질문', profile: 'parent_child_bundle', top_k: 3,
    wallet_context: { status: 'ready', card_keys: ['issuer/card-a'] } };
  const message: ChatMessage = { id: 'm', turn_id: 't', seq: 2, role: 'assistant', status: 'failed', content: '실패',
    created_at: 'now', client_request_id: '00000000-0000-4000-8000-000000000002', input_snapshot: snapshot };
  const retry = imported.buildTurnRequest('새 질문', '00000000-0000-4000-8000-000000000003', '["other"]', message);
  assert.deepEqual(retry, { ...snapshot, client_request_id: message.client_request_id, retry_failed: true });
});

test('actual request serialization with 106 wallets and long UTF-8 query fits proxy body limit', async () => {
  const imported = await client();
  assert.equal(typeof imported.buildTurnRequest, 'function');
  for (const query of ['가'.repeat(500), '😀'.repeat(250), '\u0001'.repeat(500)]) {
    const request = imported.buildTurnRequest(query, '00000000-0000-4000-8000-000000000004',
      JSON.stringify(cardProducts.map(card => card.name)));
    assert.ok(request.wallet_context?.card_keys, 'The serialized wallet must contain card IDs');
    assert.equal(request.wallet_context.card_keys.length, 106);
    assert.equal(request.top_k, 5);
    assert.ok(new TextEncoder().encode(JSON.stringify(request)).byteLength <= 8192);
  }
});

test('browser initialization uses one Web Lock and shared per-tab promise', async () => {
  const { createChatClient } = await client();
  let locks=0, calls=0;
  const api = createChatClient({ locks: { request: async (_name, action) => { locks++; return action(); } }, fetchImpl: async (_input, options) => {
    calls++;
    assert.equal(options?.credentials, 'same-origin');
    assert.equal(options?.cache, 'no-store');
    return Response.json({ status: 'ready' });
  } });
  await Promise.all([api.initializeBrowser(), api.initializeBrowser(), api.initializeBrowser()]);
  assert.equal(calls, 1);
  assert.equal(locks, 1);
});

test('missing Web Locks stops session issuance instead of racing a fallback', async () => {
  const { createChatClient, ChatApiError } = await client();
  let calls=0;
  const api = createChatClient({ locks: null, fetchImpl: async () => { calls++; return Response.json({}); } });
  await assert.rejects(api.initializeBrowser(), (error: unknown) => error instanceof ChatApiError && error.code === 'BROWSER_SESSION_UNSUPPORTED');
  assert.equal(calls, 0);
});

test('lost send response is reconciled by GET without resending or changing UUID', async () => {
  const { createChatClient } = await client();
  const calls: string[] = [];
  const api = createChatClient({ fetchImpl: async (input, options) => {
    calls.push(options?.method ?? 'GET');
    if (options?.method === 'POST') throw new TypeError('network lost');
    return Response.json({ messages: [{ id:'m1', turn_id:'t1', client_request_id:'r1', seq:1, role:'user', status:'completed', content:'카페', created_at:'now' },
      { id:'m2', turn_id:'t1', client_request_id:'r1', seq:2, role:'assistant', status:'pending', content:'', created_at:'now' }], next_before_seq:null, has_pending:true });
  } });
  const result = await api.sendMessage('c1', { query:'카페', client_request_id:'r1' });
  assert.equal(result.turn_id, 't1');
  assert.deepEqual(calls, ['POST', 'GET']);
});

test('unknown result never retries a paid send automatically', async () => {
  const { createChatClient, ChatApiError } = await client();
  let posts=0, gets=0;
  const api = createChatClient({ fetchImpl: async (_input, options) => {
    if (options?.method === 'POST') { posts++; return new Response('bad gateway', {status:502}); }
    gets++;
    return Response.json({ messages:[], next_before_seq:null, has_pending:false });
  } });
  await assert.rejects(api.sendMessage('c1', {query:'카페', client_request_id:'r1'}), (error: unknown) => error instanceof ChatApiError && error.code === 'RESULT_UNKNOWN');
  assert.equal(posts, 1);
  assert.equal(gets, 1);
});

test('delete targets one conversation and accepts empty 204 without retry', async () => {
  const { createChatClient, ChatApiError } = await client();
  const calls: string[] = [];
  const api = createChatClient({ fetchImpl: async (input, options) => {
    calls.push(String(input));
    assert.equal(options?.method, 'DELETE');
    assert.equal(options?.body, undefined);
    assert.equal(options?.credentials, 'same-origin');
    return new Response(null, { status: 204 });
  } });
  assert.equal(typeof api.deleteConversation, 'function');
  assert.equal(await api.deleteConversation('c1'), undefined);
  assert.deepEqual(calls, ['/api/chat/conversations/c1']);
  let attempts = 0;
  const unavailable = createChatClient({ fetchImpl: async () => { attempts++; throw Error('connection lost'); } });
  await assert.rejects(unavailable.deleteConversation('c1'), (error: unknown) => error instanceof ChatApiError && error.code === 'API_UNREACHABLE');
  assert.equal(attempts, 1);
});
