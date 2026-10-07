import assert from 'node:assert/strict';
import test from 'node:test';

import { readRegisteredCards } from '../lib/registered-cards.ts';

test('등록한 카드 순서를 유지하면서 중복과 유효하지 않은 항목을 제외한다', () => {
  assert.deepEqual(
    readRegisteredCards(JSON.stringify(['삼성 iD ALL 카드', '신한카드 Point Plan+', '삼성 iD ALL 카드', '없는 카드', null, 1])),
    ['삼성 iD ALL 카드', '신한카드 Point Plan+'],
  );
});

test('저장된 목록이 없거나 손상돼도 카드 화면을 열 수 있다', () => {
  for (const value of [null, '', '{broken', '{}', 'null', '42', '"카드"']) {
    assert.deepEqual(readRegisteredCards(value), []);
  }
});
