import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

/**
 * Лёгкая модалка на токенах (портал в #modal-host, есть в обоих index.html —
 * admin и teacher). Esc и клик по фону — закрытие. Используется LessonPopup
 * (календарь) и teacher-src/components/lessons/LessonForm (не календарь —
 * оставлен на реэкспорте teacher-src/components/ui/Modal.tsx, чтобы не
 * трогать импорт в LessonForm).
 */
export function Modal({
  title,
  subtitle,
  onClose,
  busy = false,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  onClose: () => void;
  /**
   * Идёт запрос — закрывать нельзя ни Esc, ни кликом по фону, ни крестиком.
   *
   * Форма, закрытая во время отправки, теряет ответ НАВСЕГДА: колбэки, переданные
   * в mutate(), после размонтирования компонента не вызываются, и человек не
   * узнаёт ни об успехе, ни об ошибке. Именно так и вышел инцидент ПГ215 —
   * преподаватель не дождался подтверждения и отправил урок ещё дважды.
   */
  busy?: boolean;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !busy) onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose, busy]);

  const host = document.getElementById('modal-host') || document.body;

  return createPortal(
    <div className="t-modal-overlay" onClick={() => { if (!busy) onClose(); }}>
      <div className="t-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="t-modal-head">
          <div>
            <div className="t-modal-title">{title}</div>
            {subtitle != null && <div className="t-modal-sub">{subtitle}</div>}
          </div>
          <button
            type="button"
            className="t-modal-close"
            onClick={onClose}
            disabled={busy}
            aria-label="Закрыть"
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>
        <div className="t-modal-body">{children}</div>
      </div>
    </div>,
    host,
  );
}
