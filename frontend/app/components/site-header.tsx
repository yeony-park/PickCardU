import { TransitionLink } from './transition-link';

type SiteHeaderProps = { active: 'home' | 'chat' | 'cards' };

export function SiteHeader({ active }: SiteHeaderProps) {
  return (
    <header className="site-header">
      <TransitionLink className="brand" href="/" aria-label="PickCardU 홈">
        <span className="brand-mark" aria-hidden="true"><i /><i /></span>
        PickCardU
      </TransitionLink>
      <nav aria-label="주요 메뉴">
        <TransitionLink className={active === 'home' ? 'active' : ''} href="/">Home</TransitionLink>
        <TransitionLink className={active === 'chat' ? 'active' : ''} href="/chat">Chat</TransitionLink>
        <TransitionLink className={active === 'cards' ? 'active' : ''} href="/cards">Cards</TransitionLink>
        <span aria-disabled="true">My Page</span>
      </nav>
    </header>
  );
}
