'use client';

import type { Conversation } from '../../../lib/chat-api';

type ConversationListProps = {
  conversations: Conversation[];
  hasMore: boolean;
  onLoadMore: () => void;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  selectedId: string | null;
};

function updatedLabel(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  if (date.toDateString() === today.toDateString()) return '오늘';
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) return '어제';
  return new Intl.DateTimeFormat('ko-KR', { month: 'short', day: 'numeric' }).format(date);
}

export function ConversationList({
  conversations, hasMore, onLoadMore, onNewChat, onSelect, selectedId,
}: ConversationListProps) {
  return (
    <>
      <div className="history-heading">
        <strong>채팅 내역</strong>
        <button aria-label="새 채팅" onClick={onNewChat} type="button">+</button>
      </div>
      <div className="history-list">
        {conversations.length ? conversations.map((conversation) => (
          <button
            aria-current={selectedId === conversation.id ? 'page' : undefined}
            className={selectedId === conversation.id ? 'active' : undefined}
            key={conversation.id}
            onClick={() => onSelect(conversation.id)}
            type="button"
          >
            <span>{updatedLabel(conversation.updated_at)}</span>
            <strong>{conversation.title}</strong>
          </button>
        )) : <p className="history-empty">아직 저장된 대화가 없어요.</p>}
        {hasMore ? (
          <button className="history-more" onClick={onLoadMore} type="button">이전 대화 더보기</button>
        ) : null}
      </div>
      <div className="history-card-note">
        <strong>내 카드</strong>
        <span>My Page에 저장한 카드를 추천에 함께 반영해요.</span>
      </div>
    </>
  );
}
