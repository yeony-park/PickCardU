import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'PickCardU — Pick Cards for You',
  description: '소비 습관에 꼭 맞는 신용카드를 찾아주는 개인화 카드 큐레이터',
  openGraph: {
    title: 'PickCardU — Pick Cards for You',
    description: '소비 습관에 꼭 맞는 신용카드를 찾아주는 개인화 카드 큐레이터',
    type: 'website',
    images: [{ url: '/og.png', width: 1200, height: 630, alt: 'Pick Cards for You' }],
  },
  twitter: {
    card: 'summary_large_image',
    title: 'PickCardU — Pick Cards for You',
    description: '소비 습관에 꼭 맞는 신용카드를 찾아주는 개인화 카드 큐레이터',
    images: ['/og.png'],
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ko"><body>{children}</body></html>;
}
