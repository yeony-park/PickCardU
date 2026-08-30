'use client';

import { useState } from 'react';
import { SiteHeader } from '../components/site-header';

const issuers = [
  '전체',
  '신한카드',
  '삼성카드',
  'KB국민카드',
  '현대카드',
  '롯데카드',
  '하나카드',
  '우리카드',
  'NH농협카드',
  'BC카드',
  'IBK기업은행',
] as const;

const cardProducts = [
  {
    issuer: '신한카드',
    name: '신한 Pick Daily',
    visuals: [
      { name: 'DAILY', tone: 'product-coral' },
      { name: 'DAILY', tone: 'product-mint' },
      { name: 'DAILY', tone: 'product-silver' },
    ],
    summary: '매일 반복되는 생활비를 가볍게 줄여주는 카드',
    benefitTag: '월 최대 4.2만원 혜택',
    benefits: [
      { label: '편의점·배달', value: '10% 할인' },
      { label: '대중교통', value: '5% 할인' },
      { label: '온라인 쇼핑', value: '7% 할인' },
    ],
    annualFee: '국내 15,000원 · 해외 18,000원',
    requirement: '전월실적 30만원 이상',
  },
  {
    issuer: '삼성카드',
    name: '삼성 iD Lifestyle',
    visuals: [
      { name: 'iD LIFE', tone: 'product-blue' },
      { name: 'iD LIFE', tone: 'product-lilac' },
      { name: 'iD LIFE', tone: 'product-silver' },
    ],
    summary: '구독과 디지털 생활에 집중한 라이프스타일 카드',
    benefitTag: '월 최대 5.8만원 혜택',
    benefits: [
      { label: '스트리밍', value: '30% 할인' },
      { label: '카페', value: '10% 할인' },
      { label: '통신요금', value: '10% 할인' },
    ],
    annualFee: '국내외 20,000원',
    requirement: '전월실적 40만원 이상',
  },
  {
    issuer: 'KB국민카드',
    name: 'KB Easy Pick',
    visuals: [
      { name: 'EASY', tone: 'product-lime' },
      { name: 'EASY', tone: 'product-coral' },
      { name: 'EASY', tone: 'product-blue' },
    ],
    summary: '자주 쓰는 영역을 알아서 골라 혜택을 더하는 카드',
    benefitTag: '월 최대 6.1만원 혜택',
    benefits: [
      { label: '간편결제', value: '10% 할인' },
      { label: '마트', value: '7% 할인' },
      { label: '주유', value: '리터당 80원' },
    ],
    annualFee: '국내 17,000원 · 해외 20,000원',
    requirement: '전월실적 40만원 이상',
  },
  {
    issuer: '현대카드',
    name: '현대 Z Work',
    visuals: [
      { name: 'Z WORK', tone: 'product-silver' },
      { name: 'Z WORK', tone: 'product-blue' },
    ],
    summary: '출퇴근과 점심시간의 소비에 맞춘 직장인 카드',
    benefitTag: '월 최대 4.8만원 혜택',
    benefits: [
      { label: '대중교통', value: '10% 할인' },
      { label: '점심 식당', value: '10% 할인' },
      { label: '편의점', value: '10% 할인' },
    ],
    annualFee: '국내외 20,000원',
    requirement: '전월실적 50만원 이상',
  },
  {
    issuer: '롯데카드',
    name: 'LOCA LIKIT Eat',
    visuals: [
      { name: 'LIKIT', tone: 'product-lilac' },
      { name: 'LIKIT', tone: 'product-coral' },
    ],
    summary: '외식과 주류 소비가 많은 주말을 위한 카드',
    benefitTag: '월 최대 5.5만원 혜택',
    benefits: [
      { label: '음식점', value: '15% 할인' },
      { label: '주류', value: '10% 할인' },
      { label: '택시', value: '10% 할인' },
    ],
    annualFee: '국내 10,000원 · 해외 10,000원',
    requirement: '전월실적 40만원 이상',
  },
  {
    issuer: '신한카드',
    name: '신한 Voyage',
    visuals: [
      { name: 'VOYAGE', tone: 'product-mint' },
      { name: 'VOYAGE', tone: 'product-blue' },
    ],
    summary: '여행 준비부터 현지 결제까지 이어지는 마일리지 카드',
    benefitTag: '해외 결제 2배 적립',
    benefits: [
      { label: '항공 마일리지', value: '1.5배 적립' },
      { label: '해외 결제', value: '2배 적립' },
      { label: '공항 라운지', value: '연 2회' },
    ],
    annualFee: '국내외 35,000원',
    requirement: '전월실적 50만원 이상',
  },
] as const;

type Issuer = (typeof issuers)[number];
type CardProduct = (typeof cardProducts)[number];

function ProductCard({ card }: { card: CardProduct }) {
  const [selectedVisual, setSelectedVisual] = useState(0);
  const visual = card.visuals[selectedVisual];

  return (
    <article className="card-product">
      <div className="product-visual-wrap">
        <div className="product-card-flipper" aria-hidden="true">
          <div className={`product-card-visual product-card-front ${visual.tone}`}>
            <div className="product-card-top">
              <span>{card.issuer}</span>
              <i />
            </div>
            <strong>{visual.name}</strong>
            <span className="product-card-brand">PickCardU</span>
          </div>
          <div className={`product-card-visual product-card-back ${visual.tone}`}>
            <span className="product-card-stripe" />
            <span className="product-card-signature">PickCardU MEMBER</span>
            <span className="product-card-number">•••• 0827</span>
          </div>
        </div>

        {card.visuals.length > 1 && (
          <div className="product-visual-dial" aria-label={`${card.name} 카드 디자인 선택`}>
            {card.visuals.map((option, index) => (
              <button
                aria-label={`${index + 1}번 디자인: ${option.name}`}
                aria-pressed={selectedVisual === index}
                className={selectedVisual === index ? 'active' : ''}
                key={`${option.tone}-${index}`}
                onClick={() => setSelectedVisual(index)}
                type="button"
              />
            ))}
          </div>
        )}
      </div>

      <div className="product-copy">
        <div className="product-heading">
          <div>
            <span className="product-issuer">{card.issuer}</span>
            <h2>{card.name}</h2>
            <p>{card.summary}</p>
          </div>
        </div>

        <span className="benefit-tag">{card.benefitTag}</span>

        <div className="product-benefits">
          {card.benefits.map((benefit) => (
            <div key={benefit.label}>
              <span>{benefit.label}</span>
              <strong>{benefit.value}</strong>
            </div>
          ))}
        </div>

        <div className="product-meta">
          <span>{card.annualFee}</span>
          <span>{card.requirement}</span>
        </div>

        <button className="product-detail-button" type="button">
          자세히 보기 <span aria-hidden="true">↗</span>
        </button>
      </div>
    </article>
  );
}

export default function CardsPage() {
  const [selectedIssuer, setSelectedIssuer] = useState<Issuer>('전체');
  const visibleCards = selectedIssuer === '전체'
    ? cardProducts
    : cardProducts.filter((card) => card.issuer === selectedIssuer);

  return (
    <main className="page-shell cards-page">
      <SiteHeader active="cards" />
      <section className="cards-content" aria-labelledby="cards-title">
        <div className="cards-intro">
          <h1 id="cards-title">Cards that fit your life</h1>
          <p>카드사별로 살펴보고, 자주 쓰는 곳에서 가장 큰 혜택을 주는 카드를 비교해보세요.</p>
        </div>

        <div className="issuer-tabs" aria-label="카드사 선택" role="tablist">
          {issuers.map((issuer) => (
            <button
              aria-selected={selectedIssuer === issuer}
              className={selectedIssuer === issuer ? 'active' : ''}
              key={issuer}
              onClick={() => setSelectedIssuer(issuer)}
              role="tab"
              type="button"
            >
              {issuer}
            </button>
          ))}
        </div>

        <div className="product-list" aria-live="polite">
          {visibleCards.map((card) => (
            <ProductCard card={card} key={card.name} />
          ))}
          {visibleCards.length === 0 && (
            <p className="product-empty-state">
              {selectedIssuer} 카드 상품은 준비 중입니다.
            </p>
          )}
        </div>
      </section>
    </main>
  );
}
