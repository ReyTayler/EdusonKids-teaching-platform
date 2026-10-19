/**
 * Шрифты, доступные в редакторе базы знаний.
 *
 * Список закрытый и обязан совпадать с ALLOWED_FONTS в
 * apps/knowledge/content.py: значение уходит в style="font-family: …", и
 * сервер не примет строку, которой нет в его списке. Разошлись — пользователь
 * выберет шрифт, а сохранение упадёт с «Недопустимый шрифт».
 */
export interface FontChoice {
  /** Значение для CSS и для сохранения в документе. */
  value: string;
  /** Подпись в выпадающем списке. */
  label: string;
}

export const FONT_CHOICES: FontChoice[] = [
  { value: 'Inter, sans-serif', label: 'Inter' },
  { value: 'Arial, Helvetica, sans-serif', label: 'Arial' },
  { value: 'Verdana, Geneva, sans-serif', label: 'Verdana' },
  { value: 'Georgia, serif', label: 'Georgia' },
  { value: 'Times New Roman, Times, serif', label: 'Times New Roman' },
  { value: 'ui-monospace, SFMono-Regular, Menlo, monospace', label: 'Моноширинный' },
];

/** Подпись «шрифт как у документа» — когда явный шрифт не выбран. */
export const DEFAULT_FONT_LABEL = 'Шрифт по умолчанию';
