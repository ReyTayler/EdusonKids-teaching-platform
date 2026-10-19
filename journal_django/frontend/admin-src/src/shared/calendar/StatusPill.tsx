/**
 * Статус-пилюля урока (tone-based, цвет только для смысла).
 *
 * AnyStatus объединяет старый /api/report ('notime' — доп. статус того
 * контракта) и новый /api/calendar+/plan (OccStatus). Union держим
 * самодостаточным (не импортируем из teacher-src/lib/types), чтобы
 * shared/calendar не тянул зависимость на teacher-src — см. types.ts.
 * Оригинальный компонент teacher-src/src/components/ui/StatusPill.tsx теперь
 * ре-экспортирует отсюда (используется MyLessonsPage/ReportPage — статусы
 * /api/report/lessons, календарём не являются).
 */
export type AnyStatus = 'done' | 'pending' | 'overdue' | 'notime' | 'cancelled' | 'moved';

const LABEL: Record<AnyStatus, string> = {
  done: 'Заполнено',
  overdue: 'Надо заполнить',
  pending: 'Ещё не было',
  notime: 'Без времени',
  cancelled: 'Отменён',
  moved: 'Перенесён',
};

export function StatusPill({ status, label }: { status: AnyStatus; label?: string }) {
  return (
    <span className={`st st--${status}`}>
      <span className="st-dot" />
      {label ?? LABEL[status]}
    </span>
  );
}
