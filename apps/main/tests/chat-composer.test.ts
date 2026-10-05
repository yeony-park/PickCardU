import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import type { FormEvent, KeyboardEvent, ReactElement } from 'react';

const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../app/chat/components/chat-composer.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
type Element = ReactElement<Record<string, unknown>>;
type Props = { disabled: boolean; sendDisabled: boolean; inputRef: { current: null }; onChange: (value: string) => void; onSubmit: () => void; value: string };
const loaded = { exports: {} as { ChatComposer: (props: Props) => Element } };
new Function('require', 'module', 'exports', compiled)(require, loaded, loaded.exports);
const { ChatComposer } = loaded.exports;

function find(element: Element, type: string): Element {
  if (element.type === type) return element;
  for (const child of [element.props.children].flat() as Element[]) {
    if (child && typeof child === 'object') {
      try { return find(child, type); } catch { /* Search the remaining siblings. */ }
    }
  }
  throw new Error(`Missing ${type}`);
}

test('pending answer allows drafting but blocks Enter and form submission', () => {
  let draft = '다음 질문', sends = 0, requested = 0, prevented = 0;
  const form = ChatComposer({ disabled: false, sendDisabled: true, inputRef: { current: null },
    value: draft, onChange: value => { draft = value; }, onSubmit: () => { sends++; } });
  const textarea = find(form, 'textarea');
  assert.equal(textarea.props.disabled, false);
  assert.equal(find(form, 'button').props.disabled, true);
  (textarea.props.onChange as (event: { target: { value: string } }) => void)({ target: { value: '미리 작성한 질문' } });
  (textarea.props.onKeyDown as (event: KeyboardEvent<HTMLTextAreaElement>) => void)({
    key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false },
    preventDefault: () => { prevented++; }, currentTarget: { form: { requestSubmit: () => { requested++; } } },
  } as unknown as KeyboardEvent<HTMLTextAreaElement>);
  (form.props.onSubmit as (event: FormEvent<HTMLFormElement>) => void)({ preventDefault() {} } as FormEvent<HTMLFormElement>);
  assert.equal(draft, '미리 작성한 질문');
  assert.equal(prevented, 1);
  assert.equal(requested, 0);
  assert.equal(sends, 0);
});

test('answer completion retains draft and waits for manual Enter', () => {
  let sends = 0;
  const props = { disabled: false, sendDisabled: true, inputRef: { current: null },
    value: '미리 작성한 질문', onChange() {}, onSubmit: () => { sends++; } };
  ChatComposer(props);
  const form = ChatComposer({ ...props, sendDisabled: false });
  assert.equal(find(form, 'textarea').props.value, '미리 작성한 질문');
  assert.equal(find(form, 'button').props.disabled, false);
  assert.equal(sends, 0);
  (find(form, 'textarea').props.onKeyDown as (event: KeyboardEvent<HTMLTextAreaElement>) => void)({
    key: 'Enter', shiftKey: false, nativeEvent: { isComposing: false }, preventDefault() {},
    currentTarget: { form: { requestSubmit: () => (form.props.onSubmit as (event: FormEvent<HTMLFormElement>) => void)({ preventDefault() {} } as FormEvent<HTMLFormElement>) } },
  } as unknown as KeyboardEvent<HTMLTextAreaElement>);
  assert.equal(sends, 1);
});

test('initialization lock and empty input still block submission; Shift+Enter and IME do not submit', () => {
  let sends = 0, requested = 0, prevented = 0;
  const form = ChatComposer({ disabled: true, sendDisabled: false, inputRef: { current: null },
    value: '질문', onChange() {}, onSubmit: () => { sends++; } });
  assert.equal(find(form, 'textarea').props.disabled, true);
  assert.equal(find(form, 'button').props.disabled, true);
  (form.props.onSubmit as (event: FormEvent<HTMLFormElement>) => void)({ preventDefault() {} } as FormEvent<HTMLFormElement>);
  for (const [shiftKey, isComposing] of [[true, false], [false, true]]) {
    (find(form, 'textarea').props.onKeyDown as (event: KeyboardEvent<HTMLTextAreaElement>) => void)({
      key: 'Enter', shiftKey, nativeEvent: { isComposing }, preventDefault: () => { prevented++; },
      currentTarget: { form: { requestSubmit: () => { requested++; } } },
    } as unknown as KeyboardEvent<HTMLTextAreaElement>);
  }
  const empty = ChatComposer({ disabled: false, sendDisabled: false, inputRef: { current: null },
    value: '   ', onChange() {}, onSubmit: () => { sends++; } });
  assert.equal(find(empty, 'button').props.disabled, true);
  (empty.props.onSubmit as (event: FormEvent<HTMLFormElement>) => void)({ preventDefault() {} } as FormEvent<HTMLFormElement>);
  assert.equal(sends, 0);
  assert.equal(requested, 0);
  assert.equal(prevented, 0);
});
