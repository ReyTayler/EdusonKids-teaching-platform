import { useMemo } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useTeacherData } from '../../hooks/useTeacherData';
import { useGroupDirections } from '../../hooks/useGroupDirections';
import { useGroupProgress } from '../../hooks/useGroupProgress';
import { GroupProgressView } from '@shared/shared/progress/GroupProgressView';
import { subjectColor, resolveDirectionColor } from '../../lib/subjects';
import type { TStudent } from '../../lib/types';
import { ageLabel } from '../../lib/dates';
import { PageHead } from '../../components/ui/PageHead';

/** Остаток оплаченных уроков: ≤0 — долг (красный), 1–2 — скоро закончатся (жёлтый). */
function remainingTone(remaining: number): 'danger' | 'warn' | 'ok' {
  if (remaining <= 0) return 'danger';
  if (remaining <= 2) return 'warn';
  return 'ok';
}

/** Тон остатка → цвет бейджа (рецепт .chip: ok / warn / bad). */
const TONE_CHIP = { ok: 'ok', warn: 'warn', danger: 'bad' } as const;

/**
 * paymentGuard=false — школа не ведёт оплаты в платформе: остаток у всех <= 0 и
 * ничего не значит, поэтому бейдж остатка не показываем вовсе (иначе «Долг» у всех).
 */
function StudentRow({ student, paymentGuard }: { student: TStudent; paymentGuard: boolean }) {
  const remaining = student.remaining ?? 0;
  const tone = remainingTone(remaining);
  return (
    <div className="grp-student">
      <div className="grp-student-main">
        <span className="grp-student-name">{student.name}</span>
<span className="grp-student-age">{ageLabel(student.birthDate) || 'возраст не указан'}</span>
      </div>
      {paymentGuard && (
        <span className={`chip grp-student-balance ${TONE_CHIP[tone]}`}>
          {remaining <= 0
            ? `Долг · остаток ${remaining}`
            : `Осталось ${remaining} опл. ур.`}
        </span>
      )}
    </div>
  );
}

/**
 * Страница группы (/groups/:group): краткая инфа по каждому ученику (имя,
 * возраст, остаток оплаченных уроков из /api/getData) + матрица прогресса
 * (GET /api/group-progress, общий GroupProgressView с admin SPA).
 *
 * Для чужой группы (замена, назначенная админом) /api/getData состав не
 * отдаёт — показываем только прогресс (доступ гейтит сервер).
 */
export default function GroupDetailPage() {
  const navigate = useNavigate();
  const { group = '' } = useParams();

  const mineQuery = useTeacherData();
  const { data: dirData } = useGroupDirections();
  const progress = useGroupProgress(group);

  const groupData = (mineQuery.data?.data ?? {})[group] ?? null;
  const dir = dirData?.groups[group];

  const color = useMemo(() => {
    if (dir) return resolveDirectionColor(dir.color, dir.direction ?? group);
    return subjectColor({ group, isGroup: groupData?.isGroup ?? true });
  }, [dir, group, groupData]);

  const notFound = !mineQuery.isLoading && !groupData && progress.isError;

  return (
    <div className="grp-page">
      <PageHead
        lead={
          <button type="button" className="btn ghost sm grp-back" onClick={() => navigate('/groups')}>
            ← Мои группы
          </button>
        }
        title={
          <span className="grp-detail-name" style={{ ['--subject-color' as any]: color }}>
            <span className="t-dot" aria-hidden="true" />
            <span className="grp-detail-code">{group}</span>
          </span>
        }
        sub={
          <span className="grp-meta-row">
            {dir?.direction && <span className="grp-type">{dir.direction}</span>}
            {groupData && (
              <>
                <span className="grp-type">{groupData.isGroup ? 'Группа' : 'Индивидуально'}</span>
                <span className="grp-count">{groupData.students.length} уч.</span>
              </>
            )}
          </span>
        }
      />

      {notFound ? (
        <div className="cal-error">Группа не найдена или недоступна.</div>
      ) : (
        <>
          {groupData && (
            <section className="grp-detail-sec">
              <div className="t-sec-label">Ученики · {groupData.students.length}</div>
              {groupData.students.length === 0 ? (
                <div className="cal-empty">В группе пока нет учеников.</div>
              ) : (
                <div className="grp-students">
                  {groupData.students.map((s) => (
                    <StudentRow key={s.id} student={s} paymentGuard={groupData.paymentGuard} />
                  ))}
                </div>
              )}
            </section>
          )}

          <section className="grp-detail-sec">
            <div className="t-sec-label">Прогресс</div>
            {progress.isLoading ? (
              <div className="cal-skel" />
            ) : progress.isError ? (
              <div className="cal-error">Не удалось загрузить прогресс группы.</div>
            ) : !progress.data || progress.data.students.length === 0 ? (
              <div className="cal-empty">В группе пока нет учеников.</div>
            ) : (
              <GroupProgressView data={progress.data} />
            )}
          </section>
        </>
      )}
    </div>
  );
}
