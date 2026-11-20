import { useMemo } from 'react';
import { useListSearchParams } from '../../../hooks/useListSearchParams';
import { useTeachers } from '../../../hooks/useTeachers';
import { TableFilterPanel, type FilterField } from '../../../components/filters';

/**
 * Лупа «Заполнить» для шапки дашборда. Отдельный файл, а не часть FillTab:
 * шапка грузится сразу, а вкладка — ленивым чанком. Преподаватель — extra-параметр
 * `teacher` (не f.*): бэк фильтрует по teacher_id, а не по колонке таблицы.
 */
export function FillSearch() {
  const { getExtra, setExtra } = useListSearchParams({ sortBy: 'date', sortDir: 'desc', pageSize: 30 });
  const teachers = useTeachers(true); // включая архивных — у них тоже бывают старые долги
  const rawTeacher = getExtra('teacher');
  const teacherId = rawTeacher && /^\d+$/.test(rawTeacher) ? Number(rawTeacher) : null;

  const fields = useMemo<FilterField[]>(() => [{
    kind: 'combobox',
    key: 'teacher',
    label: 'Преподаватель',
    allLabel: 'Все преподаватели',
    options: (teachers.data || []).slice().sort((a, b) => a.name.localeCompare(b.name))
      .map((t) => ({ value: String(t.id), label: t.name })),
  }], [teachers.data]);

  return (
    <TableFilterPanel
      fields={fields}
      filters={teacherId != null ? { teacher: String(teacherId) } : {}}
      onApply={(next) => setExtra('teacher', next.teacher || null)}
      label="Поиск просроченных уроков"
    />
  );
}
