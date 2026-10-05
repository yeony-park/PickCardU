import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import type { ReactElement } from 'react';

const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
const compiled = ts.transpileModule(readFileSync(new URL('../app/chat/components/conversation-list.tsx', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX },
}).outputText;
type Element = ReactElement<Record<string, unknown>>;
type Props = {
  conversations: { id: string; title: string; created_at: string; updated_at: string }[];
  hasMore: boolean; onLoadMore: () => void; onNewChat: () => void; onSelect: (id: string) => void;
  onDelete: (id: string) => void; deletingId: string | null; blockedDeleteId: string | null; selectedId: string | null;
};
const loaded = { exports: {} as { ConversationList: (props: Props) => Element } };
new Function('require', 'module', 'exports', compiled)(require, loaded, loaded.exports);

function buttons(element: Element): Element[] {
  if (!element || typeof element !== 'object') return [];
  return [...(element.type === 'button' ? [element] : []),
    ...[element.props.children].flat(Infinity).flatMap(child => buttons(child as Element))];
}

const conversations = ['a', 'b'].map(id => ({ id, title: id === 'a' ? '카페 추천' : '주유 추천',
  created_at: '2026-10-05T00:00:00Z', updated_at: '2026-10-05T00:00:00Z' }));

test('delete button is separate from selection and targets only its conversation', () => {
  const deleted: string[] = [], selected: string[] = [];
  const tree = loaded.exports.ConversationList({ conversations, hasMore: false, onLoadMore() {}, onNewChat() {},
    onSelect: id => selected.push(id), onDelete: id => deleted.push(id), selectedId: 'a', deletingId: null, blockedDeleteId: null });
  const remove = buttons(tree).find(button => button.props['aria-label'] === '카페 추천 대화 삭제');
  assert.ok(remove);
  assert.equal(remove.props.type, 'button');
  assert.equal(remove.props.disabled, false);
  assert.equal(buttons(remove).length, 1, 'delete must not contain a nested button');
  (remove.props.onClick as () => void)();
  assert.deepEqual(deleted, ['a']);
  assert.deepEqual(selected, []);
});

test('pending conversation is blocked and a deletion in progress prevents duplicate requests', () => {
  const props: Props = { conversations, hasMore: false, onLoadMore() {}, onNewChat() {}, onSelect() {}, onDelete() {},
    selectedId: 'a', deletingId: null, blockedDeleteId: 'a' };
  const pending = buttons(loaded.exports.ConversationList(props));
  assert.equal(pending.find(button => button.props['aria-label'] === '카페 추천 대화 삭제')?.props.disabled, true);
  assert.equal(pending.find(button => button.props['aria-label'] === '주유 추천 대화 삭제')?.props.disabled, false);
  const deleting = buttons(loaded.exports.ConversationList({ ...props, blockedDeleteId: null, deletingId: 'b' }));
  assert.equal(deleting.find(button => button.props['aria-label'] === '카페 추천 대화 삭제')?.props.disabled, true);
  assert.equal(deleting.find(button => button.props['aria-label'] === '주유 추천 대화 삭제')?.props.disabled, true);
});
