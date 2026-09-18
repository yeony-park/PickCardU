'use client';

import Link, { LinkProps } from 'next/link';
import { useRouter } from 'next/navigation';
import { AnchorHTMLAttributes, MouseEvent, useEffect } from 'react';
import { settleTransition, waitForPath } from '../../lib/view-transition';

type TransitionLinkProps = LinkProps & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, keyof LinkProps>;
type ViewTransition = { finished: Promise<void> };
type TransitionDocument = Document & { startViewTransition?: (callback: () => void | Promise<void>) => ViewTransition };

const pageOrder: Record<string, number> = {
  '/': 0,
  '/chat': 1,
  '/cards': 2,
};

export function TransitionLink({ href, onClick, ...props }: TransitionLinkProps) {
  const router = useRouter();

  useEffect(() => {
    const nextHref = typeof href === 'string' ? href : href.pathname;
    if (!nextHref || window.location.pathname === nextHref) return;
    router.prefetch(nextHref);
  }, [href, router]);

  function navigate(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const nextHref = typeof href === 'string' ? href : href.pathname;
    if (!nextHref || window.location.pathname === nextHref) return;
    const transitionDocument = document as TransitionDocument;
    if (!transitionDocument.startViewTransition) return;
    event.preventDefault();
    const currentOrder = pageOrder[window.location.pathname];
    const nextOrder = pageOrder[nextHref];
    const direction = currentOrder !== undefined && nextOrder !== undefined && nextOrder < currentOrder
      ? 'up'
      : 'down';
    document.documentElement.dataset.transitionDirection = direction;
    const transition = transitionDocument.startViewTransition(async () => {
      router.push(nextHref);
      await waitForPath(nextHref);
    });
    void settleTransition(transition.finished, () => {
      delete document.documentElement.dataset.transitionDirection;
    });
  }

  return <Link href={href} onClick={navigate} prefetch {...props} />;
}
