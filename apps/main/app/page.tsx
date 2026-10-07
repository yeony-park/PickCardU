import Image from 'next/image';
import { cardProducts } from '../lib/card-products';
import { TransitionLink } from './components/transition-link';
import { SiteHeader } from './components/site-header';

const featuredCardIds = [
  'hyundai__Hyundai_M',
  'lotte__Lotte_Hilton_Honors_Amex_Premium',
  'BC__BC_ON&OFF',
  'hana__Hana_JadeFirst',
  'woori__Woori_Classic2',
  'kookmin__Kookmin_Coupang_Wow_20250702',
];
const cards = featuredCardIds.flatMap((id) => cardProducts.filter((card) => card.id === id));

function CardMarquee() {
  return (
    <div className="card-marquee" aria-label="카드사별 대표 카드 미리보기">
      <div className="card-track">
        {[0, 1].map((setIndex) => (
          <div className="card-set" aria-hidden={setIndex === 1} key={setIndex}>
            {cards.map((card) => (
              <article className="credit-card" key={`${setIndex}-${card.id}`}>
                <Image
                  src={card.image!}
                  alt={setIndex === 0 ? `${card.issuer} · ${card.name}` : ''}
                  width={205}
                  height={320}
                  sizes="(max-width: 760px) 148px, 205px"
                  loading="eager"
                />
              </article>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <main className="page-shell home-page">
      <SiteHeader active="home" />
      <section className="hero" aria-labelledby="hero-title">
        <div className="hero-copy">
          <h1 id="hero-title">Pick Cards for You</h1>
        </div>
        <CardMarquee />
        <p className="hero-description">
          매일 어디에 얼마나 쓰는지 생활 패턴을 이해하고,<br />
          수많은 혜택 가운데 나에게 꼭 맞는 카드만 골라드려요.
        </p>
        <TransitionLink className="primary-cta" href="/chat">
          Find My Card
          <span aria-hidden="true">↘</span>
        </TransitionLink>
      </section>
    </main>
  );
}
