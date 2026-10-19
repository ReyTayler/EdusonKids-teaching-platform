import { usePendingExtraLessonsCount } from '../../hooks/useExtraLessons';

/**
 * Счётчик необработанных пропусков для пункта «Доп.уроки».
 * В свёрнутой группе он поднимается на строку группы (см. NavGroup), чтобы
 * оставаться на виду.
 */
export function ExtraLessonsBadge() {
  const { data } = usePendingExtraLessonsCount();
  const count = data?.count ?? 0;
  if (count <= 0) return null;
  const title = `Необработанных пропусков: ${count}`;
  return (
    <span className="nav-badge" title={title}>
      {count > 99 ? '99+' : count}
    </span>
  );
}
