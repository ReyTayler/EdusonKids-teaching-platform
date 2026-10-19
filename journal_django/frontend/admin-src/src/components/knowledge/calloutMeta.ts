/**
 * Виды выноски — один список на редактор, читалку и панель инструментов.
 *
 * Значения обязаны совпадать с белым списком сервера
 * (apps/knowledge/content.py: ALLOWED_CALLOUT_TONES). Разойдись они — выноска
 * либо не сохранится, либо сохранится и не покажется.
 */
export const CALLOUT_TONES = ['info', 'tip', 'important', 'warning', 'error'] as const;

export type CalloutTone = (typeof CALLOUT_TONES)[number];

export const CALLOUT_LABELS: Record<CalloutTone, string> = {
  info: 'Информация',
  tip: 'Совет',
  important: 'Важно',
  warning: 'Предупреждение',
  error: 'Ошибка',
};

export const DEFAULT_CALLOUT_TONE: CalloutTone = 'info';

export function isCalloutTone(value: unknown): value is CalloutTone {
  return typeof value === 'string' && (CALLOUT_TONES as readonly string[]).includes(value);
}
