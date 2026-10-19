import { useLocation } from 'react-router-dom';
import { navLabelOfPath } from './navConfig';
import { BrandMark } from './BrandMark';

/**
 * Верхняя полоса узкого режима: знак, название раздела и кнопка меню.
 *
 * Стоит в потоке и липнет к верху — перекрывать содержимое ей нечем по
 * устройству (плавающая кнопка всегда висела НАД чем-нибудь). Заодно отвечает
 * на вопрос «где я»: боковой колонки на узком экране нет.
 */
export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { pathname } = useLocation();
  const section = navLabelOfPath(pathname);

  return (
    <header className="topbar">
      <BrandMark compact className="topbar__mark" />
      {section && <span className="topbar__section">{section}</span>}
      <button
        type="button"
        className="topbar__burger"
        onClick={onMenu}
        aria-label="Открыть меню разделов"
      >
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <line x1="3" y1="6" x2="21" y2="6"/>
          <line x1="3" y1="12" x2="21" y2="12"/>
          <line x1="3" y1="18" x2="21" y2="18"/>
        </svg>
      </button>
    </header>
  );
}
