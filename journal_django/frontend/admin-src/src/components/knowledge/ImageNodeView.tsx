import { useCallback, useEffect, useRef, useState } from 'react';
import { NodeViewWrapper } from '@tiptap/react';
import type { NodeViewProps } from '@tiptap/react';
import { imageUrl } from '../../lib/knowledge';

/**
 * Картинка в редакторе — с изменением размера мышью.
 *
 * Размер хранится в тех же атрибутах width/height, что и раньше. Раньше они
 * означали «размеры оригинала» и служили только резервированию места, но по
 * факту уже управляли показом: `max-width: 100%` ограничивает картинку сверху,
 * а атрибут width задаёт её ширину, пока она в этот потолок укладывается.
 * Поэтому отдельного атрибута для размера не заводим — иначе в документе
 * оказались бы два размера, и пришлось бы решать, какой главнее.
 *
 * Оба атрибута меняются ВМЕСТЕ и по исходному соотношению сторон: если менять
 * только ширину, пара перестаёт описывать одну картинку, и браузер снова начнёт
 * дёргать текст при загрузке — ровно то, ради чего эти атрибуты и заводились.
 */

/** Меньше этого картинка перестаёт быть иллюстрацией. */
const MIN_WIDTH = 120;

/** Шаг изменения ширины с клавиатуры. */
const KEY_STEP = 16;

interface Size {
  width: number;
  height: number;
}

export function ImageNodeView({ node, updateAttributes, selected, editor }: NodeViewProps) {
  const attrs = node.attrs as {
    imageId: number | null; alt: string; width: number | null; height: number | null;
  };
  const imgRef = useRef<HTMLImageElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);

  /**
   * Ширина во время перетаскивания. Держится в состоянии компонента, а НЕ в
   * атрибутах узла: каждое изменение атрибута — транзакция редактора, то есть
   * запись в историю правок и планирование автосохранения. За одно движение
   * мыши таких изменений сотни, и документ уходил бы на сервер всё время, пока
   * тянут за уголок, а Ctrl+Z потом откатывал бы размер по пикселю.
   */
  const [dragWidth, setDragWidth] = useState<number | null>(null);
  /**
   * Та же ширина, но доступная синхронно. Читать её из состояния в момент
   * отпускания нельзя: обработчик замкнул значение начала перетаскивания, а
   * вызывать commit внутри функции обновления состояния — побочный эффект в
   * том месте, где React требует чистоты, и в строгом режиме он выполнится
   * дважды.
   */
  const latestWidth = useRef<number | null>(null);

  const ratio = attrs.width && attrs.height ? attrs.height / attrs.width : null;

  /**
   * Пределы ширины.
   *
   * Верхний — минимум из ширины колонки и той ширины, при которой картинка
   * упирается в потолок по высоте (--kb-image-max-h). Без второго ограничения
   * у вертикального снимка ручка «тянулась», а картинка не росла: высоту
   * зажимал CSS, и ширина пересчитывалась обратно. Тянуть за то, что не
   * двигается, — худший вид отклика.
   */
  const limits = useCallback((): { min: number; max: number } => {
    const container = wrapRef.current?.parentElement?.clientWidth
      ?? wrapRef.current?.clientWidth
      ?? 0;
    let max = container || Number.MAX_SAFE_INTEGER;

    const img = imgRef.current;
    const frame = img?.parentElement;
    if (img && frame) {
      // Потолок берём с рамки (--kb-image-cap), а НЕ из max-height картинки:
      // у картинки его больше нет, иначе зажатая высота при заданной ширине
      // растягивала бы изображение.
      const cap = Number.parseFloat(
        getComputedStyle(frame).getPropertyValue('--kb-image-cap'),
      );
      const natural = img.naturalWidth && img.naturalHeight
        ? img.naturalHeight / img.naturalWidth
        : ratio;
      if (Number.isFinite(cap) && natural) {
        max = Math.min(max, Math.round(cap / natural));
      }
    }
    return { min: MIN_WIDTH, max: Math.max(MIN_WIDTH, max) };
  }, [ratio]);

  /** Записать размер в документ — один раз, по окончании изменения. */
  const commit = useCallback((width: number) => {
    const img = imgRef.current;
    const natural = img?.naturalWidth && img.naturalHeight
      ? img.naturalHeight / img.naturalWidth
      : ratio;
    const next: Size = {
      width,
      height: natural ? Math.max(1, Math.round(width * natural)) : (attrs.height ?? width),
    };
    updateAttributes(next);
  }, [ratio, attrs.height, updateAttributes]);

  const startDrag = (event: React.PointerEvent, side: 'left' | 'right') => {
    event.preventDefault();
    event.stopPropagation();
    const handle = event.currentTarget as HTMLElement;
    handle.setPointerCapture(event.pointerId);

    const startX = event.clientX;
    const startWidth = imgRef.current?.clientWidth ?? attrs.width ?? MIN_WIDTH;
    const { min, max } = limits();

    latestWidth.current = null;

    const move = (moveEvent: PointerEvent) => {
      // Картинка стоит по центру колонки, поэтому чтобы её край шёл ЗА
      // курсором, ширина обязана меняться на удвоенное смещение: половина
      // уходит влево, половина вправо.
      const delta = (moveEvent.clientX - startX) * (side === 'right' ? 2 : -2);
      const next = Math.round(Math.min(max, Math.max(min, startWidth + delta)));
      latestWidth.current = next;
      setDragWidth(next);
    };

    const finish = () => {
      // Захват мог быть снят системой (pointercancel) — тогда освобождать
      // нечего, а вызов на снятом захвате бросает исключение.
      if (handle.hasPointerCapture(event.pointerId)) {
        handle.releasePointerCapture(event.pointerId);
      }
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', finish);
      handle.removeEventListener('pointercancel', finish);
      if (latestWidth.current !== null) commit(latestWidth.current);
      latestWidth.current = null;
      setDragWidth(null);
    };

    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
  };

  /** Стрелками — то же самое, но без мыши: перетаскивание клавиатуре недоступно. */
  const onKeyDown = (event: React.KeyboardEvent) => {
    const step = event.key === 'ArrowRight' ? KEY_STEP
      : event.key === 'ArrowLeft' ? -KEY_STEP
        : 0;
    if (step === 0) return;
    event.preventDefault();
    const { min, max } = limits();
    const current = imgRef.current?.clientWidth ?? attrs.width ?? MIN_WIDTH;
    commit(Math.round(Math.min(max, Math.max(min, current + step))));
  };

  // Пока тянут, курсор «изменение ширины» должен держаться на всей странице —
  // иначе он мигает, стоит увести мышь с самой ручки.
  useEffect(() => {
    if (dragWidth === null) return;
    document.body.classList.add('kb-resizing');
    return () => document.body.classList.remove('kb-resizing');
  }, [dragWidth]);

  const shown = dragWidth ?? attrs.width ?? undefined;
  const editable = editor.isEditable;

  return (
    <NodeViewWrapper
      ref={wrapRef}
      className={`kb-image-block${selected ? ' is-selected' : ''}${dragWidth !== null ? ' is-resizing' : ''}`}
    >
      {/* Ширина живёт ТОЛЬКО здесь. Раньше она стояла и на рамке, и атрибутом
          картинки, а во время перетаскивания навязывалась ещё и высота. Стоило
          потолку высоты из CSS зажать её, как ширина оставалась навязанной, и
          картинка растягивалась поперёк — вертикальный постер превращался в
          горизонтальный. Один источник размера делает это невозможным. */}
      <span className="kb-image-block__frame" style={shown ? { width: shown } : undefined}>
        <img
          ref={imgRef}
          src={attrs.imageId ? imageUrl(attrs.imageId) : ''}
          alt={attrs.alt || ''}
          // Атрибуты — исходные размеры и только: из них браузер берёт
          // пропорции, пока файл не загрузился. Показом управляет CSS
          // (width: 100% рамки, height: auto), поэтому исказить картинку
          // нельзя ни в какой момент, включая перетаскивание.
          width={attrs.width ?? undefined}
          height={attrs.height ?? undefined}
          draggable={false}
        />
        {editable && (['left', 'right'] as const).map((side) => (
          <button
            key={side}
            type="button"
            className={`kb-image-block__handle kb-image-block__handle--${side}`}
            aria-label={`Изменить ширину картинки${attrs.alt ? `: ${attrs.alt}` : ''}`}
            draggable={false}
            onDragStart={(event) => event.preventDefault()}
            onPointerDown={(event) => startDrag(event, side)}
            onKeyDown={onKeyDown}
          />
        ))}
        {dragWidth !== null && <span className="kb-image-block__size">{dragWidth} px</span>}
      </span>
    </NodeViewWrapper>
  );
}
