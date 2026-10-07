'use client';

import { useState } from 'react';
import { SiteHeader } from '../components/site-header';
import { ProductCard } from '../components/product-card';
import { cardProducts, issuers, type Issuer } from '../../lib/card-products';

export default function CardsPage() {
  const [selectedIssuer, setSelectedIssuer] = useState<Issuer>('전체');
  const [search, setSearch] = useState('');
  const [visibleCount, setVisibleCount] = useState(12);
  const query = search.trim().toLocaleLowerCase();
  const matchingCards = cardProducts.filter((card) =>
    (selectedIssuer === '전체' || card.issuer === selectedIssuer)
    && `${card.name} ${card.issuer}`.toLocaleLowerCase().includes(query),
  );
  const visibleCards = matchingCards.slice(0, visibleCount);

  return (
    <main className="page-shell cards-page">
      <SiteHeader active="cards" />
      <section className="cards-content" aria-labelledby="cards-title">
        <div className="cards-intro">
          <h1 id="cards-title">Cards that fit your life</h1>
          <p>카드사별 상품을 살펴보고, 내 생활에 맞는 카드의 혜택을 확인해보세요.</p>
        </div>

        <div className="cards-search-row">
          <div><label className="sr-only" htmlFor="cards-search">카드 검색</label>
            <input id="cards-search" className="mypage-search" type="search" placeholder="카드명 또는 카드사 검색" value={search} onChange={(event) => { setSearch(event.target.value); setVisibleCount(12); }} />
          </div>
          <p role="status">전체 {cardProducts.length}개 중 <strong>{matchingCards.length}개</strong></p>
        </div>
        <div className="issuer-tabs" aria-label="카드사 선택" role="tablist">
          {issuers.map((issuer) => (
            <button
              aria-selected={selectedIssuer === issuer}
              className={selectedIssuer === issuer ? 'active' : ''}
              key={issuer}
              onClick={() => { setSelectedIssuer(issuer); setVisibleCount(12); }}
              role="tab"
              type="button"
            >
              {issuer}
            </button>
          ))}
        </div>

        <div className="product-list" aria-live="polite">
          {visibleCards.map((card) => (
            <ProductCard card={card} key={card.id} />
          ))}
          {visibleCards.length === 0 && (
            <p className="product-empty-state">
              검색한 카드를 찾지 못했어요. 카드명이나 카드사를 다시 확인해 주세요.
            </p>
          )}
        </div>
        {visibleCount < matchingCards.length && (
          <button className="cards-load-more" onClick={() => setVisibleCount((count) => count + 12)} type="button">카드 더 보기 ({matchingCards.length - visibleCount}) <span aria-hidden="true">↓</span></button>
        )}
      </section>
    </main>
  );
}
