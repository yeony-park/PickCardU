import assert from 'node:assert/strict';
import test from 'node:test';

async function proxy() {
  const imported = await import('../lib/chat-proxy.ts').catch(() => null);
  assert.ok(imported, 'chat proxy is not implemented');
  return imported.proxyChatRequest;
}

const cid = '00000000-0000-4000-8000-000000000001';

test('proxy rejects invalid origin, paths and methods before calling API', async () => {
  const run = await proxy();
  let calls = 0;
  const fetchImpl = async () => { calls++; return Response.json({}); };
  const post = new Request('http://localhost:3000/api/chat/conversations', { method: 'POST', headers: { Origin: 'http://evil.example' }, body: '{}' });
  assert.equal((await run(post, ['conversations'], { fetchImpl })).status, 403);
  for (const path of [['..'], ['https://evil.example'], ['conversations', '..', 'messages'], ['conversations', '%2f', 'messages'], ['browser-session', 'extra']]) {
    assert.equal((await run(new Request('http://localhost:3000/api/chat/x'), path, { fetchImpl })).status, 404);
  }
  assert.equal((await run(new Request('http://localhost:3000/api/chat/browser-session'), ['browser-session'], { fetchImpl })).status, 405);
  assert.equal(calls, 0);
});

test('proxy forwards only owner cookie and fixed URL with normalized origin', async () => {
  const run = await proxy();
  let url = '', options: RequestInit | undefined;
  const token = 'a'.repeat(43);
  const response = await run(new Request(`http://localhost:3000/api/chat/conversations/${cid}/messages`, {
    method: 'POST', headers: { Origin: 'http://localhost:3000', Cookie: `unrelated=secret; pickcardu_browser=${token}`, Authorization: 'secret' }, body: '{}',
  }), ['conversations', cid, 'messages'], { baseUrl: 'http://127.0.0.1:8100', fetchImpl: async (input, init) => {
    url = String(input); options = init;
    return Response.json({}, { headers: { 'Set-Cookie': `pickcardu_browser=${token}; HttpOnly; SameSite=Lax; Path=/`, 'x-request-id': 'r1' } });
  } });
  assert.equal(url, `http://127.0.0.1:8100/v1/conversations/${cid}/messages`);
  const headers = new Headers(options?.headers);
  assert.equal(headers.get('Cookie'), `pickcardu_browser=${token}`);
  assert.equal(headers.get('Authorization'), null);
  assert.equal(headers.get('Origin'), 'http://127.0.0.1:8100');
  assert.equal(options?.redirect, 'error');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal(response.headers.get('x-request-id'), 'r1');
  assert.match(response.headers.get('set-cookie') ?? '', /HttpOnly/);
});

test('Next internal localhost URL does not reject the actual 127.0.0.1 Host', async () => {
  const run = await proxy();
  let calls = 0;
  const request = new Request('http://localhost:3100/api/chat/browser-session', {
    method: 'POST', headers: { Host: '127.0.0.1:3100', Origin: 'http://127.0.0.1:3100' }, body: '{}',
  });
  const response = await run(request, ['browser-session'], { fetchImpl: async () => { calls++; return Response.json({ status: 'ready' }); } });
  assert.equal(response.status, 200);
  assert.equal(calls, 1);
  const forged = new Request('http://localhost:3100/api/chat/browser-session', {
    method: 'POST', headers: { Host: '127.0.0.1:3100', Origin: 'http://evil.example', 'x-forwarded-host': 'evil.example' }, body: '{}',
  });
  assert.equal((await run(forged, ['browser-session'], { fetchImpl: async () => { calls++; return Response.json({}); } })).status, 403);
  assert.equal(calls, 1);
});

test('proxy enforces body limit and safe 502/504 errors', async () => {
  const run = await proxy();
  const request = (body='{}') => new Request('http://localhost:3000/api/chat/conversations', { method: 'POST', headers: { Origin: 'http://localhost:3000' }, body });
  let calls = 0;
  assert.equal((await run(request('x'.repeat(8193)), ['conversations'], { fetchImpl: async () => { calls++; return Response.json({}); } })).status, 413);
  assert.equal(calls, 0);
  const unavailable = await run(request(), ['conversations'], { fetchImpl: async () => { throw Error('sensitive path'); } });
  assert.equal(unavailable.status, 502);
  assert.equal((await unavailable.json()).code, 'API_UNREACHABLE');
  const timeout = await run(request(), ['conversations'], { fetchImpl: async () => { throw new DOMException('timeout', 'TimeoutError'); } });
  assert.equal(timeout.status, 504);
  assert.equal((await timeout.json()).code, 'API_TIMEOUT');
});

test('GET forwards supported pagination without a request body', async () => {
  const run = await proxy();
  let received = '', init: RequestInit | undefined;
  await run(new Request(`http://localhost:3000/api/chat/conversations/${cid}/messages?limit=2&before_seq=4`), ['conversations', cid, 'messages'], {
    fetchImpl: async (input, options) => { received=String(input); init=options; return Response.json({}); },
  });
  assert.equal(new URL(received).search, '?limit=2&before_seq=4');
  assert.equal(init?.body, undefined);
});

test('DELETE forwards only a single UUID conversation with same-origin protection and empty 204', async () => {
  const run = await proxy();
  let calls = 0;
  const fetchImpl = async (input: string | URL | Request, options?: RequestInit) => {
    calls++;
    assert.equal(String(input), `http://127.0.0.1:8000/v1/conversations/${cid}`);
    assert.equal(options?.method, 'DELETE');
    assert.equal(options?.body, undefined);
    assert.equal(new Headers(options?.headers).get('Origin'), 'http://127.0.0.1:8000');
    return new Response(null, { status: 204 });
  };
  const request = (origin='http://localhost:3000') => new Request(`http://localhost:3000/api/chat/conversations/${cid}`, { method: 'DELETE', headers: { Origin: origin } });
  const response = await run(request(), ['conversations', cid], { fetchImpl });
  assert.equal(response.status, 204);
  assert.equal(await response.text(), '');
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.equal((await run(request('http://evil.example'), ['conversations', cid], { fetchImpl })).status, 403);
  for (const path of [['conversations'], ['browser-session'], ['conversations', cid, 'messages']]) {
    assert.equal((await run(request(), path, { fetchImpl })).status, 405);
  }
  assert.equal((await run(request(), ['conversations', '..'], { fetchImpl })).status, 404);
  assert.equal(calls, 1);
});
