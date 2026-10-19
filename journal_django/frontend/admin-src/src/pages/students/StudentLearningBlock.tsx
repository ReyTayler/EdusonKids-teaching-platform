import { useMemo, useState, type CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { useStudentStats } from '../../hooks/useStudents';
import { useMemberships, useMembershipMutations } from '../../hooks/useMemberships';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { ScheduledMakeupsBlockModal } from '../../components/memberships/ScheduledMakeupsBlockModal';
import { SelectInput } from '../../components/form/SelectInput';
import { scheduledMakeupsBlockMessage } from '../../lib/api';
import { directionColor } from '../../lib/direction-color';
import { fmtDate, fmtLessons } from '../../lib/format';
import { formatSlot } from '../../lib/slots';
import type { Direction, Group, GroupMembership } from '../../lib/types';
import { BlockLoading } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';

type StatsDirection = NonNullable<ReturnType<typeof useStudentStats>['data']>['directions'][number];
type StatsGroup = StatsDirection['groups'][number];

function pctTone(p: number | null | undefined): string {
  if (p == null) return 'var(--text3)';
  if (p >= 80) return 'var(--green)';
  if (p >= 50) return 'var(--amber)';
  return 'var(--red)';
}

/** Строка группы внутри направления: статистика посещаемости + членство. */
interface GroupRow {
  groupId: number;
  name: string;
  slots: string;
  active: boolean;
  attended: number;
  recorded: number;
  pct: number | null;
  /** Есть активное членство → строку можно убрать прямо отсюда. */
  membership: GroupMembership | null;
}

interface DirBlock {
  id: number;
  name: string;
  color: string;
  stats: StatsDirection | null;
  rows: GroupRow[];
}

interface Props {
  studentId: number;
  groups: Group[];
  directions: Direction[];
}

/**
 * Вкладка «Обучение»: направление — единственный объект, группы живут внутри него.
 *
 * До этого один и тот же объект показывался дважды разными числами: строка группы
 * внутри направления («21 из 24 проведённых, 87 %») и отдельная карточка членства
 * справа («Пройдено 21»). Теперь у группы один набор цифр и тут же действия по ней,
 * а добавление в группу — прежний пикер «выбрал группу → + Добавить».
 */
export default function StudentLearningBlock({ studentId, groups, directions }: Props) {
  const { data: stats, isLoading, error } = useStudentStats(studentId);
  const { data: memberships = [] } = useMemberships({ student_id: studentId });
  const muts = useMembershipMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const [selectedId, setSelectedId] = useState<number | ''>('');
  const [blockMsg, setBlockMsg] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<number | null>(null);

  // Активные группы, где ученика ещё нет: повторная запись в ту же группу
  // вернула бы 409, архивные для записи не предлагаем.
  const availableGroups = useMemo(() => {
    const used = new Set(memberships.map((m) => m.group_id));
    return groups.filter((g) => g.active && !used.has(g.id));
  }, [groups, memberships]);

  const blocks = useMemo<DirBlock[]>(() => {
    const byDir = new Map<number, DirBlock>();

    const ensure = (id: number, name: string, color: string | null): DirBlock => {
      let b = byDir.get(id);
      if (!b) {
        b = { id, name, color: directionColor(color || name), stats: null, rows: [] };
        byDir.set(id, b);
      }
      return b;
    };

    const slotsOf = (groupId: number): string => {
      const g = groups.find((x) => x.id === groupId);
      return (g?.slots || []).map(formatSlot).join(' · ');
    };

    // 1. Направления, по которым есть проведённые уроки — основа блока.
    for (const d of stats?.directions || []) {
      if (d.lessons_recorded === 0) continue;
      const b = ensure(d.direction_id, d.direction_name, d.direction_color);
      b.stats = d;
      for (const g of d.groups as StatsGroup[]) {
        b.rows.push({
          groupId: g.group_id,
          name: g.group_name,
          slots: slotsOf(g.group_id),
          active: g.membership_active,
          attended: g.attended_count,
          recorded: g.lessons_recorded,
          pct: g.attendance_pct,
          membership: memberships.find((m) => m.group_id === g.group_id) || null,
        });
      }
    }

    // 2. Активные членства, которых нет в статистике (только что записали, уроков
    //    ещё не было). Без этого шага свежезаписанный ученик не увидел бы группу
    //    вовсе — и не смог бы её убрать.
    for (const m of memberships) {
      const g = groups.find((x) => x.id === m.group_id);
      if (!g) continue;
      const dir = directions.find((x) => x.id === g.direction_id);
      const b = ensure(g.direction_id, dir?.name || `#${g.direction_id}`, dir?.color || null);
      if (b.rows.some((r) => r.groupId === m.group_id)) continue;
      b.rows.push({
        groupId: m.group_id,
        name: m.group_name || g.name,
        slots: slotsOf(m.group_id),
        active: true,
        attended: 0,
        recorded: 0,
        pct: null,
        membership: m,
      });
    }

    for (const b of byDir.values()) {
      // Активные группы выше архивных.
      b.rows.sort((a, z) => Number(z.active) - Number(a.active));
    }
    return [...byDir.values()];
  }, [stats, memberships, groups, directions]);

  const handleRemove = async (id: number) => {
    setRemovingId(id);
    try {
      await muts.remove.mutateAsync(id);
      toast('Убран из группы', 'ok');
    } catch (err) {
      const msg = scheduledMakeupsBlockMessage(err);
      if (msg) setBlockMsg(msg); else showError(err);
    } finally {
      setRemovingId(null);
    }
  };

  const handleAdd = async () => {
    if (!selectedId) return;
    try {
      await muts.create.mutateAsync({ student_id: studentId, group_id: Number(selectedId) });
      setSelectedId('');
      toast('Добавлен в группу', 'ok');
    } catch (err) { showError(err); }
  };

  const addPicker = (
    <div className="learn__add">
      <SelectInput
        value={selectedId === '' ? '' : String(selectedId)}
        onChange={(e) => setSelectedId(e.target.value === '' ? '' : Number(e.target.value))}
        placeholder="Выберите группу"
        options={availableGroups.map((g) => ({ value: g.id, label: g.name }))}
      />
      <button
        type="button"
        className="btn-secondary"
        onClick={() => { void handleAdd(); }}
        disabled={!selectedId || muts.create.isPending}
      >
        + Добавить
      </button>
    </div>
  );

  if (isLoading) return <BlockLoading rows={4} />;
  if (error) {
    return (
      <EmptyState hint="Обновите страницу — если повторится, сообщите администратору.">
        Не удалось загрузить статистику
      </EmptyState>
    );
  }

  return (
    <div className="learn">
      <div className="learn__head">
        <span className="learn__head-title">Направления и группы</span>
        {addPicker}
      </div>

      {blocks.length === 0 ? (
        <EmptyState hint="Выберите группу в списке выше и нажмите «Добавить».">
          Ученик пока не записан ни в одну группу
        </EmptyState>
      ) : blocks.map((b) => {
        const d = b.stats;
        const pct = d?.attendance_pct ?? null;
        const month = d?.this_month;
        // Доп.уроки сверх плана курса (attended − план): completion зажат на 100%
        // (бэкенд), а «излишек» показываем отдельной пометкой, чтобы урок не потерялся.
        const over = d ? Math.max(d.attended_count - d.denominator, 0) : 0;

        return (
          <section key={b.id} className="learn-dir" style={{ '--dir-color': b.color } as CSSProperties}>
            <div className="learn-dir__head">
              <div className="learn-dir__title">
                <h3 className="learn-dir__name">{b.name}</h3>
                <div className="learn-dir__sub">
                  {d?.course_total_lessons
                    ? `план курса ${d.course_total_lessons} уроков`
                    : `проведено ${fmtLessons(d?.lessons_recorded ?? 0)} уроков`}
                  {d?.last_attended && ` · последнее занятие ${fmtDate(d.last_attended)}`}
                </div>
              </div>
              {pct != null && (
                <div className="learn-dir__pct" style={{ color: pctTone(pct) }}>{pct}%</div>
              )}
            </div>

            {d && (
              <div className="learn-dir__progress">
                <div className="learn-dir__bar">
                  <div
                    className="learn-dir__bar-fill"
                    style={{ width: `${Math.min(pct ?? 0, 100)}%`, background: pctTone(pct) }}
                  />
                </div>
                <div className="learn-dir__counts">
                  <b>{fmtLessons(Math.min(d.attended_count, d.denominator))}</b> / {fmtLessons(d.denominator)}
                  {over > 0 ? (
                    <span className="learn-dir__counts-sub">+{fmtLessons(over)} сверх курса</span>
                  ) : d.denominator !== d.lessons_recorded && (
                    <span className="learn-dir__counts-sub">проведено {fmtLessons(d.lessons_recorded)}</span>
                  )}
                </div>
                <div className="learn-dir__month">
                  {month && month.lessons_recorded > 0
                    ? <>в этом месяце <b>{fmtLessons(month.attended_count)} / {fmtLessons(month.lessons_recorded)}</b></>
                    : 'в этом месяце занятий не было'}
                </div>
              </div>
            )}

            <div className="learn-groups">
              {b.rows.map((r) => (
                <div key={r.groupId} className={`learn-group${r.active ? '' : ' is-archived'}`}>
                  <div className="learn-group__main">
                    <Link to={`/admin/groups/${r.groupId}`} className="entity-link">{r.name}</Link>
                    {!r.active && <span className="archive-tag">Архив</span>}
                  </div>
                  <div className="learn-group__slots">{r.slots || '—'}</div>
                  <div className="learn-group__num">
                    {r.recorded > 0 ? <><b>{fmtLessons(r.attended)}</b> / {fmtLessons(r.recorded)}</> : 'занятий ещё не было'}
                  </div>
                  <div className="learn-group__pct" style={{ color: pctTone(r.pct) }}>
                    {r.pct != null ? `${r.pct}%` : '—'}
                  </div>
                  {r.membership ? (
                    <button
                      type="button"
                      className="learn-group__remove"
                      aria-label={`Убрать из группы ${r.name}`}
                      title="Убрать из группы"
                      disabled={removingId === Number(r.membership.id)}
                      onClick={() => { void handleRemove(Number(r.membership!.id)); }}
                    >×</button>
                  ) : <span className="learn-group__remove-spacer" />}
                  {r.membership?.transferred_from_group_name && (
                    <div className="learn-group__note">
                      Переведён из «{r.membership.transferred_from_group_name}» — там отработано{' '}
                      {String(r.membership.transferred_from_lessons_done)} ур.
                    </div>
                  )}
                </div>
              ))}
            </div>
          </section>
        );
      })}

      {blockMsg && (
        <ScheduledMakeupsBlockModal message={blockMsg} onClose={() => setBlockMsg(null)} />
      )}
    </div>
  );
}
