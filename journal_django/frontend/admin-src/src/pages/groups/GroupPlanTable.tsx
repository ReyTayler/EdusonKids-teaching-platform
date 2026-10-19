import { useMemo } from 'react';
import { DataTable, type Column } from '../../components/table/DataTable';
import { StatusPill } from '../../shared/calendar/StatusPill';
import { useGroupPlan, type PlanRow } from '../../hooks/useGroupPlanCalendar';
import type { OccStatus } from '../../shared/calendar/types';
import { BlockLoading } from '../../components/ui/Skeleton';

const WD = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];

/**
 * ISO 'YYYY-MM-DD' → 'Вс 05.07.2026' (день недели + дд.мм.гггг) или '—'.
 *
 * Не lib/format.fmtDate: там нет дня недели, а в таблице расписания он нужен —
 * по нему видно, что занятие стоит в свой слот. Год обязателен: план курса
 * тянется через новогодний рубеж, и без него январь не отличить от января
 * следующего года.
 */
function fmtDayDate(iso: string | null): string {
  if (!iso) return '—';
  const [y, m, d] = iso.split('-').map(Number);
  const wd = new Date(Date.UTC(y, m - 1, d)).getUTCDay();
  return `${WD[wd]} ${String(d).padStart(2, '0')}.${String(m).padStart(2, '0')}.${y}`;
}

/**
 * Подписи статусов. `overdue` — «Не заполнен»: занятие по расписанию уже прошло,
 * а отчёта по нему нет. Это то же состояние, что «не отмечен» в «Потоке дня»
 * и «Надо заполнить» в кабинете преподавателя — говорим о заполнении отчёта,
 * а не о том, состоялся ли урок: этого журнал не знает.
 */
const STATUS_LABEL: Record<OccStatus, string> = {
  done: 'Проведён',
  pending: 'Запланирован',
  overdue: 'Не заполнен',
  cancelled: 'Отменён',
  moved: 'Перенесён',
};

/**
 * Таблица ВСЕХ плановых уроков группы (вкладка «Обзор»): номер урока, дата,
 * время, статус, ссылка на запись. Источник — GET /api/admin/groups/<id>/plan
 * (весь план разом).
 *
 * ОДНА колонка даты. У проведённого занятия плановая дата равна фактической —
 * это инвариант, который держит scheduling.repository.sync_position_date
 * (спека 2026-08-05 §2), поэтому две колонки только путали.
 *
 * Показываем fact_date, если он есть, иначе scheduled_date. Порядок именно
 * такой из-за легаси: в базе ещё остались строки, привязанные к факту до
 * введения инварианта, где даты разошлись. Для них фактическая — единственная
 * правда, а плановая соврала бы. Найти такие строки можно проверкой
 * «Планы групп» в разделе «Синхро» (ключ date_mismatch).
 */
export default function GroupPlanTable({ groupId }: { groupId: number }) {
  const { data: rows = [], isLoading } = useGroupPlan(groupId);

  const columns: Column<PlanRow>[] = useMemo(() => [
    {
      key: 'lesson_number', label: 'Урок', width: 110, sortable: false,
      cell: (r) => (r.is_extra ? 'доп.' : (r.lesson_number != null ? `Урок №${r.lesson_number}` : '—')),
    },
    {
      key: 'scheduled_date', label: 'Дата', sortable: false,
      cell: (r) => fmtDayDate(r.fact_date ?? r.scheduled_date),
    },
    {
      key: 'scheduled_time', label: 'Время', width: 90, sortable: false,
      cell: (r) => r.scheduled_time ?? '—',
    },
    {
      // teacher_name — ЭФФЕКТИВНЫЙ преподаватель строки: замена на дату, если она
      // была, иначе преподаватель контента (apps/scheduling/repository._plan_row_dict).
      key: 'teacher_name', label: 'Преподаватель', sortable: false,
      cell: (r) => r.teacher_name ?? '—',
    },
    {
      key: 'status', label: 'Статус', width: 160, sortable: false,
      cell: (r) => <StatusPill status={r.status} label={STATUS_LABEL[r.status]} />,
    },
    {
      key: 'record_url', label: 'Запись', sortable: false,
      cell: (r) => (r.record_url
        ? <a href={r.record_url} target="_blank" rel="noreferrer">ссылка</a>
        : '—'),
    },
  ], []);

  if (isLoading && rows.length === 0) {
    return <BlockLoading rows={5} label="Загружаем уроки…" />;
  }

  return <DataTable<PlanRow> data={rows} columns={columns} title="Уроки плана" />;
}
