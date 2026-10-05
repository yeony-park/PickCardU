'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ChatApiError, createConversation, deleteConversation, getMessages, initializeBrowser, listConversations, sendMessage,
  type ChatMessage, type Conversation,
} from './chat-api';
import { isCurrentConversation, isDefiniteRejection, mergeMessages, reconcileMessages } from './chat-state';

const DRAFT_KEY = 'pickcardu-chat-draft';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function draftId(): string {
  const existing = sessionStorage.getItem(DRAFT_KEY);
  const id = existing && UUID.test(existing) ? existing : crypto.randomUUID();
  sessionStorage.setItem(DRAFT_KEY, id);
  return id;
}

function updateUrl(id: string | null) {
  const url = new URL(window.location.href);
  if (id) url.searchParams.set('conversation', id);
  else url.searchParams.delete('conversation');
  window.history.pushState(null, '', url);
}

function errorText(error: unknown): string {
  return error instanceof ChatApiError ? error.message : '대화를 불러오지 못했습니다. 다시 확인해 주세요.';
}

export function useChat() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationCursor, setConversationCursor] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [beforeSeq, setBeforeSeq] = useState<number | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(false);
  const [sendingGeneration, setSendingGeneration] = useState<number | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const selected = useRef<string | null>(null);
  const generation = useRef(0);
  const activeSends = useRef(new Set<number>());
  const deleting = useRef<string | null>(null);
  const deletedIds = useRef(new Set<string>());
  const mounted = useRef(false);

  const refreshConversations = useCallback(async (cursor?: string) => {
    const page = await listConversations(cursor);
    if (!mounted.current) return;
    const incoming = page.conversations.filter(item => !deletedIds.current.has(item.id));
    setConversations(current => cursor
      ? [...new Map([...current, ...incoming].map(item => [item.id, item])).values()]
      : incoming);
    setConversationCursor(page.next_cursor);
  }, []);

  const selectConversation = useCallback(async (id: string | null, changeUrl = true) => {
    const requestGeneration = ++generation.current;
    selected.current = id;
    setSelectedId(id);
    setMessages([]);
    setBeforeSeq(null);
    setSendingGeneration(null);
    setError('');
    setLoading(Boolean(id));
    if (changeUrl) updateUrl(id);
    if (!id) return;
    try {
      const page = await getMessages(id);
      if (mounted.current && generation.current === requestGeneration) {
        setMessages(page.messages);
        setBeforeSeq(page.next_before_seq);
      }
    } catch (failure) {
      if (mounted.current && generation.current === requestGeneration) setError(errorText(failure));
    } finally {
      if (mounted.current && generation.current === requestGeneration) setLoading(false);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    let cancelled = false;
    async function initialize() {
      try {
        await initializeBrowser();
        if (cancelled) return;
        await refreshConversations();
        if (!cancelled) {
          const id = new URL(window.location.href).searchParams.get('conversation');
          if (id) await selectConversation(id, false);
          if (!cancelled) setReady(true);
        }
      } catch (failure) {
        if (!cancelled) setError(errorText(failure));
      }
    }
    void initialize();
    const onBack = () => void selectConversation(new URL(window.location.href).searchParams.get('conversation'), false);
    window.addEventListener('popstate', onBack);
    return () => { cancelled = true; mounted.current = false; window.removeEventListener('popstate', onBack); };
  }, [refreshConversations, selectConversation]);

  const hasPending = messages.some(message => message.role === 'assistant' && message.status === 'pending');
  const resultUnknown = messages.some(message => message.error?.code === 'RESULT_UNKNOWN');

  const refreshMessages = useCallback(async () => {
    const id = selected.current, requestGeneration = generation.current;
    if (!id) return;
    try {
      const page = await getMessages(id);
      if (mounted.current && generation.current === requestGeneration) {
        setMessages(current => reconcileMessages(current, page.messages));
        setError('');
      }
    } catch (failure) {
      if (mounted.current && generation.current === requestGeneration) setError(errorText(failure));
    }
  }, []);

  useEffect(() => {
    if (!selectedId || !hasPending) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const page = await getMessages(selectedId);
        if (cancelled) return;
        if (isCurrentConversation(selectedId, selected.current)) {
          setMessages(current => reconcileMessages(current, page.messages));
        }
        if (page.has_pending) timer = setTimeout(poll, 2000);
        else await refreshConversations();
      } catch (failure) {
        if (!cancelled) { setError(errorText(failure)); timer = setTimeout(poll, 2000); }
      }
    };
    timer = setTimeout(poll, 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [selectedId, hasPending, refreshConversations]);

  async function sendQuestion(question: string, retryMessage?: ChatMessage): Promise<boolean> {
    const value = question.trim();
    const requestGeneration = generation.current;
    if (!ready || loading || !value || value.length > 500 || hasPending || resultUnknown || activeSends.current.has(requestGeneration)
      || (deleting.current !== null && deleting.current === selected.current)) return false;
    activeSends.current.add(requestGeneration);
    setSendingGeneration(requestGeneration);
    setError('');
    let id = selected.current;
    const requestId = retryMessage?.client_request_id ?? crypto.randomUUID();
    try {
      if (!id) {
        const draft = draftId();
        const conversation = await createConversation(draft);
        id = conversation.id;
        if (sessionStorage.getItem(DRAFT_KEY) === draft) sessionStorage.removeItem(DRAFT_KEY);
        if (mounted.current && generation.current === requestGeneration) {
          selected.current = id;
          setSelectedId(id);
          updateUrl(id);
        }
      }
      const cid = id;
      const currentRequest = () => mounted.current && generation.current === requestGeneration && isCurrentConversation(cid, selected.current);
      if (currentRequest()) {
        if (retryMessage) {
          setMessages(current => current.map(message => message.id === retryMessage.id
            ? { ...message, status: 'pending', content: '', error: null } : message));
        } else {
          setMessages(current => {
            const seq = (current.at(-1)?.seq ?? 0) + 1;
            const common = { turn_id: `local-${requestId}`, client_request_id: requestId, created_at: new Date().toISOString() };
            return [...current, { ...common, id: `local-${requestId}:user`, seq, role: 'user', status: 'completed', content: value },
              { ...common, id: `local-${requestId}:assistant`, seq: seq+1, role: 'assistant', status: 'pending', content: '' }];
          });
        }
      }
      try {
        const result = await sendMessage(cid, { query: value, client_request_id: requestId, top_k: 5, retry_failed: Boolean(retryMessage) });
        if (currentRequest()) setMessages(current => reconcileMessages(current, result.messages));
      } catch (failure) {
        // Server failures may have saved the question. Prefer that authoritative state.
        const page = await getMessages(cid).catch(() => null);
        if (currentRequest()) {
          if (page?.messages.some(message => message.client_request_id === requestId)) {
            setMessages(current => reconcileMessages(current, page.messages));
          } else {
            const unknown = failure instanceof ChatApiError && isDefiniteRejection(failure.code)
              ? { code: failure.code, message: failure.message, retryable: false, request_id: failure.requestId }
              : { code: 'RESULT_UNKNOWN', message: '전송 결과가 불명확합니다. 내역을 확인해 주세요. 자동 재전송은 하지 않습니다.', retryable: false, request_id: '' };
            setMessages(current => current.map(message => message.client_request_id === requestId && message.role === 'assistant'
              ? { ...message, status: 'failed', content: unknown.message, error: unknown } : message));
          }
          setError(errorText(failure));
        }
      }
      await refreshConversations().catch(() => undefined);
      return true;
    } catch (failure) {
      if (mounted.current && generation.current === requestGeneration) setError(errorText(failure));
      return false;
    } finally {
      activeSends.current.delete(requestGeneration);
      if (mounted.current) setSendingGeneration(current => current === requestGeneration ? null : current);
    }
  }

  async function loadOlderMessages() {
    const id = selected.current, requestGeneration = generation.current;
    if (!id || !beforeSeq) return;
    try {
      const page = await getMessages(id, beforeSeq);
      if (mounted.current && generation.current === requestGeneration) {
        setMessages(current => mergeMessages(current, page.messages));
        setBeforeSeq(page.next_before_seq);
      }
    } catch (failure) {
      if (mounted.current && generation.current === requestGeneration) setError(errorText(failure));
    }
  }

  function startNewChat() {
    sessionStorage.removeItem(DRAFT_KEY);
    void selectConversation(null);
  }

  // True only when deletion reset the currently selected conversation.
  async function removeConversation(id: string): Promise<boolean> {
    if (!ready || deleting.current !== null || (selected.current === id
      && (hasPending || resultUnknown || activeSends.current.has(generation.current)))) return false;
    deleting.current = id;
    setDeletingId(id);
    try {
      try { await deleteConversation(id); }
      catch (failure) {
        if (!(failure instanceof ChatApiError) || failure.code !== 'CONVERSATION_NOT_FOUND') throw failure;
      }
      if (!mounted.current) return false;
      deletedIds.current.add(id);
      setConversations(current => current.filter(item => item.id !== id));
      if (selected.current === id) {
        startNewChat();
        return true;
      }
      return false;
    } catch (failure) {
      if (mounted.current) setError(errorText(failure));
      return false;
    } finally {
      deleting.current = null;
      if (mounted.current) setDeletingId(null);
    }
  }

  return {
    conversations, conversationCursor, selectedId, messages, beforeSeq, ready, loading, error, deletingId,
    busy: hasPending || resultUnknown || sendingGeneration !== null || (deletingId !== null && deletingId === selectedId),
    startNewChat, selectConversation, sendQuestion, refreshMessages, removeConversation,
    loadOlderMessages, loadMoreConversations: () => conversationCursor
      ? refreshConversations(conversationCursor).catch(failure => { if (mounted.current) setError(errorText(failure)); }) : Promise.resolve(),
  };
}
