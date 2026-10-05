'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import type { ChatMessage } from '../../../lib/chat-api';

type MessageListProps = {
  beforeSeq: number | null;
  loading: boolean;
  messages: ChatMessage[];
  onLoadOlder: () => Promise<void>;
  onRefresh: () => Promise<void>;
  onRetry: (question: string, message: ChatMessage) => void;
};

export function MessageList({ beforeSeq, loading, messages, onLoadOlder, onRefresh, onRetry }: MessageListProps) {
  const viewport = useRef<HTMLDivElement>(null);
  const keepAnchor = useRef<{ height: number; top: number } | null>(null);
  const nearBottom = useRef(true);
  const [loadingOlder, setLoadingOlder] = useState(false);

  useLayoutEffect(() => {
    const element = viewport.current;
    if (!element) return;
    if (keepAnchor.current) {
      element.scrollTop = keepAnchor.current.top + element.scrollHeight - keepAnchor.current.height;
      keepAnchor.current = null;
    } else if (nearBottom.current) {
      element.scrollTop = element.scrollHeight;
    }
  }, [messages]);

  async function loadOlder() {
    const element = viewport.current;
    if (!element || loadingOlder) return;
    keepAnchor.current = { height: element.scrollHeight, top: element.scrollTop };
    setLoadingOlder(true);
    try { await onLoadOlder(); }
    finally { setLoadingOlder(false); }
  }

  return (
    <div
      className="message-viewport"
      onScroll={(event) => {
        const element = event.currentTarget;
        nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 120;
      }}
      ref={viewport}
    >
      <div aria-live="polite" className="message-list" role="log">
        {beforeSeq ? (
          <button className="load-older" disabled={loadingOlder} onClick={loadOlder} type="button">
            {loadingOlder ? '불러오는 중…' : '이전 대화 보기'}
          </button>
        ) : null}
        {loading && !messages.length ? <p className="message-loading" role="status">대화를 불러오고 있어요.</p> : null}
        {messages.map((message) => {
          const question = message.role === 'assistant'
            ? messages.find(candidate => candidate.turn_id === message.turn_id && candidate.role === 'user')?.content ?? ''
            : '';
          const resultUnknown = message.error?.code === 'RESULT_UNKNOWN';
          return (
            <article className={`chat-message ${message.role} ${message.status}`} key={message.id}>
              <span className="message-author">{message.role === 'user' ? '내 질문' : 'PickCardU'}</span>
              {message.status === 'pending' ? (
                <p className="message-pending" role="status">카드 혜택과 근거를 확인하고 있어요.</p>
              ) : <p>{message.content}</p>}
              {message.answer?.answer_status === 'insufficient_evidence' ? (
                <strong className="evidence-label">근거 부족</strong>
              ) : null}
              {message.answer?.recommendations.length ? (
                <div className="recommendation-list">
                  {message.answer.recommendations.map((recommendation) => {
                    const card = message.answer?.cards.find(item => item.card_key === recommendation.card_key);
                    return (
                      <div key={recommendation.card_key}>
                        <strong>{card?.card_name ?? recommendation.card_key}</strong>
                        {card ? <span>{card.issuer}</span> : null}
                        <p>{recommendation.reason}</p>
                      </div>
                    );
                  })}
                </div>
              ) : null}
              {message.role === 'assistant' && message.status === 'failed' ? (
                <div className="message-actions">
                  {resultUnknown ? (
                    <button onClick={() => void onRefresh()} type="button">내역 확인</button>
                  ) : message.error?.retryable && question ? (
                    <button onClick={() => onRetry(question, message)} type="button">다시 시도</button>
                  ) : null}
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </div>
  );
}
