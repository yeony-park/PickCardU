import assert from 'node:assert/strict';
import test from 'node:test';

import { settleTransition, waitForPath } from '../lib/view-transition.ts';

test('렌더링 프레임 없이 경로 변경을 감지해 전환 대기를 끝낸다', async () => {
  let currentPath = '/';
  let scheduledCheck: (() => void) | undefined;

  const waiting = waitForPath('/chat', {
    readPath: () => currentPath,
    now: () => 0,
    scheduleTask: (callback) => {
      scheduledCheck = callback;
    },
  });

  assert.ok(scheduledCheck);
  currentPath = '/chat';
  scheduledCheck();

  await waiting;
});

test('브라우저가 전환을 중단해도 정리 후 정상 종료한다', async () => {
  let cleanedUp = false;

  await settleTransition(
    Promise.reject(new DOMException('DOM update timed out', 'TimeoutError')),
    () => {
      cleanedUp = true;
    },
  );

  assert.equal(cleanedUp, true);
});
