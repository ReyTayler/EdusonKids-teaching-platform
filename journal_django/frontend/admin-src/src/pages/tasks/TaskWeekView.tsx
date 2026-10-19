import { useMemo } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  addDays, addWeeks, columnIndexOfIsoDate, currentMondayMsk, dayMonth, isoDate,
  parseIsoDate, sameDay, todayMsk, weekRangeLabel,
} from '../../shared/calendar/lib';
import { useTaskWeek } from '../../hooks/useTasks';
import { TaskCard } from './TaskCard';
import { EmptyState } from '../../components/ui/EmptyState';
import { Button } from '../../components/ui/Button';
import type { TaskFilters, TaskRow } from '../../lib/tasks';

const DOW_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];

interface Props {
  filters: TaskFilters;
  onOpen: (id: number) => void;
}

/**
 * Недельная сетка задач (спека 2026-08-24). Дата берётся из ?from= (любой
 * день недели — понедельник довычисляется тут же), диапазон запроса — ровно
 * 7 дней, поэтому лимит бэкенда в 62 дня никогда не подступает.
 *
 * Даты недели считаются теми же MSK-хелперами, что и admin/teacher
 * календарь (shared/calendar/lib.ts) — не изобретаем вторую версию
 * «понедельник недели» с другим часовым поясом.
 *
 * Задачи без due_date сюда не попадают — раскладывать их по дням некуда,
 * так решено в спеке раздела.
 *
 * Карточка — тот же <TaskCard>, что и на доске, но БЕЗ <DndContext> вокруг:
 * useDraggable безопасно работает и без провайдера (дефолтный контекст
 * dnd-kit отдаёт пустой список активаторов), просто перетаскивание не
 * запускается — ровно то поведение, которое нужно недельному виду.
 */
export function TaskWeekView({ filters, onOpen }: Props) {
  const [sp, setSp] = useSearchParams();

  const today = useMemo(() => todayMsk(), []);
  const currentMonday = useMemo(() => currentMondayMsk(), []);

  const rawFrom = sp.get('from');
  const monday = useMemo(() => {
    if (!rawFrom || !/^\d{4}-\d{2}-\d{2}$/.test(rawFrom)) return currentMonday;
    const d = parseIsoDate(rawFrom);
    const dow = d.getUTCDay(); // 0=Вс … 6=Сб
    return addDays(d, dow === 0 ? -6 : 1 - dow);
  }, [rawFrom, currentMonday]);

  const days = useMemo(() => Array.from({ length: 7 }, (_, i) => addDays(monday, i)), [monday]);
  const dateFrom = isoDate(days[0]);
  const dateTo = isoDate(days[6]);
  const isCurrentWeek = sameDay(monday, currentMonday);

  const { data, isLoading, isError, isFetching, refetch } = useTaskWeek(dateFrom, dateTo, filters);

  const goWeek = (targetMonday: Date) => {
    const next = new URLSearchParams(sp);
    next.set('from', isoDate(targetMonday));
    setSp(next, { replace: true });
  };

  const byColumn = useMemo(() => {
    const cols: TaskRow[][] = Array.from({ length: 7 }, () => []);
    for (const task of data || []) {
      if (!task.due_date) continue;
      cols[columnIndexOfIsoDate(task.due_date)].push(task);
    }
    return cols;
  }, [data]);

  return (
    <div className="task-week">
      <div className="task-week__nav">
        <button
          type="button"
          className="task-week__nav-btn"
          onClick={() => goWeek(addWeeks(monday, -1))}
          aria-label="Предыдущая неделя"
        >
          <ChevronGlyph dir="left" />
        </button>
        <span className="task-week__range">{weekRangeLabel(monday)}</span>
        <button
          type="button"
          className="task-week__nav-btn"
          onClick={() => goWeek(addWeeks(monday, 1))}
          aria-label="Следующая неделя"
        >
          <ChevronGlyph dir="right" />
        </button>
        <button
          type="button"
          className="task-week__today-btn"
          disabled={isCurrentWeek}
          onClick={() => goWeek(currentMonday)}
        >
          Сегодня
        </button>
        {isFetching && !isLoading && <span className="task-week__updating">Обновляем…</span>}
      </div>

      {isLoading ? (
        <div className="task-week__loading">Загружаем неделю…</div>
      ) : isError ? (
        <EmptyState
          hint="Проверьте соединение и попробуйте ещё раз"
          action={<Button variant="secondary" onClick={() => refetch()}>Повторить</Button>}
        >
          Не удалось загрузить неделю
        </EmptyState>
      ) : (
        <div className="task-week__grid">
          {days.map((day, i) => {
            const rows = byColumn[i];
            const isToday = sameDay(day, today);
            return (
              <div key={isoDate(day)} className={`task-week__col${isToday ? ' is-today' : ''}`}>
                <div className="task-week__col-head">
                  <span className="task-week__col-dow">{DOW_LABELS[i]}</span>
                  <span className="task-week__col-date">{dayMonth(day)}</span>
                </div>
                <div className="task-week__col-body">
                  {rows.length === 0 ? (
                    <div className="task-week__col-empty">Пусто</div>
                  ) : rows.map((task) => (
                    <TaskCard key={task.id} task={task} stageId={task.stage_id}
                              onOpen={onOpen} compact />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function ChevronGlyph({ dir }: { dir: 'left' | 'right' }) {
  const points = dir === 'left' ? '15 18 9 12 15 6' : '9 18 15 12 9 6';
  return (
    <svg
      width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
    >
      <polyline points={points} />
    </svg>
  );
}
