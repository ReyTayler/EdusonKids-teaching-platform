import { useState } from 'react';
import { DataTable, type Column } from '../table/DataTable';
import { EntityLink } from '../EntityLink';
import { EmptyState } from '../ui/EmptyState';
import {
  useStudentLessons,
  type StudentLessonSort,
  type StudentLessonsPeriod,
} from '../../hooks/useStudentLessons';
import { fmtDate, fmtDateTime, fmtLessons, fmtRub } from '../../lib/format';
import { STUDENT_LESSON_KIND_LABELS } from '../../lib/labels';
import type { StudentLessonRow } from '../../lib/types';

interface Props {
  studentId: number;
  /** Без периода — вся история (вкладка карточки). С периодом — модалка дашборда. */
  period?: StudentLessonsPeriod;
  /** Подпись таблицы для ассистивных технологий. */
  title?: string;
}

/**
 * Таблица уроков ученика: все занятия, на которых он был, по убыванию даты
 * сохранения. Обе даты в таблице — занятия и сохранения: они часто расходятся,
 * и без первой порядок строк выглядел бы случайным.
 *
 * «Признано» — точный FIFO по уроку (сколько денег списала именно эта строка).
 * Ноль честный: у бесплатного занятия денег не берут вовсе, у урока сверх
 * оплаченного их ещё нет — такая строка помечена «в долг».
 *
 * Один компонент на два экрана: вкладка «Уроки» карточки ученика и модалка
 * дашборда «Посещения учеников». Копия разъехалась бы с оригиналом.
 */
export default function StudentLessonsTable({ studentId, period, title }: Props) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(50);
  const [sortBy, setSortBy] = useState<StudentLessonSort>('submitted_at');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');
  const { data, isFetching, isError } = useStudentLessons(
    studentId, page, pageSize, sortBy, sortDir, period,
  );

  const columns: Column<StudentLessonRow>[] = [
    { key: 'lesson_id', label: 'ID', sortable: false, width: 70 },
    {
      key: 'lesson_number',
      label: 'Номер урока',
      sortable: false,
      // numeric(5,1) приходит как «12.0» — fmtLessons убирает пустой хвост.
      cell: (r) => fmtLessons(Number(r.lesson_number)),
    },
    {
      key: 'lesson_date',
      label: 'Дата занятия',
      cell: (r) => fmtDate(r.lesson_date),
    },
    {
      key: 'submitted_at',
      label: 'Сохранён',
      cell: (r) => fmtDateTime(r.submitted_at),
    },
    {
      key: 'kind',
      label: 'Тип',
      sortable: false,
      cell: (r) => STUDENT_LESSON_KIND_LABELS[r.kind] || r.kind,
    },
    {
      key: 'duration_minutes',
      label: 'Продолжительность',
      sortable: false,
      cell: (r) => `${r.duration_minutes} мин`,
    },
    {
      key: 'group_name',
      label: 'Группа',
      sortable: false,
      cell: (r) => <EntityLink section="groups" id={r.group_id} text={r.group_name} />,
    },
    {
      key: 'teacher_name',
      label: 'Преподаватель',
      sortable: false,
      cell: (r) => <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} />,
    },
    {
      key: 'direction_name',
      label: 'Направление',
      sortable: false,
      cell: (r) => r.direction_name || '—',
    },
    {
      key: 'recognized_amount',
      label: 'Признано',
      sortable: false,
      // У частично оплаченного урока признанная сумма больше нуля И стоит флаг
      // долга одновременно — партий хватило на часть веса. Пометка это
      // различает: иначе «1 000 ₽ в долг» читалось бы как «вся сумма в долг».
      cell: (r) => (
        <span className="slessons__money">
          {fmtRub(r.recognized_amount)}
          {r.is_debt && (
            <span className="slessons__debt">
              {Number(r.recognized_amount) > 0 ? 'частично в долг' : 'в долг'}
            </span>
          )}
        </span>
      ),
    },
  ];

  // Упавший запрос нельзя показывать пустой таблицей: «ничего не найдено» на
  // денежной вкладке прочитается как «уроков нет и денег не признано».
  if (isError) {
    return (
      <EmptyState hint="Обновите страницу — если повторится, сообщите администратору.">
        Не удалось загрузить уроки ученика
      </EmptyState>
    );
  }

  return (
    <DataTable<StudentLessonRow>
      data={data?.rows || []}
      columns={columns}
      title={title || 'Уроки ученика'}
      isLoading={isFetching}
      serverPagination={{
        page,
        pageSize,
        total: data?.total || 0,
        sortBy,
        sortDir,
        // Фильтров у таблицы нет: ни одна колонка не searchable, поэтому
        // тулбар фильтров схлопывается в null, а обработчик — заглушка.
        filters: {},
        onPageChange: setPage,
        onPageSizeChange: (size) => { setPageSize(size); setPage(1); },
        // Сортировать можно только по двум датам (белый список на бэке, мусор
        // даёт 400) — остальные колонки помечены sortable: false и сюда не
        // приходят. Смена порядка возвращает на первую страницу: иначе третья
        // страница нового порядка — это уже не то, что человек смотрел.
        onSortChange: (col, dir) => {
          setSortBy(col as StudentLessonSort);
          setSortDir(dir);
          setPage(1);
        },
        onFiltersChange: () => {},
      }}
    />
  );
}
