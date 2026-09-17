import assert from 'node:assert/strict';
import test from 'node:test';

import { RagApiError, requestAnswer } from '../lib/rag-api.ts';

const completedAnswer = {
  status: 'completed',
  answer_status: 'answered',
  release_id: 'release_test',
  profile: 'card_page_section_benefit',
  query_type: 'semantic',
  cards: [
    {
      card_key: 'issuer/card-a',
      card_name: '카드 A',
      issuer: '카드사',
      score: 0.9,
      rank: 1,
      evidence_count: 1,
    },
  ],
  answer: '카드 A를 추천합니다.',
  recommendations: [
    {
      card_key: 'issuer/card-a',
      reason: '혜택 근거가 확인됐습니다.',
      citations: ['chunk-a'],
    },
  ],
  claims: [
    {
      card_key: 'issuer/card-a',
      text: '혜택이 제공됩니다.',
      value: null,
      unit: null,
      conditions: [],
      citations: ['chunk-a'],
    },
  ],
  evidence: [
    {
      rank: 1,
      card_key: 'issuer/card-a',
      card_name: '카드 A',
      issuer: '카드사',
      chunk_id: 'chunk-a',
      page_num: 1,
      text: '혜택 근거',
      section: '혜택',
      level: 'benefit',
      score: 0.9,
    },
  ],
  usage: { embedding: {}, answer: {} },
};

test('POST /v1/answer 계약으로 질문을 보내고 완료 응답을 반환한다', async () => {
  let receivedUrl = '';
  let receivedInit: RequestInit | undefined;
  const fetchImpl = async (input: string | URL | Request, init?: RequestInit) => {
    receivedUrl = String(input);
    receivedInit = init;
    return Response.json(completedAnswer);
  };

  const result = await requestAnswer('  공항 라운지 카드를 추천해줘  ', {
    baseUrl: 'http://127.0.0.1:8000/',
    fetchImpl,
  });

  assert.equal(receivedUrl, 'http://127.0.0.1:8000/v1/answer');
  assert.equal(receivedInit?.method, 'POST');
  assert.deepEqual(receivedInit?.headers, { 'Content-Type': 'application/json' });
  assert.deepEqual(JSON.parse(String(receivedInit?.body)), {
    query: '공항 라운지 카드를 추천해줘',
    top_k: 3,
  });
  assert.equal(result.answer, '카드 A를 추천합니다.');
});

test('FastAPI 오류 계약을 RagApiError로 변환한다', async () => {
  const fetchImpl = async () => Response.json(
    {
      code: 'LLM_UNAVAILABLE',
      message: '답변 생성 서비스를 사용할 수 없습니다.',
      retryable: true,
      request_id: 'request-123',
    },
    { status: 503 },
  );

  await assert.rejects(
    requestAnswer('카드를 추천해줘', { fetchImpl }),
    (error: unknown) => {
      assert.ok(error instanceof RagApiError);
      assert.equal(error.message, '답변 생성 서비스를 사용할 수 없습니다.');
      assert.equal(error.code, 'LLM_UNAVAILABLE');
      assert.equal(error.retryable, true);
      assert.equal(error.requestId, 'request-123');
      return true;
    },
  );
});

test('FastAPI에 연결할 수 없으면 재시도 가능한 네트워크 오류를 반환한다', async () => {
  const fetchImpl = async () => {
    throw new TypeError('fetch failed');
  };

  await assert.rejects(
    requestAnswer('카드를 추천해줘', { fetchImpl }),
    (error: unknown) => {
      assert.ok(error instanceof RagApiError);
      assert.equal(error.message, '로컬 RAG API에 연결할 수 없습니다. FastAPI 실행 상태를 확인해 주세요.');
      assert.equal(error.code, 'API_UNREACHABLE');
      assert.equal(error.retryable, true);
      return true;
    },
  );
});

test('공백뿐인 질문은 FastAPI로 보내지 않는다', async () => {
  let called = false;
  const fetchImpl = async () => {
    called = true;
    return Response.json(completedAnswer);
  };

  await assert.rejects(
    requestAnswer('   ', { fetchImpl }),
    (error: unknown) => {
      assert.ok(error instanceof RagApiError);
      assert.equal(error.message, '질문을 입력해 주세요.');
      assert.equal(error.code, 'EMPTY_QUERY');
      assert.equal(error.retryable, false);
      return true;
    },
  );
  assert.equal(called, false);
});

test('FastAPI가 JSON이 아닌 응답을 보내면 재시도 가능한 응답 오류를 반환한다', async () => {
  const fetchImpl = async () => new Response('<html>Bad Gateway</html>', {
    status: 502,
    headers: { 'Content-Type': 'text/html' },
  });

  await assert.rejects(
    requestAnswer('카드를 추천해줘', { fetchImpl }),
    (error: unknown) => {
      assert.ok(error instanceof RagApiError);
      assert.equal(error.message, '서버 응답을 해석할 수 없습니다. 잠시 후 다시 시도해 주세요.');
      assert.equal(error.code, 'INVALID_RESPONSE');
      assert.equal(error.retryable, true);
      return true;
    },
  );
});
