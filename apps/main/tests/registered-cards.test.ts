import assert from 'node:assert/strict';
import test from 'node:test';

import { readRegisteredCards } from '../lib/registered-cards.ts';
import * as registered from '../lib/registered-cards.ts';
import { cardProducts } from '../lib/card-products.ts';

test('wallet names map exactly to source document keys without changing My Page storage', () => {
  assert.ok('buildWalletContext' in registered, 'wallet mapping is missing');
  const wallet = registered.buildWalletContext(JSON.stringify(cardProducts.map(card => card.name)));
  assert.equal(wallet.status, 'ready');
  assert.equal(wallet.card_keys.length, 106);
  assert.equal(new Set(wallet.card_keys).size, 106);
  assert.equal(registered.cardKeyFromSourcePath('data/raw/shinhan/Shinhan_Point_Plan+_20240801.pdf'),
    'shinhan/Shinhan_Point_Plan+_20240801');
  for (const value of [null, '[]']) assert.deepEqual(registered.buildWalletContext(value), { status: 'empty', card_keys: [] });
  for (const value of ['{broken', '{}', '["unknown"]', '[null]', '42']) {
    assert.deepEqual(registered.buildWalletContext(value), { status: 'needs_review', card_keys: [] });
  }
  for (const source of ['data/raw/../secret.pdf', 'data/raw/a/../../secret.pdf', 'other/a.pdf', 'data/raw/a\\b.pdf']) {
    assert.equal(registered.cardKeyFromSourcePath(source), null);
  }
});

test('ambiguous future catalog names fail closed rather than picking the first product', () => {
  assert.ok('buildWalletContext' in registered);
  const duplicate = { ...cardProducts[0], sourcePath: 'data/raw/other/distinct.pdf' };
  assert.deepEqual(registered.buildWalletContext(JSON.stringify([duplicate.name]), [cardProducts[0], duplicate]),
    { status: 'needs_review', card_keys: [] });
});

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
