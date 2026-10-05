'use client';

import Image from 'next/image';
import { useRef, useState, useSyncExternalStore } from 'react';
import { cardProducts } from '../../lib/card-products';
import { readRegisteredCards, registeredCardsKey } from '../../lib/registered-cards';
import { ProductCard } from '../components/product-card';
import { SiteHeader } from '../components/site-header';

const cardsChangedEvent = 'pickcardu:registered-cards-changed';

function subscribe(onChange: () => void) {
  function onStorage(event: StorageEvent) {
    if (event.key === registeredCardsKey || event.key === null) onChange();
  }
  window.addEventListener('storage', onStorage);
  window.addEventListener(cardsChangedEvent, onChange);
  return () => {
    window.removeEventListener('storage', onStorage);
    window.removeEventListener(cardsChangedEvent, onChange);
  };
}

function getSnapshot() {
  try {
    return window.localStorage.getItem(registeredCardsKey);
  } catch {
    return null;
  }
}

export default function MyPage() {
  const storedCards = useSyncExternalStore(subscribe, getSnapshot, () => null);
  const registeredNames = readRegisteredCards(storedCards);
  const registeredCards = registeredNames.flatMap((name) =>
    cardProducts.filter((card) => card.name === name),
  );
  const dialog = useRef<HTMLDialogElement>(null);
  const [search, setSearch] = useState('');
  const [message, setMessage] = useState('');
  const [error, setError] = useState('');
  const [removedCard, setRemovedCard] = useState<string | null>(null);
  const query = search.trim().toLocaleLowerCase();
  const availableCards = cardProducts.filter((card) =>
    `${card.name} ${card.issuer}`.toLocaleLowerCase().includes(query),
  );

  function saveCards(names: string[]) {
    try {
      window.localStorage.setItem(registeredCardsKey, JSON.stringify(names));
      window.dispatchEvent(new Event(cardsChangedEvent));
      setError('');
      return true;
    } catch {
      setError('카드를 저장하지 못했어요. 브라우저의 저장 공간 설정을 확인한 뒤 다시 시도해 주세요.');
      return false;
    }
  }

  function registerCard(name: string) {
    const currentNames = readRegisteredCards(getSnapshot());
    if (currentNames.includes(name)) return;
    if (saveCards([...currentNames, name])) {
      setRemovedCard(null);
      setMessage(`${name} 카드를 등록했어요.`);
    }
  }

  function removeCard(name: string) {
    if (saveCards(readRegisteredCards(getSnapshot()).filter((item) => item !== name))) {
      setRemovedCard(name);
      setMessage(`${name} 카드를 등록 목록에서 삭제했어요.`);
    }
  }

  function undoRemove() {
    if (!removedCard) return;
    const currentNames = readRegisteredCards(getSnapshot());
    if (saveCards([...new Set([...currentNames, removedCard])])) {
      setMessage(`${removedCard} 카드를 다시 등록했어요.`);
      setRemovedCard(null);
    }
  }

  function openRegistration() {
    setSearch('');
    setMessage('');
    dialog.current?.showModal();
  }

  return (
    <main className="page-shell cards-page mypage-page">
      <SiteHeader active="mypage" />
      <section className="cards-content mypage-content" aria-labelledby="mypage-title">
        <div className="mypage-intro">
          <div className="cards-intro">
            <h1 id="mypage-title">Your cards, all here.</h1>
            <p>내가 쓰는 카드를 모아두고, 필요한 혜택을 한눈에 확인하세요.</p>
          </div>
          <button className="mypage-add-button" onClick={openRegistration} type="button">
            <span aria-hidden="true">+</span> 카드 등록
          </button>
        </div>

        <div className="mypage-list-heading">
          <h2>내 카드 <span>{registeredCards.length}</span></h2>
          <p>등록한 카드는 이 브라우저에 저장돼요.</p>
        </div>
        <div className="mypage-feedback" role="status">
          {message}
          {removedCard && <button onClick={undoRemove} type="button">되돌리기</button>}
        </div>
        {error && <p className="mypage-error" role="alert">{error}</p>}

        {registeredCards.length > 0 ? (
          <div className="product-list mypage-card-list">
            {registeredCards.map((card) => (
              <ProductCard card={card} key={card.name}>
                <button
                  className="mypage-remove-button"
                  aria-label={`${card.name} 등록 삭제`}
                  onClick={() => removeCard(card.name)}
                  type="button"
                >등록 삭제 <span aria-hidden="true">×</span></button>
              </ProductCard>
            ))}
          </div>
        ) : (
          <div className="mypage-empty">
            <div className="mypage-empty-art" aria-hidden="true">
              <span className="product-lilac" />
              <span className="product-lime"><i /><strong>YOUR<br />PICK.</strong></span>
            </div>
            <h3>나의 첫 카드를 등록해 보세요</h3>
            <button className="mypage-add-button" onClick={openRegistration} type="button">
              <span aria-hidden="true">+</span> 첫 카드 등록하기
            </button>
          </div>
        )}
      </section>

      <dialog className="mypage-dialog" ref={dialog} aria-labelledby="register-title">
        <div className="mypage-dialog-heading">
          <div><p className="eyebrow">ADD TO MY WALLET</p><h2 id="register-title">어떤 카드를 쓰고 있나요?</h2></div>
          <button className="mypage-dialog-close" aria-label="카드 등록 창 닫기" onClick={() => dialog.current?.close()} type="button">×</button>
        </div>
        <p className="mypage-dialog-description">사용 중인 카드의 이름이나 카드사를 검색해 등록하세요.</p>
        <label className="sr-only" htmlFor="register-search">카드 검색</label>
        <input id="register-search" className="mypage-search" placeholder="카드명 또는 카드사 검색" value={search} onChange={(event) => setSearch(event.target.value)} type="search" />
        <div className="mypage-registration-list">
          {availableCards.map((card) => {
            const registered = registeredNames.includes(card.name);
            return (
              <div className="mypage-registration-item" key={card.name}>
                <div className="mypage-mini-card" aria-hidden="true">{card.image && <Image src={card.image} alt="" width={52} height={56} sizes="52px" />}</div>
                <div className="mypage-registration-copy"><span>{card.issuer}</span><strong>{card.name}</strong></div>
                <button aria-label={`${card.name} ${registered ? '등록됨' : '등록'}`} disabled={registered} onClick={() => registerCard(card.name)} type="button">{registered ? '등록됨 ✓' : '+ 등록'}</button>
              </div>
            );
          })}
          {availableCards.length === 0 && <p className="mypage-search-empty">검색한 카드를 찾지 못했어요.<br />카드명이나 카드사를 다시 확인해 주세요.</p>}
        </div>
        <div className="mypage-dialog-feedback" role="status">{message}</div>
        {error && <p className="mypage-error" role="alert">{error}</p>}
        <button className="product-detail-button" onClick={() => dialog.current?.close()} type="button">내 카드 보기 <span aria-hidden="true">↗</span></button>
      </dialog>
    </main>
  );
}
