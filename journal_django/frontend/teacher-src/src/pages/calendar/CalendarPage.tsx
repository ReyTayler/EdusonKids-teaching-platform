import { useCallback, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useCalendar } from '../../hooks/useCalendar';
import { useGroupData } from '../../hooks/useGroupData';
import { CalendarView } from '@shared/shared/calendar/CalendarView';
import { LessonPopup } from '@shared/shared/calendar/LessonPopup';
import { Modal } from '../../components/ui/Modal';
import { LessonForm } from '../../components/lessons/LessonForm';
import { ExtraLessonRecordModal } from '../../components/lessons/ExtraLessonRecordModal';
import { OccurrenceMenu } from './OccurrenceMenu';
import { currentMondayMsk, addDays, isoDate } from '../../lib/dates';
import type { Occurrence } from '../../lib/types';

interface MenuState {
  occ: Occurrence;
  x: number;
  y: number;
}

/**
 * Тонкая обёртка над презентационным CalendarView (shared/calendar) — грузит
 * /api/calendar сама (useCalendar), CalendarView владеет UI-состоянием
 * (вид/навигация/KPI/легенда) и сообщает видимое окно через
 * onVisibleRangeChange. Начальный range сидируется той же логикой, что
 * CalendarView использует по умолчанию (view='week', текущая неделя МСК) —
 * без этого первый рендер запросил бы устаревший диапазон до первого
 * эффекта CalendarView.
 *
 * Клик по занятию (onOccurrenceMenu) открывает контекстное меню: Отметить
 * урок (LessonForm с датой занятия) / Карточка группы / Чат / Подробности
 * (LessonPopup). Для чужой группы (замена, назначенная админом через «Сменить
 * преподавателя») данных в /api/getData нет — форма лениво тянет /api/getAllData.
 */
export default function CalendarPage() {
  const navigate = useNavigate();
  const [range, setRange] = useState(() => {
    const monday = currentMondayMsk();
    return { from: isoDate(monday), to: isoDate(addDays(monday, 6)) };
  });

  const { data, isLoading, isError, isFetching } = useCalendar(range.from, range.to);

  const [menu, setMenu] = useState<MenuState | null>(null);
  const [details, setDetails] = useState<Occurrence | null>(null);
  const [marking, setMarking] = useState<Occurrence | null>(null);

  // Доп.урок отмечается своим путём (ExtraLessonRecordModal) — данные группы ему не нужны.
  const markingGroup = marking && marking.extraLessonId == null ? marking.group : null;
  const marked = useGroupData(markingGroup);

  const onVisibleRangeChange = useCallback((from: string, to: string) => {
    setRange((prev) => (prev.from === from && prev.to === to ? prev : { from, to }));
  }, []);

  const onOccurrenceMenu = useCallback((occ: Occurrence, pos: { x: number; y: number }) => {
    setMenu({ occ, x: pos.x, y: pos.y });
  }, []);

  return (
    <>
      <CalendarView
        occurrences={data?.occurrences ?? []}
        unscheduled={data?.unscheduled ?? []}
        isLoading={isLoading}
        isError={isError}
        isFetching={isFetching}
        onVisibleRangeChange={onVisibleRangeChange}
        onOccurrenceMenu={onOccurrenceMenu}
        role="teacher"
      />

      {menu && (
        <OccurrenceMenu
          occ={menu.occ}
          x={menu.x}
          y={menu.y}
          onSubmitLesson={() => { setMarking(menu.occ); setMenu(null); }}
          onOpenGroup={() => { setMenu(null); navigate(`/groups/${encodeURIComponent(menu.occ.group)}`); }}
          onDetails={() => { setDetails(menu.occ); setMenu(null); }}
          onClose={() => setMenu(null)}
        />
      )}

      {details && (
        <LessonPopup lesson={details} onClose={() => setDetails(null)} role="teacher" />
      )}

      {marking && (
        marking.extraLessonId != null ? (
          <ExtraLessonRecordModal assignmentId={marking.extraLessonId} onClose={() => setMarking(null)} />
        ) : (marked.data ? (
          <LessonForm
            group={marking.group}
            groupData={marked.data}
            initialDate={marking.date}
            plannedLessonId={marking.id}
            plannedLessonNumber={marking.lessonNumber}
            isSubstitution={!!marking.teacherOverride}
            onClose={() => setMarking(null)}
          />
        ) : (
          <Modal title={marking.group} subtitle="Запись урока" onClose={() => setMarking(null)}>
            {marked.isError
              ? <div className="cal-error">Не удалось загрузить данные группы. Попробуйте ещё раз.</div>
              : marked.isLoading
                ? <div className="cal-empty">Загружаем данные группы…</div>
                // Группы нет ни среди своих, ни среди чужих: неактивна, без учеников
                // или снята с преподавателя. Раньше здесь навсегда висело «Загружаем…».
                : <div className="cal-empty">Группа не найдена среди активных — отметить урок нельзя. Обратитесь к менеджеру.</div>}
          </Modal>
        ))
      )}
    </>
  );
}
