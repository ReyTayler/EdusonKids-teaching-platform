import { nameColor } from './direction-color';

/** Заливка шапки стадии и цвет подписи на ней. */
export interface StageTone {
  bg: string;
  ink: string;
}

const WHITE = '#ffffff';
const DARK = '#16161a';

// Пороги относительной яркости (WCAG). Ниже LIGHT_TEXT_MAX белый текст даёт
// контраст не хуже 4.5:1, выше DARK_TEXT_MIN — тёмный. Между ними не проходит
// ни один из двух, поэтому заливка притемняется.
const LIGHT_TEXT_MAX = 0.183;
const DARK_TEXT_MIN = 0.211;

const HEX_RE = /^#[0-9a-fA-F]{6}$/;

function channels(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function relativeLuminance(hex: string): number {
  const [r, g, b] = channels(hex).map((c) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function darken(hex: string, factor: number): string {
  const parts = channels(hex)
    .map((c) => Math.round(c * factor).toString(16).padStart(2, '0'));
  return `#${parts.join('')}`;
}

/**
 * Тон шапки стадии.
 *
 * Цвет стадии задаёт суперадмин и может выбрать светло-жёлтый — белая подпись
 * на нём нечитаема. Поэтому цвет текста выводится из яркости заливки, а в узкой
 * полосе, где не проходит ни белый, ни тёмный, заливка притемняется на 20%:
 * яркость падает примерно вдвое и белый текст снова держит 4.5:1.
 *
 * Стадия без своего цвета получает детерминированный тон из названия — тот же
 * приём, что у направлений и аватаров. Серая заглушка отвергнута сознательно:
 * доска из восьми одинаковых серых плашек хуже, чем разноцветная.
 */
export function stageTone(color: string | null | undefined, label: string): StageTone {
  if (!color || !HEX_RE.test(color)) {
    // nameColor отдаёт hsl со светлотой 42% — он всегда тёмный, белый текст подходит.
    return { bg: nameColor(label), ink: WHITE };
  }
  const luminance = relativeLuminance(color);
  if (luminance >= DARK_TEXT_MIN) return { bg: color, ink: DARK };
  if (luminance <= LIGHT_TEXT_MAX) return { bg: color, ink: WHITE };
  return { bg: darken(color, 0.8), ink: WHITE };
}

/**
 * Готовые цвета стадии для выбора человеком.
 *
 * Набор, а не свободная палитра `<input type="color">`: доска складывается из
 * восьми плашек рядом, и произвольные оттенки быстро превращают её в мешанину.
 * Значения подобраны так, чтобы stageTone() выдал по ним читаемую подпись без
 * притемнения — то есть каждый попадает в «белый» или «тёмный» диапазон
 * яркости, а не в узкую полосу между ними.
 */
/**
 * Фиксированный набор цветов стадии.
 *
 * Заменил произвольный ввод hex: цвет стадии видит вся школа, а свободный выбор
 * давал грязные и неотличимые друг от друга оттенки на доске. Каждый цвет здесь
 * проверен stageTone() — для него находится читаемый цвет подписи.
 */
export const STAGE_PALETTE = [
  // Белый — обычный цвет набора, а не «отсутствие цвета»: пустой кружок в
  // палитре читался как белая колонка, а давал тон по названию.
  '#FFFFFF',
  '#5B8DEF', '#6C5CE7', '#A55EEA', '#D63FC4',
  '#F368A0', '#F55C5C', '#22C1C3', '#2BD9A8',
  '#2FB85F', '#8CD44A', '#F5C542', '#F5A340',
] as const;
