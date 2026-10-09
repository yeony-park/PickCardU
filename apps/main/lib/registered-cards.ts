import { cardProducts, type CardProduct } from './card-products.ts';
import type { components } from '../../../packages/contracts/generated/api';

export const registeredCardsKey = 'pickcardu.registered-cards.v1';

export function cardKeyFromSourcePath(sourcePath: string): string | null {
  if (!sourcePath.startsWith('data/raw/') || !sourcePath.endsWith('.pdf') || sourcePath.includes('\\')) return null;
  const key = sourcePath.slice('data/raw/'.length, -'.pdf'.length);
  const segments = key.split('/');
  if (key.length > 64 || key !== key.trim() || segments.length < 2
    || segments.some(segment => !segment || segment === '.' || segment === '..')) return null;
  return key;
}

export function buildWalletContext(rawStorage: string | null | undefined, catalog: readonly CardProduct[] = cardProducts):
  components['schemas']['WalletContext'] & { card_keys: string[] } {
  if (rawStorage === null) return { status: 'empty', card_keys: [] };
  const unknown = { status: 'needs_review' as const, card_keys: [] as string[] };
  try {
    const names: unknown = JSON.parse(rawStorage ?? '');
    if (!Array.isArray(names) || names.some(name => typeof name !== 'string')) return unknown;
    const keys: string[] = [];
    for (const name of new Set(names as string[])) {
      const matches = catalog.filter(card => card.name === name);
      if (matches.length !== 1) return unknown;
      const key = cardKeyFromSourcePath(matches[0].sourcePath);
      if (key === null || keys.includes(key)) return unknown;
      keys.push(key);
    }
    if (keys.length > 106) return unknown;
    return { status: keys.length ? 'ready' : 'empty', card_keys: keys };
  } catch { return unknown; }
}

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
