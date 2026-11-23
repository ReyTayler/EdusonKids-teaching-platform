import { ScheduleEditor } from '../../shared/availability/ScheduleEditor';
import type { Teacher } from '../../lib/types';

/** Вкладка «График работы» карточки преподавателя. */
export default function TeacherScheduleTab({ teacher, canEdit }: { teacher: Teacher; canEdit: boolean }) {
  return (
    <ScheduleEditor
      contextTitle={`График работы · ${teacher.name}`}
      basePath={`/api/admin/teachers/${teacher.id}/work-schedule`}
      calendarUrl={(from, to) => `/api/admin/calendar?teacher_id=${teacher.id}&from=${from}&to=${to}`}
      // Архивному преподавателю график не правим — только просмотр.
      readOnly={!teacher.active || !canEdit}
      readOnlyReason={!teacher.active ? 'Преподаватель в архиве. График доступен только для просмотра.' : undefined}
    />
  );
}
