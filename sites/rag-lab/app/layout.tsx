import type { Metadata } from 'next';
import './globals.css';

const siteOrigin = new URL(process.env.SITE_ORIGIN ?? 'http://localhost:5173');

export const metadata: Metadata = {
  metadataBase: siteOrigin,
  title: 'PickCardU RAG Lab',
  description: '검색 가중치와 모델을 실험하고 팀 결과를 비교하는 PickCardU 내부 RAG 대시보드',
  openGraph: {
    title: 'PickCardU RAG Lab',
    description: 'Test. Trace. Compare.',
    images: [{ url: '/og.png', width: 1672, height: 941, alt: 'PickCardU RAG Lab' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'PickCardU RAG Lab',
    description: 'Test. Trace. Compare.',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
