import { useNotificationSchedule, type NotificationScheduleJob } from '../../hooks/useNotifications';
import { PageLoading } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';
import { fmtDateTime } from '../../lib/format';
import { NOTIFICATION_STATUS_LABELS } from '../../lib/labels';

/** Сутки в миллисекундах — порог «дайджест давно не отрабатывал». */
const STALE_AFTER_MS = 24 * 60 * 60 * 1000;

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
