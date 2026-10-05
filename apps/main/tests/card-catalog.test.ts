import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { cardProducts, issuers } from '../lib/card-products.ts';

const root = path.resolve(import.meta.dirname, '../../..');
const manifest = JSON.parse(readFileSync(path.join(root, 'data/ocr_extract/v1/source-manifest.json'), 'utf8')) as {
  documents: { path: string; page_count: number }[];
};

test('모든 원본 PDF가 실제 상품 목록에 한 번씩 포함된다', () => {
  assert.deepEqual(cardProducts.map((card) => card.sourcePath).sort(), manifest.documents.map((document) => document.path).sort());
  assert.equal(new Set(cardProducts.map((card) => card.id)).size, cardProducts.length);
  assert.equal(new Set(cardProducts.map((card) => card.name)).size, cardProducts.length);
  for (const card of cardProducts) {
    assert.ok(issuers.some((issuer) => issuer === card.issuer));
    assert.ok(existsSync(path.join(root, card.sourcePath)));
    assert.equal(card.pageCount, manifest.documents.find((document) => document.path === card.sourcePath)?.page_count);
  }
});

test('카드 이미지와 출처가 존재하며 서로 다른 상품 이미지를 덮어쓰지 않는다', () => {
  const images = cardProducts.filter((card) => card.image);
  assert.equal(new Set(images.map((card) => card.image)).size, images.length);
  for (const card of images) {
    assert.ok(existsSync(path.join(root, 'apps/main/public', card.image!)));
    assert.ok(card.imageSource?.startsWith('https://'));
    assert.ok(card.imageSourcePage?.startsWith('https://'));
  }
  assert.deepEqual(cardProducts.filter((card) => !card.image).map((card) => card.id), ['BC__BC_Biz_AirMoney']);
});
