/**
 * Цвет текста и цвет выделения в редакторе базы знаний.
 *
 * Палитра закрытая и состоит из ССЫЛОК НА ТОКЕНЫ, а не из кодов цвета. Причин
 * две, и обе существенные.
 *
 * Первая — тема. Токены разные в светлой и тёмной: `--danger` это #dc2626 и
 * #f87171 соответственно. Сохрани мы в документе код цвета — красный текст,
 * набранный в светлой теме, в тёмной оказался бы почти нечитаемым, а заливка
 * светлым по светлому просто исчезла бы. Ссылка на токен подставляется при
 * показе и остаётся читаемой в обеих.
 *
 * Вторая — дизайн-система: цвет в проекте закреплён за смыслом, и произвольная
 * раскраска запрещена. Закрытый список семантических цветов — тот же приём, что
 * у видов выноски и у шрифтов: раскрасить можно, но только в то, что уже
 * означает «важно», «хорошо», «внимание».
 *
 * Список обязан совпадать с белым списком сервера (apps/knowledge/content.py:
 * ALLOWED_TEXT_COLORS и ALLOWED_HIGHLIGHT_COLORS). Сторож — тест
 * test_node_lists_in_sync.
 */

export interface ColorChoice {
  /** Значение для документа. Пустая строка — снять цвет. */
  value: string;
  label: string;
}

/** Цвет букв. Все варианты проходят контраст на светлом и тёмном фоне. */
export const TEXT_COLORS: ColorChoice[] = [
  { value: '', label: 'Обычный' },
  { value: 'var(--accent)', label: 'Акцент' },
  { value: 'var(--danger)', label: 'Красный' },
  { value: 'var(--success)', label: 'Зелёный' },
  { value: 'var(--warning)', label: 'Оранжевый' },
  { value: 'var(--info)', label: 'Синий' },
  { value: 'var(--text3)', label: 'Приглушённый' },
];

/**
 * Цвет выделения — только полупрозрачные заливки (…-soft).
 *
 * Плотный фон под текстом означал бы, что цвет букв тоже надо менять, иначе
 * тёмное по тёмному. Полупрозрачная подложка сохраняет читаемость с любым
 * цветом текста и в любой теме.
 */
export const HIGHLIGHT_COLORS: ColorChoice[] = [
  { value: '', label: 'Без выделения' },
  { value: 'var(--warning-soft)', label: 'Жёлтое' },
  { value: 'var(--success-soft)', label: 'Зелёное' },
  { value: 'var(--danger-soft)', label: 'Красное' },
  { value: 'var(--info-soft)', label: 'Синее' },
  { value: 'var(--accent-soft)', label: 'Акцентное' },
];

/**
 * Как сохранённый цвет букв выглядит на экране.
 *
 * Значение в документе и цвет при показе разведены намеренно. Документ
 * хранит то, что принимает сервер (ALLOWED_TEXT_COLORS) и что уже записано в
 * существующих документах, а оттенок задаёт токен статьи. Поэтому оттенок
 * можно поменять одной строкой в tokens.css, не трогая ни сервер, ни документы.
 * Переопределить сам --text3 внутри статьи нельзя: на нём держатся цитаты,
 * выполненные задачи, карточки файлов.
 */
const TEXT_COLOR_DISPLAY: Record<string, string> = {
  'var(--text3)': 'var(--kb-text-muted)',
};

export function displayTextColor(value: string): string {
  return TEXT_COLOR_DISPLAY[value] ?? value;
}

/** Обратное преобразование — для HTML, который редактор нарисовал сам. */
export function storedTextColor(value: string): string {
  const entry = Object.entries(TEXT_COLOR_DISPLAY).find(([, shown]) => shown === value);
  return entry ? entry[0] : value;
}

/** Значения, которые редактор согласен принять из вставленной разметки. */
export const KNOWN_TEXT_COLORS = new Set(TEXT_COLORS.map((c) => c.value).filter(Boolean));
export const KNOWN_HIGHLIGHTS = new Set(HIGHLIGHT_COLORS.map((c) => c.value).filter(Boolean));
