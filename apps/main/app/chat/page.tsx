'use client';

import { useEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../../lib/chat-api';
import { useChat } from '../../lib/use-chat';
import { SiteHeader } from '../components/site-header';
import { ChatComposer } from './components/chat-composer';
import { ConversationList } from './components/conversation-list';
import { MessageList } from './components/message-list';

const suggestions = [
  { label: '보유 카드', question: '내가 보유한 카드 혜택 설명해줘.' },
  { label: '주류', question: '주류비 혜택이 좋은 카드 추천해줘.' },
  { label: '생활비', question: '배달과 온라인 쇼핑 혜택을 같이 받을 수 있는 카드 추천해줘.' },
  { label: '여행', question: '해외여행과 공항 라운지 혜택이 좋은 카드 추천해줘.' },
  { label: '연회비', question: '연회비 대비 혜택이 가장 좋은 카드 추천해줘.' },
  { label: '비교', question: '내 소비 패턴에 맞춰 보유 카드와 새 카드를 비교해줘.' },
];

export default function ChatPage() {
  const chat = useChat();
  const [question, setQuestion] = useState('');
  const [historyOpen, setHistoryOpen] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const historyClose = useRef<HTMLButtonElement>(null);
  const historyPanel = useRef<HTMLDivElement>(null);
  const viewGeneration = useRef(0);
  const lastSelectedId = useRef(chat.selectedId);
  const activeConversation = Boolean(chat.selectedId || chat.messages.length || chat.loading);

  useEffect(() => {
    if (lastSelectedId.current !== chat.selectedId) {
      lastSelectedId.current = chat.selectedId;
      viewGeneration.current += 1;
    }
  }, [chat.selectedId]);

  useEffect(() => {
    if (!historyOpen) return;
    historyClose.current?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setHistoryOpen(false);
        historyButton.current?.focus();
      } else if (event.key === 'Tab') {
        const focusable = Array.from(historyPanel.current?.querySelectorAll<HTMLElement>('button:not(:disabled)') ?? []);
        if (!focusable.length) return;
        const first = focusable[0], last = focusable.at(-1)!;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [historyOpen]);

  async function send(retry?: { question: string; message: ChatMessage }) {
    const value = retry?.question ?? question;
    const currentView = viewGeneration.current;
    if (!retry) setQuestion('');
    const sent = await chat.sendQuestion(value, retry?.message);
    if (!sent && !retry && viewGeneration.current === currentView) setQuestion(value);
  }

  function newChat() {
    viewGeneration.current += 1;
    chat.startNewChat();
    setHistoryOpen(false);
    setQuestion('');
    requestAnimationFrame(() => composer.current?.focus());
  }

  function selectConversation(id: string) {
    viewGeneration.current += 1;
    void chat.selectConversation(id);
    setHistoryOpen(false);
    requestAnimationFrame(() => historyButton.current?.focus());
  }

  function closeHistory() {
    setHistoryOpen(false);
    requestAnimationFrame(() => historyButton.current?.focus());
  }

  const history = (
    <ConversationList
      conversations={chat.conversations}
      hasMore={Boolean(chat.conversationCursor)}
      onLoadMore={() => void chat.loadMoreConversations()}
      onNewChat={newChat}
      onSelect={selectConversation}
      selectedId={chat.selectedId}
    />
  );

  return (
    <main className="page-shell chat-page">
      <SiteHeader active="chat" />
      <div className="chat-layout">
        <aside className="chat-history" aria-label="채팅 내역">{history}</aside>
        <section className={`chat-hero${activeConversation ? ' chat-thread' : ''}`} aria-labelledby="chat-title">
          <div className="mobile-chat-tools">
            <button
              aria-controls="mobile-chat-history"
              aria-expanded={historyOpen}
              onClick={() => setHistoryOpen(true)}
              ref={historyButton}
              type="button"
            >채팅 내역</button>
            <button onClick={newChat} type="button">새 채팅</button>
          </div>

          {activeConversation ? (
            <>
              <h1 className="sr-only" id="chat-title">PickCardU와 대화</h1>
              <MessageList
                beforeSeq={chat.beforeSeq}
                loading={chat.loading}
                messages={chat.messages}
                onLoadOlder={chat.loadOlderMessages}
                onRefresh={chat.refreshMessages}
                onRetry={(value, message) => void send({ question: value, message })}
              />
              {chat.error ? <p className="chat-status-error" role="alert">{chat.error}</p> : null}
              <ChatComposer
                disabled={!chat.ready || chat.loading || chat.busy}
                inputRef={composer}
                onChange={setQuestion}
                onSubmit={() => void send()}
                value={question}
              />
            </>
          ) : (
            <>
              <div className="assistant-orb" aria-hidden="true"><span /></div>
              <h1 id="chat-title">What matters most<br /><span>when you use a card?</span></h1>
              <p className="chat-description">
                소비 습관이나 원하는 혜택을 편하게 알려주세요. 근거가 분명한 카드만 골라드릴게요.
              </p>
              <ChatComposer
                disabled={!chat.ready || chat.loading || chat.busy}
                inputRef={composer}
                onChange={setQuestion}
                onSubmit={() => void send()}
                value={question}
              />
              {chat.error ? <p className="submit-preview" role="alert"><span>연결 오류</span>{chat.error}</p> : null}
              <div className="suggestion-section" aria-label="추천 질문">
                <div className="suggestion-grid">
                  {suggestions.map((suggestion) => (
                    <button key={suggestion.label} onClick={() => setQuestion(suggestion.question)} type="button">
                      <span>{suggestion.label}</span>{suggestion.question}<b aria-hidden="true">↗</b>
                    </button>
                  ))}
                </div>
              </div>
            </>
          )}
        </section>
      </div>

      {historyOpen ? (
        <div className="mobile-history-layer">
          <button aria-label="채팅 내역 닫기" className="history-backdrop" onClick={closeHistory} type="button" />
          <div aria-label="채팅 내역" aria-modal="true" className="mobile-history-panel" id="mobile-chat-history" ref={historyPanel} role="dialog">
            <button className="mobile-history-close" onClick={closeHistory} ref={historyClose} type="button">닫기</button>
            {history}
          </div>
        </div>
      ) : null}
    </main>
  );
}
