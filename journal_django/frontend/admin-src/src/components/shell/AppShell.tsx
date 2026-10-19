import { useState, useEffect } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import { Sidebar } from './Sidebar';
import { SidebarDrawer } from './SidebarDrawer';
import { TopBar } from './TopBar';
import { ScrollTopButton } from './ScrollTopButton';
import { PaymentModalProvider } from '../../providers/PaymentModalProvider';
import { ErrorBoundary } from './ErrorBoundary';

/**
 * Ниже этой ширины боковая колонка не помещается рядом с содержимым и уступает
 * место верхней полосе с меню. 1180 = 272px колонки + ~900px содержимого:
 * столько таблице хватает; ноутбуки 1366/1440 остаются с колонкой на экране.
 */
const NARROW_BREAKPOINT = 1180;

/** Ключ памяти о том, свёрнута ли колонка в рельсу. */
const RAIL_KEY = 'admin:nav-rail';

function readRail(): boolean {
  try { return localStorage.getItem(RAIL_KEY) === '1'; } catch { return false; }
}

function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== 'undefined' && window.innerWidth < NARROW_BREAKPOINT,
  );
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < NARROW_BREAKPOINT);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return narrow;
}

/**
 * Оболочка admin SPA: слева колонка навигации (или рельса), справа страница.
 * В узком режиме колонки нет — вместо неё липкая полоса TopBar и меню в Dialog.
 */
export function AppShell() {
  const isNarrow = useIsNarrow();
  // Рельса — не «спрятать колонку», а сузить её до значков: навигация остаётся
  // на экране, а таблице достаются освободившиеся 208px. Состояние переживает
  // перезагрузку — это настройка рабочего места, а не режим одной сессии.
  const [rail, setRail] = useState(readRail);
  const [mobileOpen, setMobileOpen] = useState(false);
  const location = useLocation();

  // Закрыть меню при переходе и при выходе из узкого режима: иначе флаг
  // остаётся true и меню само всплывает, когда окно снова сузят.
  useEffect(() => { setMobileOpen(false); }, [location.pathname, isNarrow]);

  const toggleRail = () => {
    setRail((r) => {
      const next = !r;
      try { localStorage.setItem(RAIL_KEY, next ? '1' : '0'); } catch { /* приватный режим */ }
      return next;
    });
  };

  return (
    <PaymentModalProvider>
      <div className="shell shell--side">
        {!isNarrow && (
          <Sidebar
            rail={rail}
            onToggle={toggleRail}
            toggleLabel={rail ? 'Развернуть боковую панель' : 'Свернуть боковую панель'}
          />
        )}
        {/* main--topbar публикует высоту липкой полосы (--topbar-h/--header-h):
            расчёты высот канбанов и таблиц вычитают её из окна. На десктопе
            полосы нет и обе переменные равны 0. Класс, а не медиазапрос, —
            чтобы порог жил в одном месте (NARROW_BREAKPOINT). */}
        <main className={`main${isNarrow ? ' main--topbar' : ''}`} id="admin-main">
          {isNarrow && <TopBar onMenu={() => setMobileOpen(true)} />}
          {/* Сброс boundary по pathname (смена раздела), НЕ по location.key —
              key меняется на каждый setSearchParams (фильтр/пагинация/сортировка)
              и ремоунтил бы всю страницу на каждый символ фильтра. */}
          <ErrorBoundary key={location.pathname}>
            <div className="app-page">
              <Outlet />
            </div>
          </ErrorBoundary>
        </main>
        {isNarrow && <SidebarDrawer open={mobileOpen} onOpenChange={setMobileOpen} />}
        <ScrollTopButton />
      </div>
    </PaymentModalProvider>
  );
}
