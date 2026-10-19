/**
 * Контракт GET /api/admin/groups/:id/progress (admin) и GET
 * /api/group-progress?group= (teacher) — один и тот же ответ
 * (apps.groups.services.get_group_progress), поэтому типы общие.
 */

/** Слот урока (столбец матрицы прогресса). held=false → плановый, ещё не проведён. */
export interface ProgressSlot {
  slot: number;
  lesson_id: number | null;
  date: string | null;   // ISO 'YYYY-MM-DD' | null (плановый)
  held: boolean;
}

/** Строка ученика: посещаемость по слотам + агрегаты.
 *  cells выровнены по slots: true=был, false=не был, null=не проведён / не в составе. */
export interface ProgressStudent {
  student_id: number;
  name: string;
  present: number;
  held: number;
  pct: number;
  cells: (boolean | null)[];
  // Выровнен по cells: true — пропуск (cell=false) закрыт доп.уроком/сожжён,
  // такую ячейку матрица красит жёлтым («был через доп.урок / урок сожжён»).
  compensated: boolean[];
  // Выровнен по cells: true — бесплатное занятие (cell=true, is_free) → серый.
  free: boolean[];
  // Выровнен по cells: true — неоплачиваемый пропуск (cell=false, unpaid_skip) → синий.
  unpaid_skip: boolean[];
  transferred_lessons: number;
  transferred_from_group_name: string | null;
  // Сырое B (cumulative_transferred_lessons), НЕ капается total_slots —
  // используется только для текста тултипа «догоняем к уроку N».
  locked_through: string | number;
}

export interface GroupProgress {
  group_id: number;
  total_slots: number;
  held_slots: number;
  slots: ProgressSlot[];
  students: ProgressStudent[];
}
