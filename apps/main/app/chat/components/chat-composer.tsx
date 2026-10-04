'use client';

import { FormEvent, KeyboardEvent, RefObject } from 'react';

type ChatComposerProps = {
  disabled: boolean;
  inputRef: RefObject<HTMLTextAreaElement | null>;
  onChange: (value: string) => void;
  onSubmit: () => void;
  value: string;
};

export function ChatComposer({ disabled, inputRef, onChange, onSubmit, value }: ChatComposerProps) {
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!disabled && value.trim()) onSubmit();
  }

  function submitOnEnter(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      event.currentTarget.form?.requestSubmit();
    }
  }

  return (
    <form className="chat-composer" onSubmit={submit}>
      <label className="sr-only" htmlFor="card-question">PickCardU에 질문하기</label>
      <textarea
        aria-describedby="question-limit"
        disabled={disabled}
        id="card-question"
        maxLength={500}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={submitOnEnter}
        placeholder="예: 월 80만원 정도 쓰고, 배달과 온라인 쇼핑 혜택이 중요해요."
        ref={inputRef}
        rows={2}
        value={value}
      />
      <div className="composer-actions">
        <span className="saved-card-note" id="question-limit">
          My Page에 저장된 카드도 함께 고려해요. {value.length}/500
        </span>
        <button
          aria-label={disabled ? '질문을 보낼 수 없음' : '질문 보내기'}
          disabled={disabled || !value.trim()}
          type="submit"
        >↑</button>
      </div>
    </form>
  );
}
