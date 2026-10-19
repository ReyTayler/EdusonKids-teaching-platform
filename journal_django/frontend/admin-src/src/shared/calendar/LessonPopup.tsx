import { Modal } from './Modal';
import { StatusPill } from './StatusPill';
import { Button } from '../../components/ui/Button';
import type { Occurrence } from './types';
import { parseIsoDate, columnIndexOfIsoDate, dayMonth } from './lib';

const DAY_FULL = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье'];

export type LessonActionKind = 'reschedule' | 'cancel' | 'change-teacher';

/**
 * Read-only детали занятия (Occurrence из /api/calendar или /plan) +
 * необязательная кнопка «Отметить урок» (onSubmit/CalendarView.onLessonAction)
 * + необязательные быстрые действия admin (onAction, шаг 7) — перенос/отмена/
 * смена преподавателя конкретного занятия. Оба набора кнопок не связаны:
 * onSubmit — отметка урока (не задействован ни в одном SPA сейчас), onAction —
 * операции плана (только admin: role='admin' И передан onAction). teacher
 * ничего из этого не передаёт — регресс исключён (компонент общий).
 */
export function LessonPopup({
  lesson,
  onClose,
  onSubmit,
  onAction,
  onOpenGroup,
  role,
}: {
  lesson: Occurrence;
  onClose: () => void;
  onSubmit?: () => void;
  onAction?: (kind: LessonActionKind, lesson: Occurrence) => void;
  /** Кнопка «Открыть группу» — не связана с onAction/onSubmit, видна независимо от role, если передан И lesson.groupId задан. Используется admin-календарём («Календарь» → /admin/groups/:id), не задействован ни в teacher, ни в GroupDetailPage (там карточка группы уже открыта). */
  onOpenGroup?: (lesson: Occurrence) => void;
  role?: 'teacher' | 'admin';
}) {
  // done — занятие уже проведено, операции плана его не трогают (инвариант
  // "никогда не двигать done", docs/lesson-scheduling.md). Отмена — только
  // для курсовых строк (seq != null, не доп. занятие).
  const canModifyPlan = role === 'admin' && !!onAction && lesson.status !== 'done';
  const canCancelPlan = canModifyPlan && !lesson.isExtra && lesson.seq != null;
  const dayName = DAY_FULL[columnIndexOfIsoDate(lesson.date)];
  const dateLabel = dayMonth(parseIsoDate(lesson.date));
  const when = lesson.time
    ? `${dayName}, ${dateLabel} · ${lesson.time}`
    : `${dayName}, ${dateLabel} · время не указано`;
  const displayTeacher = lesson.teacherOverride ?? lesson.teacher;

  return (
    <Modal title={lesson.groupDisplay} subtitle={when} onClose={onClose}>
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <StatusPill status={lesson.status} label={lesson.label} />
        </div>
      </div>

      <div>
        <div className="t-sec-label">Информация</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
          <div className="t-info-row"><span className="t-info-ico">👤</span><span>Преподаватель: {displayTeacher}</span></div>
          {lesson.teacherOverride && lesson.teacherOverride !== lesson.teacher && (
            <div className="t-info-row"><span className="t-info-ico">🔁</span><span>Замена вместо: {lesson.teacher}</span></div>
          )}
          {lesson.direction && <div className="t-info-row"><span className="t-info-ico">🧭</span><span>Направление: {lesson.direction}</span></div>}
          {!lesson.isExtra && lesson.lessonNumber != null && (
            <div className="t-info-row"><span className="t-info-ico">#️⃣</span><span>Урок №{lesson.lessonNumber}</span></div>
          )}
          {lesson.movedFrom && (
            <div className="t-info-row"><span className="t-info-ico">↪️</span><span>Перенесён с {lesson.movedFrom}{lesson.movedTo ? ` → ${lesson.movedTo}` : ''}</span></div>
          )}
        </div>
      </div>

      {lesson.students.length > 0 && (
        <div>
          <div className="t-sec-label">Ученики · {lesson.students.length}</div>
          <div className="t-students">
            {lesson.students.map((s, i) => (
              <div key={`${s.name}-${i}`} className="t-student"><span>{s.name}</span></div>
            ))}
          </div>
        </div>
      )}

      {onOpenGroup && lesson.groupId != null && (
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <Button size="sm" onClick={() => onOpenGroup(lesson)}>Открыть группу</Button>
        </div>
      )}

      {canModifyPlan && (
        <div style={{ display: 'flex', gap: 'var(--space-2)', flexWrap: 'wrap' }}>
          <Button size="sm" onClick={() => onAction!('reschedule', lesson)}>Перенести</Button>
          <Button size="sm" onClick={() => onAction!('change-teacher', lesson)}>Сменить преподавателя</Button>
          {canCancelPlan && (
            <Button size="sm" variant="danger" onClick={() => onAction!('cancel', lesson)}>Отменить</Button>
          )}
        </div>
      )}

      {onSubmit && (
        <div className="lf-actions">
          <button type="button" className="btn-save grp-card-btn" onClick={onSubmit}>
            Отметить урок
          </button>
        </div>
      )}
    </Modal>
  );
}
