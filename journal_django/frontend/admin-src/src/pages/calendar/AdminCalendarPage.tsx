import { useCallback, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTeachers } from '../../hooks/useTeachers';
import { useAdminCalendar } from '../../hooks/useAdminCalendar';
import { CalendarView } from '../../shared/calendar/CalendarView';
import { Combobox } from '../../components/form/Combobox';
import { currentMondayMsk, addDays, isoDate } from '../../shared/calendar/lib';
import type { Occurrence } from '../../shared/calendar/types';
import { PageHeader } from '../../components/shell/PageHeader';

/**
 * Раздел «Календарь» admin SPA — read-only расписание (RequireRole manager/
 * admin/superadmin в App.tsx). По умолчанию показывает занятия ВСЕХ
 * преподавателей (вся школа); фильтр «Преподаватель» сужает до одного. Обёртка
 * над презентационным CalendarView (см. teacher-src CalendarPage.tsx для
 * оригинального паттерна) — без onAction/onLessonAction, попап занятия
 * строго read-only, с кнопкой перехода в план группы (onOpenGroup).
 */
export default function AdminCalendarPage() {
  const navigate = useNavigate();
  const teachers = useTeachers(true); // включая архивных — можно посмотреть их прошлое расписание
  const [teacherId, setTeacherId] = useState<number | null>(null);
  const [range, setRange] = useState(() => {
    const monday = currentMondayMsk();
    return { from: isoDate(monday), to: isoDate(addDays(monday, 6)) };
  });

  const teacherOptions = useMemo(
    () => [
      // Пустое значение → сброс фильтра, занятия всех преподавателей (Combobox без
      // отдельной кнопки очистки, поэтому явный пункт).
      { value: '', label: 'Все преподаватели' },
      ...(teachers.data || []).slice().sort((a, b) => a.name.localeCompare(b.name))
        .map((t) => ({ value: String(t.id), label: t.name })),
    ],
    [teachers.data],
  );

  // Колонки вида «по преподавателям»: активные несервисные по алфавиту, затем
  // «дополнительные» (неактивные/служебные) — сетка покажет их, только если у них
  // есть уроки на неделе, чтобы ничего не пропадало.
  const gridTeachers = useMemo(() => {
    const byName = (a: { name: string }, b: { name: string }) => a.name.localeCompare(b.name, 'ru');
    const all = teachers.data || [];
    const main = all.filter((t) => t.active && !t.is_service).sort(byName)
      .map((t) => ({ id: t.id, name: t.name }));
    const extra = all.filter((t) => !(t.active && !t.is_service)).sort(byName)
      .map((t) => ({ id: t.id, name: t.name, extra: true, hint: t.is_service ? 'служебный' : 'не активен' }));
    return [...main, ...extra];
  }, [teachers.data]);

  const { data, isLoading, isError, isFetching, isPlaceholderData } = useAdminCalendar(teacherId, range.from, range.to);

  const onVisibleRangeChange = useCallback((from: string, to: string) => {
    setRange((prev) => (prev.from === from && prev.to === to ? prev : { from, to }));
  }, []);

  const onOpenGroup = useCallback((occ: Occurrence) => {
    if (occ.groupId != null) navigate(`/admin/groups/${occ.groupId}`);
  }, [navigate]);

  return (
    <div className="admin-calendar-page">
      {/* Раньше страница начиналась сразу с фильтра — без заголовка нельзя было
          понять, в каком ты разделе. Фильтр преподавателя перенесён в шапку:
          это единственный управляющий элемент страницы. */}
      <PageHeader
        title="Календарь"
        sub="Занятия всех преподавателей. Клик по занятию — карточка группы."
        actions={
          <div className="calendar-teacher-filter">
            <Combobox
              value={teacherId != null ? String(teacherId) : ''}
              onChange={(v) => setTeacherId(v ? Number(v) : null)}
              options={teacherOptions}
              placeholder="Все преподаватели"
            />
          </div>
        }
      />

      <CalendarView
        occurrences={data?.occurrences ?? []}
        unscheduled={data?.unscheduled ?? []}
        availability={isPlaceholderData ? null : data?.availability ?? null}
        // Вид «по преподавателям» — только в режиме «Все преподаватели»; с выбранным
        // преподавателем остаётся недельная сетка с фоном его графика.
        teachers={teacherId == null ? gridTeachers : undefined}
        availabilityByTeacher={teacherId == null && !isPlaceholderData ? data?.availabilityByTeacher ?? null : null}
        // В режиме «по преподавателям» без списка преподавателей показывать нечего: скелет,
        // а не обычная сетка, которая тут же сменилась бы.
        isLoading={isLoading || (teacherId == null && teachers.isPending)}
        dataReady={!isPlaceholderData}
        dataWindow={data?.window ?? null}
        isError={isError}
        isFetching={isFetching}
        onVisibleRangeChange={onVisibleRangeChange}
        role="admin"
        showTitle={false}
        onOpenGroup={onOpenGroup}
      />
    </div>
  );
}
