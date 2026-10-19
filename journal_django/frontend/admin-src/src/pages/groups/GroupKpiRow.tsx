import { useMemo, type CSSProperties } from 'react';
import { useGroupPlan } from '../../hooks/useGroupPlanCalendar';
import { useMemberships } from '../../hooks/useMemberships';
import { StatTiles, type StatTile } from '../../components/detail/StatTiles';
import { todayMSK } from '../../lib/format';
import { DOW } from '../../lib/slots';

const MONTH_SHORT = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'];

/** 'YYYY-MM-DD' → '20 июл'. */
function shortDate(iso: string): string {
  const [, m, d] = iso.split('-').map(Number);
  return `${d} ${MONTH_SHORT[m - 1]}`;
}

/** 'YYYY-MM-DD' → 'Вс'. */
function weekday(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  return DOW[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
}

interface Props {
  groupId: number;
  isIndividual: boolean;
  /** Цвет направления — им же красится заливка полосы прогресса. */
  color: string;
}

/**
 * Ключевые цифры группы + полоса прогресса курса.
 *
 * Ни одного нового запроса: план уроков (`useGroupPlan`) уже грузит таблица
 * плана на этой же вкладке, а состав (`useMemberships`) переиспользует ключ
 * вкладки «Ученики» — react-query отдаёт их из общего кэша.
 */
export default function GroupKpiRow({ groupId, isIndividual, color }: Props) {
  const { data: rows = [] } = useGroupPlan(groupId);
  const { data: memberships = [] } = useMemberships({ group_id: groupId });

  const m = useMemo(() => {
    // Отменённые и перенесённые строки не считаем: перенос порождает новую
    // плановую строку, иначе один урок попал бы в сумму дважды.
    const counted = rows.filter((r) => r.status !== 'cancelled' && r.status !== 'moved');
    const done = counted.filter((r) => r.status === 'done').length;
    const overdue = counted.filter((r) => r.status === 'overdue').length;
    const pending = counted.filter((r) => r.status === 'pending').length;

    const today = todayMSK();
    const next = counted
      .filter((r) => r.status === 'pending' && r.scheduled_date >= today)
      .sort((a, b) => a.scheduled_date.localeCompare(b.scheduled_date))[0] || null;

    const total = done + overdue + pending;
    return { done, overdue, pending, total, next, pct: total ? Math.round((done / total) * 100) : 0 };
  }, [rows]);

  const tiles: StatTile[] = [
    {
      label: 'Учеников',
      value: memberships.length,
      sub: isIndividual ? 'индивидуальная' : 'активных членств',
    },
    {
      label: 'Проведено',
      value: m.done,
      sub: m.total ? `из ${m.total} по плану` : 'план не сгенерирован',
    },
    {
      label: 'Осталось',
      value: m.pending,
      // Просроченные («надо заполнить») — единственное, что требует действия.
      sub: m.overdue > 0 ? `${m.overdue} не заполнено вовремя` : 'по расписанию',
      subTone: m.overdue > 0 ? 'warn' : 'default',
    },
    {
      label: 'Следующий урок',
      value: m.next ? shortDate(m.next.scheduled_date) : '—',
      sub: m.next
        ? `${weekday(m.next.scheduled_date)}${m.next.scheduled_time ? ` · ${m.next.scheduled_time}` : ''}`
        : 'занятий впереди нет',
    },
  ];

  return (
    <>
      <StatTiles items={tiles} />
      {m.total > 0 && (
        <div className="dprogress" style={{ '--entity-c': color } as CSSProperties}>
          <div className="dprogress__head">
            <span className="dprogress__title">Прогресс курса</span>
            <span className="dprogress__pct">{m.pct}%</span>
          </div>
          <div
            className="dprogress__bar"
            role="progressbar"
            aria-valuenow={m.pct}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="Прогресс курса группы"
          >
            <div className="dprogress__fill" style={{ width: `${m.pct}%` }} />
          </div>
          <div className="dprogress__foot">
            <span>{m.done} из {m.total} уроков проведено</span>
            <span>
              {m.next
                ? `Следующее занятие: ${weekday(m.next.scheduled_date)} ${shortDate(m.next.scheduled_date)}${m.next.scheduled_time ? ` · ${m.next.scheduled_time}` : ''}`
                : 'Следующее занятие не запланировано'}
            </span>
          </div>
        </div>
      )}
    </>
  );
}
