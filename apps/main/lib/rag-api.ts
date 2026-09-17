import type { components } from '../../../packages/contracts/generated/api';

export type AnswerResponse = components['schemas']['AnswerResponse'];
type ErrorResponse = components['schemas']['ErrorResponse'];

type RequestAnswerOptions = {
  baseUrl?: string;
  fetchImpl?: typeof fetch;
};

const DEFAULT_API_BASE_URL = 'http://127.0.0.1:8000';

export class RagApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId: string;

  constructor(error: ErrorResponse) {
    super(error.message);
    this.name = 'RagApiError';
    this.code = error.code;
    this.retryable = error.retryable;
    this.requestId = error.request_id;
  }
}

export async function requestAnswer(
  query: string,
  options: RequestAnswerOptions = {},
): Promise<AnswerResponse> {
  const trimmedQuery = query.trim();
  if (!trimmedQuery) {
    throw new RagApiError({
      code: 'EMPTY_QUERY',
      message: '질문을 입력해 주세요.',
      retryable: false,
      request_id: '',
    });
  }
  const baseUrl = options.baseUrl
    ?? process.env.NEXT_PUBLIC_RAG_API_BASE_URL
    ?? DEFAULT_API_BASE_URL;
  let response: Response;
  try {
    response = await (options.fetchImpl ?? fetch)(
      `${baseUrl.replace(/\/$/, '')}/v1/answer`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: trimmedQuery, top_k: 3 }),
      },
    );
  } catch {
    throw new RagApiError({
      code: 'API_UNREACHABLE',
      message: '로컬 RAG API에 연결할 수 없습니다. FastAPI 실행 상태를 확인해 주세요.',
      retryable: true,
      request_id: '',
    });
  }
  let body: AnswerResponse | ErrorResponse;
  try {
    body = await response.json() as AnswerResponse | ErrorResponse;
  } catch {
    throw new RagApiError({
      code: 'INVALID_RESPONSE',
      message: '서버 응답을 해석할 수 없습니다. 잠시 후 다시 시도해 주세요.',
      retryable: true,
      request_id: '',
    });
  }
  if (!response.ok) {
    throw new RagApiError(body as ErrorResponse);
  }
  return body as AnswerResponse;
}
