import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { cardProducts } from '../../../../lib/card-products';

export const runtime = 'nodejs';

export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const card = cardProducts.find((item) => item.id === id);
  if (!card) return new Response('상품설명서를 찾을 수 없습니다.', { status: 404 });

  try {
    const pdf = await readFile(path.join(process.cwd(), '../../data/raw', card.sourcePath.slice('data/raw/'.length)));
    return new Response(pdf, {
      headers: {
        'Content-Type': 'application/pdf',
        'Content-Disposition': `inline; filename="card.pdf"; filename*=UTF-8''${encodeURIComponent(card.name)}.pdf`,
        'Cache-Control': 'public, max-age=86400',
        'X-Content-Type-Options': 'nosniff',
      },
    });
  } catch (error) {
    console.error('Card document could not be read:', card.id, error);
    return new Response('상품설명서를 불러오지 못했습니다.', { status: 500 });
  }
}
