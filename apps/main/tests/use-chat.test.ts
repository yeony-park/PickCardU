import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';

const require = createRequire(new URL('../package.json', import.meta.url));
const ts = require('typescript');
type ChatState = ReturnType<typeof import('../lib/use-chat.ts').useChat>;

// Exercise the real hook's async control flow without adding a DOM/test dependency.
function harness() {
  const states: unknown[] = [], refs: { current: unknown }[] = [], effects: (() => unknown)[] = [];
  let stateIndex = 0, refIndex = 0, initial = true;
  const storage = new Map<string, string>();
  const globals = new Map<string, PropertyDescriptor | undefined>();
  for (const [key, value] of Object.entries({
    window: { location: { href: 'http://localhost/chat' }, history: { pushState() {} }, addEventListener() {}, removeEventListener() {} },
    sessionStorage: { getItem: (key: string) => storage.get(key) ?? null, setItem: (key: string, value: string) => storage.set(key, value), removeItem: (key: string) => storage.delete(key) },
    localStorage: { getItem: () => null },
  })) {
    globals.set(key, Object.getOwnPropertyDescriptor(globalThis, key));
    Object.defineProperty(globalThis, key, { configurable: true, value });
  }
  let resolveCreation!: (value: { id: string }) => void;
  const creation = new Promise<{ id: string }>(resolve => { resolveCreation = resolve; });
  const sends: string[] = [];
  const api = {
    initializeBrowser: async () => {}, listConversations: async () => ({ conversations: [], next_cursor: null }),
    getMessages: async () => ({ messages: [], next_before_seq: null, survey_context: null }),
    createConversation: () => creation,
    sendMessage: async (id: string) => { sends.push(id); return { messages: [] }; },
    buildTurnRequest: (query: string, client_request_id: string) => ({ query, client_request_id }),
  };
  const react = {
    useState(value: unknown) {
      const index = stateIndex++;
      if (initial) states[index] = value;
      return [states[index], (next: unknown) => { states[index] = typeof next === 'function' ? next(states[index]) : next; }];
    },
    useRef(value: unknown) {
      const index = refIndex++;
      if (initial) refs[index] = { current: value };
      return refs[index];
    },
    useCallback: (callback: unknown) => callback,
    useEffect: (effect: () => unknown) => { if (initial) effects.push(effect); },
  };
  const dependencies: Record<string, unknown> = {
    react, './chat-api': api, './chat-state': require('./lib/chat-state.ts'),
    './registered-cards': { registeredCardsKey: 'pickcardu.registered-cards.v1' },
  };
  const hookModule = { exports: {} as { useChat: () => ChatState } };
  const source = ts.transpileModule(readFileSync(new URL('../lib/use-chat.ts', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS },
  }).outputText;
  new Function('require', 'module', 'exports', source)((name: string) => dependencies[name], hookModule, hookModule.exports);
  function render() {
    stateIndex = refIndex = 0;
    const hook = hookModule.exports.useChat();
    initial = false;
    return hook;
  }
  render();
  const cleanups = effects.map(effect => effect());
  let cleaned = false;
  function unmount() {
    if (cleaned) return;
    cleaned = true;
    for (const cleanup of cleanups) if (typeof cleanup === 'function') cleanup();
  }
  return {
    render, sends, resolveCreation, unmount,
    cleanup() {
      unmount();
      for (const [key, descriptor] of globals) {
        if (descriptor) Object.defineProperty(globalThis, key, descriptor);
        else Reflect.deleteProperty(globalThis, key);
      }
    },
  };
}

for (const next of ['new', 'existing', 'unmount', 'stay']) {
  test(`conversation creation: ${next} determines whether the original question is sent`, async () => {
    const hook = harness();
    try {
      await new Promise(resolve => setImmediate(resolve));
      const state = hook.render();
      assert.equal(state.ready, true);
      const pending = state.sendQuestion('내 소비 패턴 카드 추천');
      if (next === 'new') state.startNewChat();
      if (next === 'existing') await state.selectConversation('existing-conversation');
      if (next === 'unmount') hook.unmount();
      hook.resolveCreation({ id: 'created-conversation' });
      assert.equal(await pending, next === 'stay');
      assert.deepEqual(hook.sends, next === 'stay' ? ['created-conversation'] : []);
      if (next !== 'unmount') assert.equal(hook.render().selectedId,
        next === 'stay' ? 'created-conversation' : next === 'existing' ? 'existing-conversation' : null);
    } finally { hook.cleanup(); }
  });
}
