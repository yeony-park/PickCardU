import { cardProducts } from './card-products.ts';

export const registeredCardsKey = 'pickcardu.registered-cards.v1';

export function readRegisteredCards(value: string | null): string[] {
  if (!value) return [];
  try {
    const names: unknown = JSON.parse(value);
    if (!Array.isArray(names)) return [];
    return [...new Set(names.filter((name): name is string =>
      typeof name === 'string' && cardProducts.some((card) => card.name === name),
    ))];
  } catch {
    return [];
  }
}
