import { useGroupSchedule } from '../../hooks/useGroupSchedule';
import { formatSlot } from '../../lib/slots';
import { fmtDate } from '../../lib/format';
import { BlockLoading } from '../../components/ui/Skeleton';

interface Props {
  groupId: number;
}

/**
 * Read-only витрина recurrence-шаблона расписания (group_schedule_slots:
 * активные + история версий по effective_from/to). Операции над занятиями идут
 * через planned_lessons (см. GroupPlanActions: toolbar кнопок + модалки
 * reschedule/permanent-change/cancel/extra). Модель lesson_schedule_exceptions
 * выведена из употребления (шаг 9) — её список здесь больше не показываем.
 */
export default function GroupScheduleBlock({ groupId }: Props) {
  const { data, isLoading } = useGroupSchedule(groupId);

  if (isLoading) {
    return <BlockLoading rows={3} label="Загружаем расписание…" />;
  }

  const slots = data?.slots || [];
  const activeSlots = slots.filter((s) => !s.effective_to);
  const historySlots = slots.filter((s) => s.effective_to);

  return (
    <div className="schedule-block">
      <div className="schedule-block__subtitle">Текущее расписание</div>
      {activeSlots.length === 0 ? (
        <div className="memberships__empty">Активных слотов нет</div>
      ) : (
        <div className="schedule-slots">
          {activeSlots.map((s) => (
            <div key={s.id} className="schedule-slot-chip is-active">
              <span className="schedule-slot-chip__label">{formatSlot(s)}</span>
              <span className="schedule-slot-chip__period">с {fmtDate(s.effective_from)}</span>
            </div>
          ))}
        </div>
      )}
      {historySlots.length > 0 && (
        <details className="schedule-history details-toggle">
          <summary>История слотов ({historySlots.length})</summary>
          <div className="schedule-slots">
            {historySlots.map((s) => (
              <div key={s.id} className="schedule-slot-chip is-history">
                <span className="schedule-slot-chip__label">{formatSlot(s)}</span>
                <span className="schedule-slot-chip__period">
                  {fmtDate(s.effective_from)} – {fmtDate(s.effective_to)}
                </span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
