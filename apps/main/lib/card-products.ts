import catalog from './card-catalog.json' with { type: 'json' };

export const issuers = [
  '전체', '신한카드', '삼성카드', 'KB국민카드', '현대카드', '롯데카드',
  '하나카드', '우리카드', 'NH농협카드', 'BC카드', 'IBK기업은행',
] as const;

export const cardProducts = catalog;
export type Issuer = (typeof issuers)[number];
export type CardProduct = (typeof cardProducts)[number];
