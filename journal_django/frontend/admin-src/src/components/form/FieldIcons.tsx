/**
 * Глифы свойств для строк панели (InlineField).
 *
 * Общего набора иконок в проекте нет — глифы живут рядом с местом
 * использования (см. GearGlyph в TasksPage, CommentGlyph в TaskCard). Эти
 * ходят вместе и нужны уже двум разделам (панель задачи и панель сделки),
 * поэтому лежат в общих, рядом с самим InlineField, а не в pages/tasks/.
 *
 * Один размер и одна толщина линии на весь набор: разнобой в иконках заметнее,
 * чем сами иконки.
 */
interface GlyphProps { size?: number }

const BASE = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.75,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};

function frame(size: number) {
  return { width: size, height: size, viewBox: '0 0 24 24', ...BASE };
}

/** Исполнитель. */
export function PersonGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M4.5 20a7.5 7.5 0 0 1 15 0" />
    </svg>
  );
}

/** Ученик — шапочка выпускника, чтобы не путать с исполнителем. */
export function StudentGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <path d="M12 4 2.5 9 12 14l9.5-5z" />
      <path d="M6.5 11.3V16c0 1.4 2.5 2.6 5.5 2.6s5.5-1.2 5.5-2.6v-4.7" />
    </svg>
  );
}

/** Группа. */
export function GroupGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <circle cx="9" cy="8.5" r="3" />
      <path d="M3 19a6 6 0 0 1 12 0" />
      <path d="M16 6.2a3 3 0 0 1 0 4.6M17.5 14.4A5.6 5.6 0 0 1 21 19" />
    </svg>
  );
}

/** Срок. */
export function CalendarGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <rect x="3" y="5" width="18" height="16" rx="2.5" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </svg>
  );
}

/** Приоритет — столбики разной высоты. */
export function PriorityGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <path d="M6 20v-5M12 20V9M18 20V4" />
    </svg>
  );
}

/** Стадия сделки в панели «Продлений». */
export function TypeGlyph({ size = 15 }: GlyphProps) {
  return (
    <svg {...frame(size)}>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="3.5" />
    </svg>
  );
}
