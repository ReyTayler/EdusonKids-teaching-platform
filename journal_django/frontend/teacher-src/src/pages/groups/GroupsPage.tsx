import { useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTeacherData } from '../../hooks/useTeacherData';
import { useGroupDirections } from '../../hooks/useGroupDirections';
import { subjectColor, resolveDirectionColor } from '../../lib/subjects';
import { GroupCard } from './GroupCard';
import { PageHead } from '../../components/ui/PageHead';
import type { GroupData } from '../../lib/types';

/**
 * Мои группы — сетка карточек групп/индивов; клик по карточке открывает
 * страницу группы (/groups/:group — ученики + прогресс).
 *
 * Вкладка «Замена» удалена: замену назначает админ через «Сменить
 * преподавателя» (admin SPA), назначенный урок появляется в календаре
 * заменщика и отмечается оттуда как обычный.
 */
export default function GroupsPage() {
  const navigate = useNavigate();
  const mineQuery = useTeacherData();
  const { data: dirData } = useGroupDirections();
  const dirMap = useMemo(() => dirData?.groups ?? {}, [dirData]);

  /** Точный цвет направления по карте; фолбэк — эвристика по имени группы. */
  const colorOf = useCallback((name: string, data: GroupData): string => {
    const dir = dirMap[name];
    if (dir) return resolveDirectionColor(dir.color, dir.direction ?? name);
    return subjectColor({ group: name, isGroup: data.isGroup });
  }, [dirMap]);

  return (
    <div className="grp-page">
      <PageHead title="Мои группы" />

      {mineQuery.isLoading ? (
        <div className="cal-skel" />
      ) : mineQuery.isError ? (
        <div className="cal-error">Не удалось загрузить группы. Попробуйте обновить страницу.</div>
      ) : Object.keys(mineQuery.data?.data ?? {}).length === 0 ? (
        <div className="cal-empty">Группы не найдены.</div>
      ) : (
        <div className="grp-grid">
          {Object.entries(mineQuery.data!.data).map(([name, data]) => (
            <GroupCard
              key={name}
              name={name}
              data={data}
              color={colorOf(name, data)}
              limit={dirMap[name]?.totalLessons}
              onOpen={() => navigate(`/groups/${encodeURIComponent(name)}`)}
            />
          ))}
        </div>
      )}
    </div>
  );
}
