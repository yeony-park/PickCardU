const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const BODY_LIMIT = 8192;

function error(status: number, code: string, message: string, retryable = false) {
  return Response.json({ code, message, retryable, request_id: '' }, { status, headers: { 'Cache-Control': 'no-store' } });
}

async function boundedBody(request: Request): Promise<string | null> {
  if (Number(request.headers.get('content-length')) > BODY_LIMIT) return null;
  const reader = request.body?.getReader();
  if (!reader) return '';
  const chunks: Uint8Array[] = [];
  let size = 0;
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    size += value.byteLength;
    if (size > BODY_LIMIT) { await reader.cancel(); return null; }
    chunks.push(value);
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
}

export async function proxyChatRequest(
  request: Request, path: string[], options: { baseUrl?: string; fetchImpl?: typeof fetch } = {},
): Promise<Response> {
  const session = path.length === 1 && path[0] === 'browser-session';
  const conversations = path.length === 1 && path[0] === 'conversations';
  const messages = path.length === 3 && path[0] === 'conversations' && UUID.test(path[1]) && path[2] === 'messages';
  if (!session && !conversations && !messages) return error(404, 'NOT_FOUND', '허용되지 않은 API 경로입니다.');
  if (!['GET', 'POST'].includes(request.method) || (session && request.method !== 'POST')) {
    return error(405, 'METHOD_NOT_ALLOWED', '허용되지 않은 요청 메서드입니다.');
  }
  const incoming = new URL(request.url);
  // Next may normalize Request.url to localhost despite an actual 127.0.0.1 Host.
  // Use the HTTP Host, not untrusted X-Forwarded-Host, and still require exact Origin.
  const incomingOrigin = `${incoming.protocol}//${request.headers.get('host') ?? incoming.host}`;
  if (request.method === 'POST' && request.headers.get('origin') !== incomingOrigin) {
    return error(403, 'ORIGIN_NOT_ALLOWED', '허용되지 않은 요청 출처입니다.');
  }
  let body: string | undefined;
  if (request.method === 'POST') {
    try {
      const content = await boundedBody(request);
      if (content === null) return error(413, 'REQUEST_TOO_LARGE', '요청은 8KiB 이하여야 합니다.');
      body = content;
    } catch {
      return error(422, 'INVALID_REQUEST', '요청 본문이 올바르지 않습니다.');
    }
  }
  const headers = new Headers({ 'Content-Type': 'application/json' });
  const cookie = request.headers.get('cookie')?.split(';').map(value => value.trim())
    .find(value => /^pickcardu_browser=[A-Za-z0-9_-]{43}$/.test(value));
  if (cookie) headers.set('Cookie', cookie);
  try {
    const base = new URL(options.baseUrl ?? process.env.PICKCARDU_RAG_API_BASE_URL ?? 'http://127.0.0.1:8000');
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password) throw Error('invalid API configuration');
    const upstreamUrl = new URL(`/v1/${path.join('/')}`, base.origin);
    const allowedQuery = messages ? ['limit', 'before_seq'] : conversations ? ['limit', 'cursor'] : [];
    for (const [key, value] of incoming.searchParams) {
      if (!allowedQuery.includes(key) || request.method !== 'GET') return error(422, 'INVALID_REQUEST', '허용되지 않은 조회 조건입니다.');
      upstreamUrl.searchParams.append(key, value);
    }
    if (request.method === 'POST') headers.set('Origin', base.origin);
    const upstream = await (options.fetchImpl ?? fetch)(upstreamUrl.toString(), {
      method: request.method, headers, body, cache: 'no-store', redirect: 'error', signal: AbortSignal.timeout(540_000),
    });
    const responseHeaders = new Headers({ 'Cache-Control': 'no-store', 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' });
    const issuedCookie = upstream.headers.get('set-cookie');
    if (issuedCookie?.startsWith('pickcardu_browser=')) responseHeaders.set('Set-Cookie', issuedCookie);
    const requestId = upstream.headers.get('x-request-id');
    if (requestId) responseHeaders.set('x-request-id', requestId);
    return new Response(await upstream.text(), { status: upstream.status, headers: responseHeaders });
  } catch (cause) {
    const timeout = cause instanceof Error && ['TimeoutError', 'AbortError'].includes(cause.name);
    return timeout
      ? error(504, 'API_TIMEOUT', '답변 처리 시간이 초과됐습니다. 저장 상태를 확인해 주세요.', true)
      : error(502, 'API_UNREACHABLE', '로컬 RAG API에 연결할 수 없습니다. FastAPI 실행 상태를 확인해 주세요.', true);
  }
}
