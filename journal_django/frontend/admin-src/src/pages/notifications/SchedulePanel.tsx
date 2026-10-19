import { useNotificationSchedule, type NotificationScheduleJob } from '../../hooks/useNotifications';
import { PageLoading } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';
import { fmtDateTime } from '../../lib/format';
import { NOTIFICATION_STATUS_LABELS } from '../../lib/labels';

/** Сутки в миллисекундах — порог «дайджест давно не отрабатывал». */
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

/**
 * Пояснения к задачам-напоминаниям по ключу. Заголовок и время приходят с
 * бэкенда; здесь — что именно произойдёт и при каких условиях.
 */
const JOB_NOTES: Record<string, string> = {
  morning_digest: 'Письмо преподавателю с расписанием на сегодня, если у него есть уроки.',
  lesson_unrecorded:
    'Письмо преподавателю, если после занятия урок не записан; до 3 повторов — каждые сутки.',
  parent_day_before: 'Письмо родителям накануне: «урок завтра». Только если включены напоминания родителям.',
  parent_hour_before: 'Письмо родителям за час до начала урока. Только если включены напоминания родителям.',
};

/** Задачи, у которых «давно не было запуска» — норма: работы может просто не быть. */
const EVENT_DRIVEN_JOBS = new Set(['lesson_unrecorded', 'parent_day_before', 'parent_hour_before']);

/**
 * Молчащая задача — единственный отказ, который никак себя не проявляет: beat
 * умер, сообщений просто нет, и никто не замечает. Поэтому подсвечиваем два
 * случая: задача не отрабатывала ни разу и дайджест не отрабатывал сутки.
 *
 * Порог по суткам применяем только к дайджестам (у них своё расписание раз в
 * день). У «Отправки очереди» отметка — время последней реальной отправки:
 * при пустой очереди отправлять нечего, и молчание там нормально.
 */
function isStale(job: NotificationScheduleJob): boolean {
  // Напоминания зависят от уроков и от флага родителей: тишина — не поломка.
  if (EVENT_DRIVEN_JOBS.has(job.key)) return false;
  if (!job.last_run_at) return true;
  if (!job.kind) return false;
  return Date.now() - new Date(job.last_run_at).getTime() > STALE_AFTER_MS;
}

function JobCard({ job }: { job: NotificationScheduleJob }) {
  const stale = isStale(job);
  return (
    <div className={`schedule-card${stale ? ' schedule-card--stale' : ''}`}>
      <div className="schedule-card__head">
        <span className="schedule-card__title">{job.title}</span>
        <span className="schedule-card__when">{job.schedule}</span>
      </div>
      <div className="schedule-card__last">
        Последний раз: {job.last_run_at ? fmtDateTime(job.last_run_at) : 'никогда'}
      </div>
      {JOB_NOTES[job.key] && <div className="schedule-card__note">{JOB_NOTES[job.key]}</div>}
      {stale && (
        <div className="schedule-card__warn">
          {job.last_run_at
            ? 'Задача не отрабатывала больше суток — проверьте, жив ли Celery beat.'
            : 'Задача не отрабатывала ни разу — проверьте, жив ли Celery beat.'}
        </div>
      )}
    </div>
  );
}

/** Плитка счётчика по статусу очереди. */
function CountTile({ status, value }: { status: string; value: number }) {
  return (
    <div className={`schedule-count schedule-count--${status}`}>
      <span className="schedule-count__value">{value}</span>
      <span className="schedule-count__label">{NOTIFICATION_STATUS_LABELS[status] ?? status}</span>
    </div>
  );
}

export function SchedulePanel() {
  const { data, isLoading } = useNotificationSchedule();

  if (isLoading) return <PageLoading />;
  if (!data) return <EmptyState hint="Попробуйте обновить страницу">Расписание недоступно</EmptyState>;

  const counts = Object.entries(data.counts);

  return (
    <div className="schedule-panel">
      {counts.length > 0 && (
        <div className="schedule-counts">
          {counts.map(([status, value]) => (
            <CountTile key={status} status={status} value={value} />
          ))}
        </div>
      )}
      <div className="schedule-cards">
        {data.jobs.map((job) => <JobCard key={job.key} job={job} />)}
      </div>
    </div>
  );
}
