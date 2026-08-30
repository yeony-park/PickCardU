import { TransitionLink } from './components/transition-link';
import { SiteHeader } from './components/site-header';

const cards = [
  { tone: 'card-blue', brand: 'TRAVEL', name: 'Sky Pass', rate: '2.0x' },
  { tone: 'card-lime', brand: 'DAILY', name: 'Everyday', rate: '1.5%' },
  { tone: 'card-coral', brand: 'DINING', name: 'Table', rate: '5.0%' },
  { tone: 'card-lilac', brand: 'SHOP', name: 'Weekend', rate: '3.0%' },
  { tone: 'card-mint', brand: 'MILEAGE', name: 'Voyage', rate: '1.8x' },
  { tone: 'card-silver', brand: 'PREMIUM', name: 'Signature', rate: '4.0%' },
];

function CardMarquee() {
  return (
    <div className="card-marquee" aria-label="추천 카드 미리보기">
      <div className="card-track">
        {[0, 1].map((setIndex) => (
          <div className="card-set" aria-hidden={setIndex === 1} key={setIndex}>
            {cards.map((card) => (
              <article className={`credit-card ${card.tone}`} key={`${setIndex}-${card.name}`}>
                <div className="card-topline">
                  <span>{card.brand}</span>
                  <span className="card-chip" aria-hidden="true" />
                </div>
                <div className="card-rate">{card.rate}</div>
                <div className="card-bottomline">
                  <span>{card.name}</span>
                  <span>PickCardU</span>
                </div>
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
