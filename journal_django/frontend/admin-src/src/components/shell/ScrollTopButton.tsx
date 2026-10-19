import { useEffect, useState } from 'react';

const THRESHOLD = 300;

export function ScrollTopButton() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const getMain = () => document.querySelector<HTMLElement>('main.main');

    const update = () => {
      const main = getMain();
      const mainY = main ? main.scrollTop : 0;
      const winY = window.scrollY || document.documentElement.scrollTop || 0;
      setVisible(Math.max(winY, mainY) > THRESHOLD);
    };

    window.addEventListener('scroll', update, { passive: true });

    const onDocScroll = (e: Event) => {
      const t = e.target as HTMLElement | null;
      if (t && t.classList && t.classList.contains('main')) update();
    };
    document.addEventListener('scroll', onDocScroll, true);

    return () => {
      window.removeEventListener('scroll', update);
      document.removeEventListener('scroll', onDocScroll, true);
    };
  }, []);

  const click = () => {
    window.scrollTo({ top: 0, behavior: 'smooth' });
    document.querySelector<HTMLElement>('main.main')?.scrollTo({ top: 0, behavior: 'smooth' });
  };

  return (
    <button
      id="scroll-top-btn"
      type="button"
      title="Наверх"
      aria-label="Прокрутить наверх"
      className={visible ? 'visible' : ''}
      onClick={click}
    >
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="18 15 12 9 6 15"/>
      </svg>
    </button>
  );
}
