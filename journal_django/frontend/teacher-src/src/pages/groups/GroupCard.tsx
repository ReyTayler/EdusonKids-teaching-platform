import { getCourseLimit } from '../../lib/teacher-calc';
import type { GroupData } from '../../lib/types';

/** Карточка группы/индива в «Моих группах» — кликабельна, открывает страницу группы. */
export function GroupCard({
  name,
  data,
  color,
  limit: limitProp,
  onOpen,
}: {
  name: string;
  data: GroupData;
  /** Точный цвет направления (карта useGroupDirections) с фолбэком на эвристику — резолвится в GroupsPage. */
  color: string;
  /** Лимит курса (totalLessons из /api/group-directions) — резолвится в GroupsPage. Фолбэк — эвристика по имени, если не передан. */
  limit?: number | null;
  onOpen: () => void;
}) {
  const limit = limitProp !== undefined ? limitProp : getCourseLimit(name);
  // «Долг» — по ЛЮБОМУ ученику с исчерпанным остатком (не только первому). Без
  // гарда оплат (школа не ведёт оплаты в платформе) остаток <= 0 у всех — не долг.
  const debtors = data.paymentGuard
    ? data.students.filter((s) => (s.remaining ?? 0) <= 0).length
    : 0;
  const pct = limit ? Math.min(100, Math.round((data.lessonsDone / limit) * 100)) : null;

  return (
    <div
      className="grp-card grp-card--link"
      style={{ ['--subject-color' as any]: color }}
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => { if (e.key === 'Enter') onOpen(); }}
    >
      <div className="grp-card-head">
        <span className="grp-name"><span className="t-dot" aria-hidden="true" />{name}</span>
        {data.students.length > 0 && debtors > 0 && (
          <span className="chip bad">Долг · {debtors}</span>
        )}
      </div>

      <div className="grp-meta-row">
        <span className="grp-type">{data.isGroup ? 'Группа' : 'Индивидуально'}</span>
        <span className="grp-count">{data.students.length} уч.</span>
      </div>

      <div className="grp-progress">
        {pct !== null ? (
          <>
            <div className="grp-progress-track">
              <div className="grp-progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <div className="grp-progress-label">{data.lessonsDone} / {limit} уроков</div>
          </>
        ) : (
          <div className="grp-progress-label">{data.lessonsDone} уроков проведено</div>
        )}
      </div>
    </div>
  );
}
