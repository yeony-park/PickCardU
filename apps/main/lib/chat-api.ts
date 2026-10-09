import type { components } from '../../../packages/contracts/generated/api';
import { buildWalletContext } from './registered-cards.ts';

export type Conversation = components['schemas']['Conversation'];
export type ChatMessage = components['schemas']['ChatMessage'];
export type ChatRequest = components['schemas']['ChatRequest'];
export type MessagesPage = components['schemas']['MessagesPage'];
export type SurveyContext = components['schemas']['SurveyContext'];
export type WalletContext = components['schemas']['WalletContext'];
export type TurnInputSnapshot = components['schemas']['TurnInputSnapshot'];
type ErrorBody = components['schemas']['ErrorResponse'];
type TurnResponse = components['schemas']['TurnResponse'];
type ConversationPage = components['schemas']['ConversationPage'];
type LockRunner = { request<T>(name: string, action: () => Promise<T>): Promise<T> };

export class ChatApiError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly requestId: string;
  constructor(body: ErrorBody) {
    super(body.message);
    this.name = 'ChatApiError';
    this.code = body.code;
    this.retryable = body.retryable;
    this.requestId = body.request_id;
  }
}

function failure(code: string, message: string, retryable = true) {
  return new ChatApiError({ code, message, retryable, request_id: '' });
}

export function buildTurnRequest(query: string, requestId: string, rawWallet: string | null | undefined,
  retryMessage?: ChatMessage): ChatRequest {
  if (retryMessage) {
    if (!retryMessage.input_snapshot) {
      throw failure('INVALID_RESPONSE', '원래 질문 정보를 불러온 뒤 다시 시도해 주세요.', false);
    }
    return { ...retryMessage.input_snapshot, client_request_id: retryMessage.client_request_id, retry_failed: true };
  }
  return { query: query.trim(), profile: null, top_k: 5, client_request_id: requestId,
    wallet_context: buildWalletContext(rawWallet), retry_failed: false };
}

export function createChatClient(options: { fetchImpl?: typeof fetch; locks?: LockRunner | null } = {}) {
  let initialization: Promise<void> | undefined;

  async function request<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
    let response: Response;
    try {
      response = await (options.fetchImpl ?? fetch)(`/api/chat/${path}`, {
        method, credentials: 'same-origin', cache: 'no-store',
        ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
      });
    } catch {
      throw failure('API_UNREACHABLE', 'API 연결이 끊겼습니다. 저장 상태를 확인해 주세요.');
    }
    if (response.status === 204) return undefined as T;
    let result: unknown;
    try { result = await response.json(); }
    catch { throw failure('INVALID_RESPONSE', '서버 응답을 확인할 수 없습니다. 저장 상태를 다시 확인해 주세요.'); }
    if (!response.ok) {
      const error = result as Partial<ErrorBody> | null;
      if (error && typeof error.code === 'string' && typeof error.message === 'string') {
        throw new ChatApiError({ code: error.code, message: error.message, retryable: error.retryable === true, request_id: error.request_id ?? '' });
      }
      throw failure(response.status === 504 ? 'API_TIMEOUT' : 'API_UNREACHABLE', 'API 응답을 확인할 수 없습니다.');
    }
    return result as T;
  }

  function initializeBrowser(): Promise<void> {
    if (initialization) return initialization;
    const locks = options.locks !== undefined ? options.locks : typeof navigator !== 'undefined' ? navigator.locks : null;
    if (!locks) return Promise.reject(failure('BROWSER_SESSION_UNSUPPORTED', '이 환경에서는 안전한 대화 저장을 초기화할 수 없습니다. Web Locks를 지원하는 브라우저와 localhost 또는 HTTPS 접속을 사용해 주세요.', false));
    initialization = locks.request('pickcardu-browser-session', async () => {
      await request('browser-session', {});
    }).catch(error => { initialization = undefined; throw error; });
    return initialization;
  }

  const getMessages = (id: string, beforeSeq?: number): Promise<MessagesPage> =>
    request(`conversations/${encodeURIComponent(id)}/messages${beforeSeq ? `?before_seq=${beforeSeq}` : ''}`);

  async function sendMessage(id: string, payload: ChatRequest): Promise<TurnResponse> {
    try { return await request(`conversations/${encodeURIComponent(id)}/messages`, payload); }
    catch (error) {
      if (!(error instanceof ChatApiError) || !['API_UNREACHABLE', 'API_TIMEOUT', 'INVALID_RESPONSE'].includes(error.code)) throw error;
      // An ambiguous POST may already be running/completed. Read before any retry.
      try {
        const page = await getMessages(id);
        const messages = page.messages.filter(message => message.client_request_id === payload.client_request_id);
        if (messages.length === 2) return { turn_id: messages[0].turn_id, messages };
      } catch { /* Keep the original request ID; never resend automatically. */ }
      throw failure('RESULT_UNKNOWN', '전송 결과를 확인하지 못했습니다. 내역을 다시 확인해 주세요. 자동 재전송은 하지 않습니다.', false);
    }
  }

  return {
    initializeBrowser, getMessages, sendMessage,
    listConversations: (cursor?: string): Promise<ConversationPage> => request(`conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`),
    createConversation: (id: string, surveyContext?: SurveyContext | null): Promise<Conversation> =>
      request('conversations', { client_conversation_id: id,
        ...(surveyContext === undefined ? {} : { survey_context: surveyContext }) }),
    deleteConversation: (id: string): Promise<void> => request(`conversations/${encodeURIComponent(id)}`, undefined, 'DELETE'),
  };
}

const client = createChatClient();
export const { initializeBrowser, getMessages, sendMessage, listConversations, createConversation, deleteConversation } = client;
