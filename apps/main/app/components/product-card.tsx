import Image from 'next/image';
import type { ReactNode } from 'react';
import type { CardProduct } from '../../lib/card-products';

export function ProductCard({ card, children }: { card: CardProduct; children?: ReactNode }) {
  return (
    <article className="card-product">
      <div className="product-visual-wrap">
        {card.image ? (
          <Image className="product-real-image" src={card.image} alt={card.name} width={260} height={200} sizes="(max-width: 600px) 80vw, (max-width: 900px) 40vw, 260px" />
        ) : (
          <span className="product-image-unavailable">카드 이미지 준비 중</span>
        )}
      {card.imageNote && <span className="product-image-caption">{card.imageNote}</span>}
      </div>
      <div className="product-copy">
        <div className="product-heading">
          <span className="product-issuer">{card.issuer}</span>
          <h2>{card.name}</h2>
        </div>
        <span className="benefit-tag">혜택과 이용 조건</span>
        <p className="product-document-description">연회비, 전월실적, 할인·적립 조건을<br />상품설명서에서 자세히 확인하세요.</p>
        <div className="product-meta"><span>상품설명서 PDF · {card.pageCount}페이지</span></div>
        <a className="product-detail-button" href={`/api/card-documents/${encodeURIComponent(card.id)}`} target="_blank" rel="noopener noreferrer" aria-label={`${card.name} 상품설명서 보기 (새 탭)`}>
          상품설명서 보기 <span aria-hidden="true">↗</span>
        </a>
        {children}
      </div>
    </article>
  );
}
