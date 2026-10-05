import assert from 'node:assert/strict';
import test from 'node:test';

async function client() {
  const imported = await import('../lib/chat-api.ts').catch(() => null);
  assert.ok(imported, 'chat client is not implemented');
  return imported;
}

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
