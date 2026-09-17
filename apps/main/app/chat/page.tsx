'use client';

import { FormEvent, KeyboardEvent, useRef, useState } from 'react';
import { RagApiError, requestAnswer, type AnswerResponse } from '../../lib/rag-api';
import { SiteHeader } from '../components/site-header';

const suggestions = [
  { label: '보유 카드', question: '내가 보유한 카드 혜택 설명해줘.' },
  { label: '주류', question: '주류비 혜택이 좋은 카드 추천해줘.' },
  { label: '생활비', question: '배달과 온라인 쇼핑 혜택을 같이 받을 수 있는 카드 추천해줘.' },
  { label: '여행', question: '해외여행과 공항 라운지 혜택이 좋은 카드 추천해줘.' },
  { label: '연회비', question: '연회비 대비 혜택이 가장 좋은 카드 추천해줘.' },
  { label: '비교', question: '내 소비 패턴에 맞춰 보유 카드와 새 카드를 비교해줘.' },
];

const chatHistory = [
  { date: '오늘', title: '주류 혜택 카드 추천' },
  { date: '오늘', title: '보유 카드 혜택 정리' },
  { date: '어제', title: '해외여행 카드 비교' },
  { date: '8월 27일', title: '생활비 절약 카드' },
];

export default function ChatPage() {
  const [question, setQuestion] = useState('');
  const [submitted, setSubmitted] = useState('');
  const [answer, setAnswer] = useState<AnswerResponse | null>(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const activeRequestId = useRef(0);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = question.trim();
    if (!value || isLoading) return;
    const requestId = ++activeRequestId.current;
    setSubmitted(value);
    setQuestion('');
    setAnswer(null);
    setErrorMessage('');
    setIsLoading(true);
    try {
      const result = await requestAnswer(value);
      if (requestId === activeRequestId.current) {
        setAnswer(result);
      }
    } catch (error) {
      if (requestId === activeRequestId.current) {
        setErrorMessage(
          error instanceof RagApiError
            ? error.message
            : '답변을 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.',
        );
      }
    } finally {
      if (requestId === activeRequestId.current) {
        setIsLoading(false);
      }
    }
  }

  function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  function startNewChat() {
    activeRequestId.current += 1;
    setQuestion('');
    setSubmitted('');
    setAnswer(null);
    setErrorMessage('');
    setIsLoading(false);
  }

  return (
    <main className="page-shell chat-page">
      <SiteHeader active="chat" />
      <div className="chat-layout">
        <aside className="chat-history" aria-label="채팅 내역">
          <div className="history-heading">
            <strong>채팅 내역</strong>
            <button
              aria-label="새 채팅"
              onClick={startNewChat}
              type="button"
            >+</button>
          </div>
          <div className="history-list">
            {chatHistory.map((item) => (
              <button key={`${item.date}-${item.title}`} type="button">
                <span>{item.date}</span>
                <strong>{item.title}</strong>
              </button>
            ))}
          </div>
          <div className="history-card-note">
            <strong>내 카드</strong>
            <span>My Page에 저장한 카드를 추천에 함께 반영해요.</span>
          </div>
        </aside>
        <section className="chat-hero" aria-labelledby="chat-title">
          <div className="assistant-orb" aria-hidden="true"><span /></div>
          <h1 id="chat-title">
            What matters most<br /><span>when you use a card?</span>
          </h1>
          <p className="chat-description">
            소비 습관이나 원하는 혜택을 편하게 알려주세요. 근거가 분명한 카드만 골라드릴게요.
          </p>
          <form className="chat-composer" onSubmit={submit}>
            <label className="sr-only" htmlFor="card-question">PickCardU에 질문하기</label>
            <textarea
              id="card-question"
              onKeyDown={submitOnEnter}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder="예: 월 80만원 정도 쓰고, 배달과 온라인 쇼핑 혜택이 중요해요."
              rows={2}
              value={question}
            />
            <div className="composer-actions">
              <span className="saved-card-note">My Page에 저장된 카드도 함께 고려해요.</span>
              <button
                aria-label={isLoading ? '답변 생성 중' : '질문 보내기'}
                disabled={isLoading || !question.trim()}
                type="submit"
              >↑</button>
            </div>
          </form>
          {submitted ? (
            <p className="submit-preview"><span>내 질문</span>{submitted}</p>
          ) : null}
          {isLoading ? (
            <p aria-live="polite" className="submit-preview" role="status">
              <span>PickCardU</span>카드 혜택과 근거를 확인하고 있어요.
            </p>
          ) : null}
          {errorMessage ? (
            <p className="submit-preview" role="alert"><span>연결 오류</span>{errorMessage}</p>
          ) : null}
          {answer ? (
            <div aria-live="polite" className="submit-preview" role="status">
              <span>{answer.answer_status === 'answered' ? 'PickCardU 답변' : '근거 부족'}</span>
              {answer.answer}
              {answer.recommendations.map((recommendation) => {
                const card = answer.cards.find((item) => item.card_key === recommendation.card_key);
                return (
                  <div key={recommendation.card_key}>
                    <strong>{card?.card_name ?? recommendation.card_key}</strong>
                    {card ? ` · ${card.issuer}` : ''}: {recommendation.reason}
                  </div>
                );
              })}
            </div>
          ) : null}
          <div className="suggestion-section" aria-label="추천 질문">
            <div className="suggestion-grid">
              {suggestions.map((suggestion) => (
                <button key={suggestion.label} onClick={() => setQuestion(suggestion.question)} type="button">
                  <span>{suggestion.label}</span>{suggestion.question}<b aria-hidden="true">↗</b>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
