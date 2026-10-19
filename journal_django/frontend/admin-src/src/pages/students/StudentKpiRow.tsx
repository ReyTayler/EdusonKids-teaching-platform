import { useStudentStats } from '../../hooks/useStudents';
import { useStudentBalance } from '../../hooks/useStudentBalance';
import { StatTiles, type StatTile, type StatTone } from '../../components/detail/StatTiles';
import { fmtRub, fmtLessons } from '../../lib/format';

function pctTone(p: number | null | undefined): StatTone {
  if (p == null) return 'default';
  if (p >= 80) return 'ok';
  if (p >= 50) return 'warn';
  return 'danger';
}

/** «3 урока» / «2 урока» / «1 урок» — подпись к балансу. */
function lessonsWord(n: number): string {
  const abs = Math.floor(Math.abs(n));
  if (abs % 10 === 1 && abs % 100 !== 11) return 'урок';
  if ([2, 3, 4].includes(abs % 10) && ![12, 13, 14].includes(abs % 100)) return 'урока';
  return 'уроков';
}

interface Props {
  studentId: number;
}

export default function StudentKpiRow({ studentId }: Props) {
  const { data: stats } = useStudentStats(studentId);
  const { data: balance } = useStudentBalance(studentId);

  if (!stats && !balance) return null;

  const attendancePct = stats?.overall.attendance_pct ?? null;
  const activeDirections = stats?.directions.filter((d) => d.lessons_recorded > 0).length ?? 0;
  const bal = balance ? Number(balance.total_balance) : null;

  const tiles: StatTile[] = [
    {
      label: 'Посещаемость',
      value: attendancePct != null ? `${attendancePct}%` : '—',
      // Голый процент не читается без знаменателя — показываем, из чего он собран.
      // Счётчики взвешены (45-мин = 0.5 урока), поэтому дробные и в «уроках», не «занятиях».
      // Числитель зажат планом (min): доп.уроки сверх курса не дают «53 из 52».
      sub: stats
        ? `${fmtLessons(Math.min(stats.overall.attended_count, stats.overall.denominator))} из ${fmtLessons(stats.overall.denominator)} уроков`
        : undefined,
      tone: pctTone(attendancePct),
    },
    {
      label: 'Баланс уроков',
      value: balance ? fmtLessons(balance.total_balance) : '—',
      sub: bal != null
        ? (bal < 0 ? `долг ${lessonsWord(bal)}` : `оплачено вперёд, ${lessonsWord(bal)}`)
        : undefined,
      tone: bal != null && bal < 0 ? 'danger' : 'default',
      subTone: bal != null && bal < 0 ? 'danger' : 'default',
    },
    {
      label: 'Оплачено всего',
      value: balance ? fmtRub(balance.total_paid_amount) : '—',
      sub: balance ? `неотработано ${fmtRub(balance.remaining_value)}` : undefined,
    },
    {
      label: 'Направлений',
      value: String(activeDirections),
      sub: stats && stats.overall.this_month.lessons_recorded > 0
        ? `в этом месяце ${fmtLessons(stats.overall.this_month.attended_count)} из ${fmtLessons(stats.overall.this_month.lessons_recorded)}`
        : 'в этом месяце занятий не было',
    },
  ];

  return <StatTiles items={tiles} />;
}
