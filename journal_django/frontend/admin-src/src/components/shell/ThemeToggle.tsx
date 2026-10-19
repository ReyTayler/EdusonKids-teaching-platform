import { useTheme } from '../../providers/ThemeProvider';

interface Props {
  /** Класс кнопки. По умолчанию — кнопка-иконка верхней шапки: его же использует
   *  teacher SPA, у которого шапка осталась верхней. */
  className?: string;
  /** Показать подпись рядом со значком (боковая колонка admin). */
  showLabel?: boolean;
}

/** Переключатель темы: смысл всегда несёт aria-label, подпись — по желанию. */
export function ThemeToggle({ className = 'topnav__icon-btn', showLabel }: Props = {}) {
  const { theme, toggle } = useTheme();
  const label = theme === 'dark' ? 'Светлая тема' : 'Тёмная тема';
  return (
    <button type="button" className={className} onClick={toggle} title={label} aria-label={label}>
      {theme === 'dark' ? (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <circle cx="12" cy="12" r="5"/>
          <line x1="12" y1="1" x2="12" y2="3"/>
          <line x1="12" y1="21" x2="12" y2="23"/>
          <line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/>
          <line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/>
          <line x1="1" y1="12" x2="3" y2="12"/>
          <line x1="21" y1="12" x2="23" y2="12"/>
          <line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/>
          <line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>
        </svg>
      ) : (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>
        </svg>
      )}
      {showLabel && <span className="side-btn__label">{label}</span>}
    </button>
  );
}
