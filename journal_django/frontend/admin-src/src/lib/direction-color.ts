import type { Direction } from './types';

const FALLBACK = '#0d9488';

export function directionColor(input: Direction | string | null | undefined): string {
  if (!input) return FALLBACK;
  if (typeof input === 'object') {
    if (input.color && /^#[0-9a-fA-F]{6}$/.test(input.color)) return input.color;
    return nameColor(input.name || '');
  }
  if (/^#[0-9a-fA-F]{6}$/.test(input)) return input;
  return nameColor(input);
}

/**
 * Тон из имени — детерминированный, без хранения цвета в БД.
 *
 * Экспортируется отдельно от nameColor, потому что Avatar строит из одного тона
 * три цвета (подложка, рамка, текст) с разной светлотой. Вторая копия формулы
 * дала бы разный цвет у аватара и монограммы одного и того же человека.
 */
export function hueOfName(name: string): number {
  return [...String(name || '')].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
}

/** Насыщенный цвет сущности, у которой нет своего поля цвета (преподаватель). */
export function nameColor(name: string): string {
  return `hsl(${hueOfName(name)}, 55%, 42%)`;
}
