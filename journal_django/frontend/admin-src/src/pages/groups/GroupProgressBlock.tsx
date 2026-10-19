import { GroupProgressView } from '../../shared/progress/GroupProgressView';
import { useGroupProgress } from '../../hooks/useGroupProgress';
import { BlockLoading } from '../../components/ui/Skeleton';

/**
 * Вкладка «Прогресс»: обзорная матрица посещаемости группы. Данные — GET
 * /api/admin/groups/:id/progress (вся матрица разом, без N+1); рендер —
 * общий GroupProgressView (shared/progress, используется и teacher SPA).
 */
export default function GroupProgressBlock({ groupId }: { groupId: number }) {
  const { data, isLoading, isError } = useGroupProgress(groupId);

  if (isLoading) return <BlockLoading rows={4} label="Загружаем прогресс…" />;
  if (isError) return <div className="memberships__empty">Не удалось загрузить прогресс группы.</div>;
  if (!data) return null;

  if (data.students.length === 0) {
    return <div className="memberships__empty">В группе пока нет учеников.</div>;
  }

  return <GroupProgressView data={data} />;
}
