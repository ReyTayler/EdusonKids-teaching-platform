import { useMemo, useState, type CSSProperties } from 'react';
import { useMyLessons } from '../../hooks/useMyLessons';
import { MonthNav } from '../../components/ui/MonthNav';
import { addDays, addMonths, dayMonthOfIso, firstOfMonthMsk, isoDate, weekdayShortOfIso } from '../../lib/dates';
import { LESSON_KIND_LABEL, STUDENT_STATUS_LABEL } from '../../lib/lessonKinds';
import { resolveDirectionColor } from '../../lib/subjects';
import type { MyLesson } from '../../lib/types';

/** Уроков за месяц у преподавателя ~20–40; 500 — потолок пагинатора с запасом. */
const MONTH_PAGE_SIZE = 500;

/**
 * Строка посещаемости: считается по ученикам урока, а не по строке зарплаты —
 * это экран «кто был», а не расчёт оплаты. «Не посещает» (неоплачиваемый пропуск)
 * в знаменатель не входит: ученик этот урок и не должен был посещать.
 */
function attendanceLine(lesson: MyLesson): string {
  if (lesson.lessonType === 'burned') return 'пропуск сгорел';
  const came = lesson.students.filter((s) => s.status === 'present' || s.status === 'free').length;
  const expected = lesson.students.filter((s) => s.status !== 'skip').length;
  // «пришли 0 из 0» читается как сбой счёта. На dev у 53 уроков нет отметок
  // учеников вовсе, а «не посещает» у всех ставится вручную и тоже бывает.
  if (expected === 0) return 'учеников не отмечено';
  return `пришли ${came} из ${expected}`;
}

function LessonRow({ lesson }: { lesson: MyLesson }) {
  const [open, setOpen] = useState(false);
  const kindLabel = LESSON_KIND_LABEL[lesson.lessonType];
  const color = resolveDirectionColor(lesson.directionColor, lesson.direction ?? lesson.group);

  return (
    <div className={`mlh-row${open ? ' is-open' : ''}`} style={{ '--subject-color': color } as CSSProperties}>
      <button
        type="button"
        className="mlh-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {/* Внутри <button> допустим только phrasing content — поэтому <span>,
            а не <div>. Раскладку задают классы .pr-*, тег на неё не влияет. */}
        <span className="pr-date">
          <span className="pr-date-day">{dayMonthOfIso(lesson.date)}</span>
          <span className="pr-date-dow">{weekdayShortOfIso(lesson.date)}</span>
        </span>
        <span className="pr-main">
          <span className="pr-title">
            <span className="t-dot" aria-hidden="true" />
            <span className="pr-group">{lesson.group}</span>
            {kindLabel && <span className={`pr-badge pr-badge--${lesson.lessonType}`}>{kindLabel}</span>}
          </span>
          <span className="pr-formula">{attendanceLine(lesson)}</span>
        </span>
        <svg className="mlh-chevron" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        lesson.students.length === 0 ? (
          <div className="mlh-students mlh-students--empty">Ученики урока не отмечены.</div>
        ) : (
          <ul className="mlh-students">
            {lesson.students.map((s) => (
              <li key={s.id} className="mlh-student">
                <span className="mlh-name">{s.name}</span>
                <span className={`mlh-mark mlh-mark--${s.status}`}>{STUDENT_STATUS_LABEL[s.status]}</span>
              </li>
            ))}
          </ul>
        )
      )}
    </div>
  );
}

/**
 * Мои уроки — история проведённых уроков текущего преподавателя по месяцам.
 *
 * Список за месяц совпадает со списком «Зарплаты» строка в строку (закреплено
 * тестом на бэке), но денег здесь нет: задача экрана — показать, кто был на
 * уроке. Записывать урок отсюда нельзя — вход в запись живёт в «Календаре».
 */
export default function MyLessonsPage() {
  const [month, setMonth] = useState<Date>(() => firstOfMonthMsk());
  const currentMonth = useMemo(() => firstOfMonthMsk(), []);

  const from = isoDate(month);
  const to = isoDate(addDays(addMonths(month, 1), -1));
  const { data, isLoading, isError, isFetching } = useMyLessons({
    page: 1, pageSize: MONTH_PAGE_SIZE, from, to,
  });

  const rows = data?.rows ?? [];
  const truncated = data ? data.total > rows.length : false;

  return (
    <div className="pr-page">
      <MonthNav title="Мои уроки" month={month} currentMonth={currentMonth} onChange={setMonth} isFetching={isFetching} />

      {isLoading ? (
        <div className="cal-skel cal-skel--md" />
      ) : isError ? (
        <div className="cal-error">Не удалось загрузить уроки.</div>
      ) : rows.length === 0 ? (
        <div className="cal-empty">В этом месяце проведённых уроков нет.</div>
      ) : (
        <>
          <div className="t-panel">
            {rows.map((lesson) => <LessonRow key={lesson.id} lesson={lesson} />)}
          </div>
          {truncated && (
            <div className="pr-note">Показаны не все уроки месяца: {rows.length} из {data!.total}.</div>
          )}
        </>
      )}
    </div>
  );
}
