import type { ChatMessage } from './chat-api.ts';

export function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  return [...new Map([...current, ...incoming].map(message => [message.id, message])).values()].sort((a, b) => a.seq-b.seq);
}

export function isCurrentConversation(requestConversationId: string, selectedConversationId: string | null): boolean {
  return requestConversationId === selectedConversationId;
}

export function isDefiniteRejection(code: string): boolean {
  return ['CONVERSATION_BUSY', 'REQUEST_ID_CONFLICT', 'INVALID_REQUEST', 'ORIGIN_NOT_ALLOWED',
    'BROWSER_SESSION_REQUIRED', 'CONVERSATION_NOT_FOUND', 'REQUEST_TOO_LARGE'].includes(code);
}

export function restoreDraftAfterFailedSend(currentDraft: string, sentQuestion: string): string {
  return currentDraft || sentQuestion;
}

export function reconcileMessages(current: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const requests = new Set(incoming.map(message => message.client_request_id));
  // Completed server turns are immutable; a delayed poll may still report pending.
  const completed = new Map(current.filter(message => message.status === 'completed' && !message.id.startsWith('local-'))
    .map(message => [message.id, message]));
  return mergeMessages(current.filter(message => !requests.has(message.client_request_id)),
    incoming.map(message => completed.get(message.id) ?? message));
}
