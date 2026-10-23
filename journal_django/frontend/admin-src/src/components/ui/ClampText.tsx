import { useLayoutEffect, useRef, useState } from 'react';
import { CLAMP_LABELS } from '../../lib/labels';

interface Props {
  text: string;
  /** Для колонки таблицы — `clamp-text--column`: сам компонент ширину не задаёт. */
  className?: string;
}

/**
 * Длинный текст: две строки, по кнопке — целиком. Кнопка нужна, только если текст
 * реально обрезан: измерение scrollHeight > clientHeight повторяется при смене
 * ширины (ResizeObserver).
 */
export function ClampText({ text, className }: Props) {
  const [open, setOpen] = useState(false);
  const [overflows, setOverflows] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const el = bodyRef.current;
    // Раскрытый текст не измеряем: кнопка «Свернуть» остаётся по состоянию open.
    if (!el || open) return;
    const measure = () => setOverflows(el.scrollHeight > el.clientHeight + 1);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [text, open]);

  const showButton = overflows || open;
  return (
    <div className={`clamp-text${className ? ` ${className}` : ''}`}>
      <div ref={bodyRef} className={`clamp-text__body${open ? '' : ' clamp-text__body--clamped'}`}>{text}</div>
      {showButton && (
        <button type="button" className="clamp-text__more" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? CLAMP_LABELS.collapse : CLAMP_LABELS.expand}
        </button>
      )}
    </div>
  );
}
