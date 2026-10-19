import { useLayoutEffect, useState, type CSSProperties, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';

interface Props {
  /** Элемент, к которому «прилипает» попап (триггер/инпут). */
  anchorRef: RefObject<HTMLElement | null>;
  /** Ссылка на сам портал — нужна для click-outside в родителе. */
  floatingRef?: RefObject<HTMLDivElement | null>;
  open: boolean;
  className?: string;
  /** Растянуть по ширине якоря (списки select/combobox). Календарь — false. */
  matchWidth?: boolean;
  /** Потолок высоты; реальная высота ограничивается ещё и свободным местом до края экрана. */
  maxHeight?: number;
  gap?: number;
  children: ReactNode;
}

/**
 * Рендерит попап в document.body с position:fixed, спозиционированным по
 * bounding-rect якоря. Так выпадающий список не обрезается overflow-контейнерами
 * (например, скроллящимся телом модалки) и не требует прокрутки внутри неё.
 * Позиция пересчитывается на scroll (capture — ловим и скролл модалки) и resize.
 */
export function Floating({
  anchorRef,
  floatingRef,
  open,
  className,
  matchWidth = true,
  maxHeight = 320,
  gap = 4,
  children,
}: Props) {
  const [style, setStyle] = useState<CSSProperties>({ position: 'fixed', top: 0, left: 0, visibility: 'hidden' });

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = anchorRef.current;
    if (!anchor) return;

    const compute = () => {
      const r = anchor.getBoundingClientRect();
      const margin = 8;
      const spaceBelow = window.innerHeight - r.bottom - gap - margin;
      const spaceAbove = r.top - gap - margin;
      // Раскрываем вверх, только если снизу тесно и сверху заметно просторнее.
      const placeAbove = spaceBelow < Math.min(maxHeight, 200) && spaceAbove > spaceBelow;
      const room = Math.max(120, placeAbove ? spaceAbove : spaceBelow);

      const next: CSSProperties = {
        position: 'fixed',
        left: Math.round(r.left),
        maxHeight: Math.min(maxHeight, room),
        zIndex: 1200,
      };
      if (matchWidth) next.width = Math.round(r.width);
      if (placeAbove) next.bottom = Math.round(window.innerHeight - r.top + gap);
      else next.top = Math.round(r.bottom + gap);
      setStyle(next);
    };

    compute();
    window.addEventListener('scroll', compute, true);
    window.addEventListener('resize', compute);
    return () => {
      window.removeEventListener('scroll', compute, true);
      window.removeEventListener('resize', compute);
    };
  }, [open, anchorRef, matchWidth, maxHeight, gap]);

  if (!open) return null;

  // Порталим в узел модалки (если якорь внутри неё), а не в body: Radix Dialog
  // оборачивает контент в react-remove-scroll, который глушит прокрутку колёсиком
  // за пределами модалки — из-за этого длинные списки в body не скроллились.
  // position:fixed всё равно отсчитывается от вьюпорта и не обрезается overflow
  // тела модалки. Вне модалки (таблицы, пагинатор) — обычный body.
  // Radix-поповер тоже помечен role="dialog", но его обёртка позиционируется
  // transform'ом, а transform создаёт containing block для position:fixed —
  // портал внутрь уехал бы ровно на смещение поповера. Такому предку не отдаём:
  // поповер немодальный, react-remove-scroll там нет, body подходит.
  const dialog = anchorRef.current?.closest('[role="dialog"]');
  const container: Element = dialog && !dialog.closest('[data-radix-popper-content-wrapper]')
    ? dialog
    : document.body;

  return createPortal(
    <div ref={floatingRef} className={className} style={style} data-floating-popover>
      {children}
    </div>,
    container,
  );
}
