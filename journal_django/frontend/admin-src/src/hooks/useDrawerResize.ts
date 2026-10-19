import {
  useCallback, useEffect, useRef, useState, type KeyboardEvent, type MouseEvent,
} from 'react';

/** Общая механика ресайза боковых панелей (TaskDrawer, RenewalDrawer — задача
 * 2026-08-26). Раньше жила по копии в каждой панели; вынесена сюда, потому что
 * дублирование двух независимых обработчиков mousemove/mouseup гарантированно
 * разъехалось бы при следующей правке одного из них. */

const DRAWER_WIDTH_MIN = 360;
const DRAWER_WIDTH_STEP = 24;

/** Верхняя граница — не шире 900px и не шире 90% окна, нижняя всегда MIN,
 * даже если окно уже настолько узкое, что 90% меньше MIN (на мобильных сама
 * ширина в CSS не применяется, но JS-состояние не должно ловить NaN/инверсию). */
function clampDrawerWidth(width: number): number {
  const upper = Math.max(DRAWER_WIDTH_MIN, Math.min(900, window.innerWidth * 0.9));
  return Math.min(Math.max(width, DRAWER_WIDTH_MIN), upper);
}

/** localStorage бросает исключение в приватном режиме/при отключённых данных
 * сайта — без try/catch панель попросту не открылась бы. Мусор или отсутствие
 * значения — молча берём дефолт; прочитанное значение всё равно прогоняем
 * через clampDrawerWidth, потому что окно могло стать уже с прошлого раза. */
function readStoredWidth(key: string, fallback: number): number {
  try {
    const raw = window.localStorage.getItem(key);
    const parsed = raw ? Number(raw) : NaN;
    if (!Number.isFinite(parsed)) return fallback;
    return clampDrawerWidth(parsed);
  } catch {
    return fallback;
  }
}

function writeStoredWidth(key: string, width: number): void {
  try {
    window.localStorage.setItem(key, String(Math.round(width)));
  } catch {
    // приватный режим / отключённые данные сайта — не критично, просто не запомнится
  }
}

export interface UseDrawerResizeOptions {
  /** Ключ localStorage — у каждой панели свой (taskboard.drawerWidth / renewals.drawerWidth). */
  storageKey: string;
  /** Ширина по умолчанию, если в хранилище ничего нет. */
  defaultWidth?: number;
}

export interface UseDrawerResizeResult {
  /** Текущая ширина панели — прокинуть в инлайновый style: `{ width }`. */
  width: number;
  /** Идёт перетаскивание ручки прямо сейчас (для DrawerResizeHandle). */
  resizing: boolean;
  /** Обработчики для DrawerResizeHandle. */
  handleProps: {
    onMouseDown: (e: MouseEvent) => void;
    onKeyDown: (e: KeyboardEvent) => void;
  };
  /** Оборачивает onClose оверлея: гасит закрытие, пока идёт ресайз и сразу
   * после отпускания мыши — тот же mouseup, что завершает ресайз, доходит до
   * оверлея как клик, и без подавления панель закрывалась бы при отпускании
   * мыши над затемнением. */
  wrapOverlayClose: (onClose: () => void) => () => void;
}

export function useDrawerResize({
  storageKey,
  defaultWidth = 440,
}: UseDrawerResizeOptions): UseDrawerResizeResult {
  const [width, setWidth] = useState<number>(() => readStoredWidth(storageKey, defaultWidth));
  // ref — читать «свежее» значение в обработчиках window-событий без
  // пересоздания подписки на каждый пиксель перетаскивания.
  const widthRef = useRef(width);
  const [resizing, setResizing] = useState(false);
  const suppressOverlayCloseRef = useRef(false);

  const applyWidth = useCallback((next: number, persist: boolean) => {
    const clamped = clampDrawerWidth(next);
    widthRef.current = clamped;
    setWidth(clamped);
    if (persist) writeStoredWidth(storageKey, clamped);
  }, [storageKey]);

  useEffect(() => {
    if (!resizing) return undefined;
    const prevUserSelect = document.body.style.userSelect;
    const prevCursor = document.body.style.cursor;
    document.body.style.userSelect = 'none';
    document.body.style.cursor = 'col-resize';

    const handleMove = (e: globalThis.MouseEvent) => {
      applyWidth(window.innerWidth - e.clientX, false);
    };
    const handleUp = () => {
      setResizing(false);
      writeStoredWidth(storageKey, widthRef.current);
      suppressOverlayCloseRef.current = true;
      window.setTimeout(() => { suppressOverlayCloseRef.current = false; }, 0);
    };

    window.addEventListener('mousemove', handleMove);
    window.addEventListener('mouseup', handleUp);
    return () => {
      document.body.style.userSelect = prevUserSelect;
      document.body.style.cursor = prevCursor;
      window.removeEventListener('mousemove', handleMove);
      window.removeEventListener('mouseup', handleUp);
    };
  }, [resizing, applyWidth, storageKey]);

  const onMouseDown = useCallback((e: MouseEvent) => {
    e.preventDefault();
    setResizing(true);
  }, []);

  const onKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      e.preventDefault();
      applyWidth(widthRef.current + DRAWER_WIDTH_STEP, true);
    } else if (e.key === 'ArrowRight') {
      e.preventDefault();
      applyWidth(widthRef.current - DRAWER_WIDTH_STEP, true);
    }
  }, [applyWidth]);

  const wrapOverlayClose = useCallback((onClose: () => void) => () => {
    if (resizing || suppressOverlayCloseRef.current) return;
    onClose();
  }, [resizing]);

  return {
    width,
    resizing,
    handleProps: { onMouseDown, onKeyDown },
    wrapOverlayClose,
  };
}
