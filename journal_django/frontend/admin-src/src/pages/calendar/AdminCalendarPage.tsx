import { useCallback, useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useTeachers } from '../../hooks/useTeachers';
import { useAdminCalendar } from '../../hooks/useAdminCalendar';
import { useDirections } from '../../hooks/useDirections';
import { CalendarView, NO_DIRECTION_KEY, type CalendarViewMode } from '../../shared/calendar/CalendarView';
import { CalendarSearch } from './CalendarSearch';
import { currentMondayMsk, addDays, isoDate } from '../../shared/calendar/lib';
import type { Occurrence } from '../../shared/calendar/types';

/** Стабильная ссылка: views попадает в useState/deps CalendarView. */
const WEEK_ONLY: CalendarViewMode[] = ['week'];

/**
 * Раздел «Календарь» admin SPA — read-only расписание (RequireRole manager/
 * admin/superadmin в App.tsx). По умолчанию показывает занятия ВСЕХ
 * преподавателей (вся школа); окно поиска (лупа) сужает по преподавателю и/или направлению. Обёртка
 * над презентационным CalendarView (см. teacher-src CalendarPage.tsx для
 * оригинального паттерна) — без onAction/onLessonAction, попап занятия
 * строго read-only, с кнопкой перехода в план группы (onOpenGroup).
 */
export default function AdminCalendarPage() {
  const navigate = useNavigate();
  const teachers = useTeachers(true); // включая архивных — можно посмотреть их прошлое расписание
  const directions = useDirections(true); // включая архивные — в их прошлых уроках направление ещё встречается
  const [teacherId, setTeacherId] = useState<number | null>(null);
  const [direction, setDirection] = useState<string | null>(null);
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

  // Направление фильтруется по названию (occ.direction — строка с сервера).
  const directionOptions = useMemo(
    () => [
      { value: '', label: 'Все направления' },
      ...(directions.data || []).slice().sort((a, b) => a.name.localeCompare(b.name, 'ru'))
        .map((d) => ({ value: d.name, label: d.name })),
      { value: NO_DIRECTION_KEY, label: 'Без направления' },
    ],
    [directions.data],
  );

  // Имя преподавателя — ссылкой на карточку (в шапке колонок и в карточке занятия).
  const renderTeacher = useCallback((name: string, id: number) => (
    <Link to={`/admin/teachers/${id}`}>{name}</Link>
  ), []);

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
      {/* Только заголовок: без липкой шапки, линии и подзаголовка. Фильтры живут в окне
          поиска рядом с навигацией недели (headExtra). */}
      <h1 className="page-header__title">Календарь</h1>

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
        // Админу нужна только неделя; на узком экране CalendarView сам включает список.
        views={WEEK_ONLY}
        directionFilter={direction}
        legendInteractive={false}
        renderTeacher={renderTeacher}
        headExtra={
          <CalendarSearch
            filters={{ teacherId, direction }}
            teacherOptions={teacherOptions}
            directionOptions={directionOptions}
            onApply={(f) => { setTeacherId(f.teacherId); setDirection(f.direction); }}
          />
        }
        onOpenGroup={onOpenGroup}
      />
    </div>
  );
}
