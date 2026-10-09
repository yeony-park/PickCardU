import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import { createElement, type FormEvent, type KeyboardEvent, type ReactElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
type Element = ReactElement<Record<string, unknown>>;
type ChatState = ReturnType<typeof import('../lib/use-chat.ts').useChat>;
type ComposerProps = {
  disabled: boolean; sendDisabled: boolean; value: string; inputRef: { current: null };
  onChange: (value: string) => void; onSubmit: () => void;
};

function compile(path: string) {
  return ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
}
const composer = { exports: {} as { ChatComposer: (props: ComposerProps) => Element } };
new Function('require', 'module', 'exports', compile('../app/chat/components/chat-composer.tsx'))(require, composer, composer.exports);
let state: ChatState, captured: ComposerProps | undefined;
const page = { exports: {} as { default: () => ReactElement } };
const dependencies: Record<string, unknown> = {
  '../../lib/use-chat': { useChat: () => state },
  '../../lib/chat-state': require('./lib/chat-state.ts'),
  '../components/site-header': { SiteHeader: () => null },
  './components/conversation-list': { ConversationList: () => null },
  './components/message-list': { MessageList: () => null },
  './components/chat-composer': { ChatComposer: (props: ComposerProps) => {
    captured = props;
    return composer.exports.ChatComposer(props);
  } },
};
new Function('require', 'module', 'exports', compile('../app/chat/page.tsx'))(
  (name: string) => dependencies[name] ?? require(name), page, page.exports,
);

function capturedComposerProps(): ComposerProps {
  assert.ok(captured, 'Render the real page and capture its composer state');
  return captured;
}

function find(element: Element, type: string): Element {
  if (element.type === type) return element;
  for (const child of [element.props.children].flat() as Element[]) {
    if (child && typeof child === 'object') {
      try { return find(child, type); } catch { /* Try the next sibling. */ }
    }
  }
  throw new Error(`Missing ${type}`);
}

const cases: { name: string; state: Partial<ChatState>; blocked: boolean }[] = [
  { name: 'initial browser-session setup', state: { ready: false }, blocked: true },
  { name: 'initialization failure', state: { ready: false, error: '연결 오류' }, blocked: true },
  { name: 'saved conversation loading', state: { ready: false, selectedId: 'saved', loading: true }, blocked: true },
  { name: 'switching conversations', state: { selectedId: 'saved', loading: true }, blocked: true },
  { name: 'answer generation', state: { selectedId: 'saved', busy: true }, blocked: true },
  { name: 'ready conversation', state: {}, blocked: false },
];

for (const fixture of cases) test(`${fixture.name}: drafting is allowed and sending follows readiness`, () => {
  state = {
    conversations: [], conversationCursor: null, selectedId: null, messages: [], beforeSeq: null,
    ready: true, loading: false, error: '', deletingId: null, busy: false,
    surveyDraft: null, setSurveyDraft() {},
    sendQuestion: async () => false, startNewChat() {},
    selectConversation: async () => {}, refreshMessages: async () => {},
    loadOlderMessages: async () => {}, loadMoreConversations: async () => {},
    removeConversation: async () => false, ...fixture.state,
  };
  captured = undefined;
  renderToStaticMarkup(createElement(page.exports.default));
  const props = capturedComposerProps();
  let draft = '다음 질문', sends = 0, requested = 0;
  const form = composer.exports.ChatComposer({ ...props, value: draft,
    onChange: value => { draft = value; }, onSubmit: () => { sends++; } });
  const textarea = find(form, 'textarea');
  assert.equal(textarea.props.disabled, false, 'Drafting must remain available');
  assert.equal(find(form, 'button').props.disabled, fixture.blocked);
  (textarea.props.onChange as (event: { target: { value: string } }) => void)({ target: { value: '미리 작성한 질문' } });
  assert.equal(draft, '미리 작성한 질문');
  assert.equal(sends, 0, 'Readiness must not auto-send a draft');
  (textarea.props.onKeyDown as (event: KeyboardEvent<HTMLTextAreaElement>) => void)({
    key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false }, preventDefault() {},
    currentTarget: { form: { requestSubmit: () => { requested++; } } },
  } as unknown as KeyboardEvent<HTMLTextAreaElement>);
  assert.equal(requested, fixture.blocked ? 0 : 1);
  (form.props.onSubmit as (event: FormEvent<HTMLFormElement>) => void)({ preventDefault() {} } as FormEvent<HTMLFormElement>);
  assert.equal(sends, fixture.blocked ? 0 : 1);
});

test('team onboarding is confined to the empty view without replacing the chat composer', () => {
  state = {
    conversations: [], conversationCursor: null, selectedId: null, messages: [], beforeSeq: null,
    ready: false, loading: false, error: '', deletingId: null, busy: false,
    surveyDraft: null, setSurveyDraft() {},
    sendQuestion: async () => false, startNewChat() {},
    selectConversation: async () => {}, refreshMessages: async () => {},
    loadOlderMessages: async () => {}, loadMoreConversations: async () => {},
    removeConversation: async () => false,
  };
  captured = undefined;
  const empty = renderToStaticMarkup(createElement(page.exports.default));
  assert.match(empty, /assistant-logo brand-mark/);
  assert.match(empty, /id="survey-monthly-spending"/);
  const props = capturedComposerProps();
  assert.equal(props.disabled, false);
  assert.equal(props.sendDisabled, true);
  state = { ...state, ready: true, selectedId: '00000000-0000-4000-8000-000000000001' };
  const thread = renderToStaticMarkup(createElement(page.exports.default));
  assert.match(thread, /chat-thread/);
  assert.match(thread, /PickCardU와 대화/);
  assert.doesNotMatch(thread, /survey-monthly-spending/);
});

test('existing survey controls render the current draft rather than uncontrolled defaults', () => {
  state = {
    conversations: [], conversationCursor: null, selectedId: null, messages: [], beforeSeq: null,
    ready: true, loading: false, error: '', deletingId: null, busy: false,
    surveyDraft: { monthly_spending: '30-50', spending_categories: ['카페', '교통'], preferred_benefits: ['포인트 적립'] },
    setSurveyDraft() {}, sendQuestion: async () => false, startNewChat() {},
    selectConversation: async () => {}, refreshMessages: async () => {},
    loadOlderMessages: async () => {}, loadMoreConversations: async () => {}, removeConversation: async () => false,
  };
  const html = renderToStaticMarkup(createElement(page.exports.default));
  assert.match(html, /<option value="30-50" selected=""/);
  const inputs = [...html.matchAll(/<input[^>]*>/g)].map(match => match[0]);
  const checked = inputs.filter(input => input.includes('checked=""'));
  assert.equal(checked.length, 3);
  for (const value of ['카페', '교통', '포인트 적립']) assert.ok(checked.some(input => input.includes(`value="${value}"`)));
});
