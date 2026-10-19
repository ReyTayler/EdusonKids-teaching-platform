import { useMemo } from 'react';
import { useLessonsForGroup } from '../../hooks/useLessons';
import { useDirections } from '../../hooks/useDirections';
import { directionColor } from '../../lib/direction-color';
import type { Group, Lesson } from '../../lib/types';

interface Props {
  group: Group;
  selectedSlot: number | null;
  onSelectSlot: (slot: number, lessonId: number | null) => void;
}

export function LessonGrid({ group, selectedSlot, onSelectSlot }: Props) {
  const { data: lessons = [] } = useLessonsForGroup({ group_id: group.id });
  const { data: directions = [] } = useDirections(true);
  const direction = directions.find((d) => d.id === group.direction_id) || null;
  const color = directionColor(direction);

  // Half-lesson: 45 минут → шаг 0.5 (см. CLAUDE.md), слот = lesson_number / step.
  // Для обычных групп (step=1) совпадает со старым Math.ceil (номера целые); для
  // 45-мин групп каждая половина урока получает свой слот (0.5→1, 1.0→2, …) —
  // без схлопывания пары половинок в одну ячейку (был баг: ceil(0.5)===ceil(1)).
  const step = group.lesson_duration_minutes === 45 ? 0.5 : 1;

  // Группа-продолжение (перевод в пустую персональную группу, Phase 1b): курс
  // начинается не с урока №1, а с offset+step — уроки 1..offset ученик отработал в
  // прежней группе, в этой группе их физически нет. Сетка не рисует эти ячейки:
  // иначе первый клик открывал бы урок, на котором переведённый ученик
  // заблокирован (isLockedByTransfer в LessonEditor), и сохранить его нельзя.
  // Для обычных групп offset=0 → поведение прежнее (нумерация с 1).
  const offsetSlots = Math.round(Number(group.lesson_number_offset || 0) / step);

  const byNumber = useMemo(() => {
    const map = new Map<number, Lesson>();
    let max = 0;
    for (const l of lessons) {
      // Доп.уроки (сгорание/отработка) — это Lesson(lesson_type='extra') с тем же
      // group_id и lesson_number, что и пропущенный урок. Слот сетки = групповое
      // занятие, поэтому extra-уроки его не занимают: иначе клик по слоту открыл
      // бы пер-ученический доп.урок вместо исходного (сортировка списка по дате
      // desc делает проведённый позже extra «победителем» first-wins-коллапса).
      if (['extra', 'burned'].includes(l.lesson_type)) continue;
      const slot = Math.max(1, Math.round(Number(l.lesson_number) / step));
      if (!map.has(slot)) map.set(slot, l);
      if (slot > max) max = slot;
    }
    return { map, max };
  }, [lessons, step]);

  // Длина курса ИМЕННО ЭТОЙ группы (groups.lessons_total) перекрывает программу
  // направления: группа-остаток проходит только часть курса, и рисовать ей клетки
  // до конца программы — врать. Пусто — как в направлении (прежнее поведение).
  // Единица — уроки, поэтому делим на шаг: у 45-мин группы 1 урок = 2 клетки.
  const courseLessons = group.lessons_total ?? direction?.total_lessons ?? null;
  const totalSlots = courseLessons != null
    ? Math.round(Number(courseLessons) / step)
    : null;
  const slotCount = totalSlots
    ? Math.max(totalSlots, byNumber.max)
    : Math.max(byNumber.max, offsetSlots + 12);
  // Рисуем ячейки offsetSlots+1 .. slotCount (для offset=0 — как раньше, 1..slotCount).
  const visibleCount = Math.max(0, slotCount - offsetSlots);

  return (
    <div className="lesson-grid">
      {Array.from({ length: visibleCount }, (_, i) => {
        const num = offsetSlots + i + 1;
        const lesson = byNumber.map.get(num);
        const filled = !!lesson;
        const isSelected = selectedSlot === num;
        return (
          <button
            key={num}
            type="button"
            className={`lesson-square${filled ? ' is-filled' : ''}${isSelected ? ' is-selected' : ''}`}
            style={{ ['--dir-color' as string]: color }}
            aria-label={`Урок №${num}${filled ? '' : ' (не проведён)'}`}
            data-tip={`Урок №${num}`}
            onClick={() => onSelectSlot(num, lesson ? lesson.id : null)}
          >
            <span className="lesson-square__num">{num}</span>
          </button>
        );
      })}
    </div>
  );
}
