import { ScheduleEditor } from '@shared/shared/availability/ScheduleEditor';
import { PageHead } from '../../components/ui/PageHead';

/** «График работы»: преподаватель сам отмечает окна, когда ему можно ставить уроки. */
export default function SchedulePage() {
  return (
    <div className="grp-page">
      <PageHead title="График работы" />
      <div className="pr-note">
        Отметьте время, когда вам можно ставить уроки. Шаблон повторяется каждую неделю; конкретную неделю можно поправить отдельно.
      </div>
      <ScheduleEditor
        basePath="/api/work-schedule"
        calendarUrl={(from, to) => `/api/calendar?from=${from}&to=${to}`}
      />
    </div>
  );
}
