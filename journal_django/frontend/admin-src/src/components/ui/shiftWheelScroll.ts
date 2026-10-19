/**
 * Горизонтальная прокрутка Shift+колесом внутри модалки.
 *
 * Radix Dialog запирает прокрутку страницы через react-remove-scroll. Тот решает,
 * по какой оси крутит колесо, только по величине дельт (|deltaX| > |deltaY|) и на
 * Shift не смотрит. Chromium на Windows при Shift+колесе шлёт deltaY и сам
 * превращает его в горизонтальный скролл — библиотека же видит «вертикаль», не
 * находит вертикально прокручиваемого блока и гасит событие. В Firefox Shift даёт
 * deltaX, поэтому там всё работало. Исправления выше по течению нет:
 * react-remove-scroll 2.7.2 — последняя версия на 2026-09-15.
 *
 * Обработчик делает то, что сделал бы браузер без замка: находит под курсором
 * ближайший блок, которому есть куда сдвинуться по горизонтали, и двигает его
 * сам. Нативное действие гасится, чтобы на длинных таблицах (где библиотека
 * событие пропускает) не получилось двойного сдвига.
 */

/** Высота «строки» для колёс, которые шлют дельту в строках, а не в пикселях. */
const LINE_PX = 16;

function toPixels(e: WheelEvent, delta: number, el: HTMLElement): number {
  if (e.deltaMode === WheelEvent.DOM_DELTA_LINE) return delta * LINE_PX;
  if (e.deltaMode === WheelEvent.DOM_DELTA_PAGE) return delta * el.clientWidth;
  return delta;
}

/** Может ли блок сдвинуться по горизонтали в сторону delta прямо сейчас. */
function canScrollX(el: HTMLElement, delta: number): boolean {
  if (el.scrollWidth <= el.clientWidth) return false;
  const { overflowX } = getComputedStyle(el);
  if (overflowX !== 'auto' && overflowX !== 'scroll') return false;
  // Допуск в пиксель — дробный масштаб страницы не даёт ровных scrollLeft.
  return delta > 0
    ? el.scrollLeft + el.clientWidth < el.scrollWidth - 1
    : el.scrollLeft > 0;
}

/**
 * Обработчик wheel для контейнера модалки. Вешать нативно и НЕпассивно:
 * слушатели колеса в React пассивные, из них нельзя погасить нативный скролл.
 */
export function handleShiftWheel(e: WheelEvent, host: HTMLElement): void {
  // Только Shift+колесо, которое браузер сам не перевёл в deltaX (Chromium).
  // Ctrl+колесо — масштаб страницы, его не трогаем.
  if (!e.shiftKey || e.ctrlKey || Math.abs(e.deltaX) >= Math.abs(e.deltaY)) return;

  for (let node = e.target instanceof Element ? e.target : null; node; node = node.parentElement) {
    if (node instanceof HTMLElement) {
      const delta = toPixels(e, e.deltaY, node);
      if (canScrollX(node, delta)) {
        e.preventDefault();
        node.scrollLeft += delta;
        return;
      }
    }
    // Выше контейнера модалки не поднимаемся: страница под модалкой заперта.
    if (node === host) break;
  }
}
