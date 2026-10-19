/**
 * Предмет урока и его цвет.
 *
 * Точный источник направления — GET /api/group-directions (карта группа →
 * направление+цвет) и поля direction/directionColor в GET /api/lessons, см.
 * resolveDirectionColor(). /api/report направление явно не отдаёт (контракт
 * заморожен), поэтому там цвет резолвится через карту направлений по имени
 * группы (см. CalendarPage/ReportPage). subjectOf/subjectColor — эвристика
 * по названию группы, остаётся ПОСЛЕДНИМ фолбэком, когда группы нет в карте
 * направлений (например, ещё не подгрузилась или устарела).
 */
import type { ReportLesson } from './types';

export type SubjectKey = 'python' | 'scratch' | 'vibe' | 'group';

export interface SubjectDef {
  key: SubjectKey;
  label: string;
  /** CSS-переменная цвета (из tokens.css). */
  colorVar: string;
}

export const SUBJECTS: SubjectDef[] = [
  { key: 'scratch', label: 'Scratch', colorVar: 'var(--subject-scratch)' },
  { key: 'python', label: 'Python', colorVar: 'var(--subject-python)' },
  { key: 'vibe', label: 'Вайб-кодинг', colorVar: 'var(--subject-vibe)' },
  { key: 'group', label: 'Группа', colorVar: 'var(--subject-group)' },
];

const SUBJECT_BY_KEY: Record<SubjectKey, SubjectDef> = Object.fromEntries(
  SUBJECTS.map((s) => [s.key, s]),
) as Record<SubjectKey, SubjectDef>;

/** Эвристика: ключ предмета по названию группы + признаку группы. */
export function subjectOf(lesson: Pick<ReportLesson, 'group' | 'isGroup'>): SubjectKey {
  const n = (lesson.group || '').toLowerCase();
  if (/(вайб|vibe|vibe-?coding|вайб-?кодинг)/.test(n)) return 'vibe';
  if (/(python|пайтон|питон)/.test(n)) return 'python';
  if (/(scratch|скретч|скрэтч)/.test(n)) return 'scratch';
  if (lesson.isGroup) return 'group';
  return 'python'; // дефолт — флагманское направление (RISD Blue / accent)
}

/** CSS-переменная цвета предмета урока. */
export function subjectColor(lesson: Pick<ReportLesson, 'group' | 'isGroup'>): string {
  return SUBJECT_BY_KEY[subjectOf(lesson)].colorVar;
}

/** Нейтральный цвет для уроков без направления (правило см. в CalendarPage — «Без направления»). */
export const NO_DIRECTION_COLOR = 'var(--text3)';

const HEX_COLOR_RE = /^#[0-9a-fA-F]{6}$/;

/**
 * Точный цвет направления по правилу admin-src/src/lib/direction-color.ts:
 * валидный hex из БД → сам цвет; иначе — детерминированный hue по имени
 * направления (сумма кодов символов % 360, hsl 55%/42%). Если и цвета,
 * и имени нет — нейтральный фолбэк NO_DIRECTION_COLOR.
 */
export function resolveDirectionColor(
  color: string | null | undefined,
  name: string | null | undefined,
): string {
  if (color && HEX_COLOR_RE.test(color)) return color;
  if (name) {
    const hue = [...name].reduce((sum, ch) => sum + ch.charCodeAt(0), 0) % 360;
    return `hsl(${hue}, 55%, 42%)`;
  }
  return NO_DIRECTION_COLOR;
}
