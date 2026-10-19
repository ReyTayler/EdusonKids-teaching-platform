import { useParams, Navigate, useNavigate } from 'react-router-dom';
import { useLessonFull, useLessonMutations } from '../../hooks/useLessons';
import { usePayrollMutations } from '../../hooks/usePayroll';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { useAuth } from '../../hooks/useAuth';
import { canWriteLessons, canSeeLessonPayroll, type Role } from '../../lib/permissions';
import { DetailShell, type DetailField } from '../../components/detail/DetailShell';
import { EntityLink } from '../../components/EntityLink';
import { PageLoading } from '../../components/ui/Skeleton';
import { fmtDate } from '../../lib/format';
import { LESSON_TYPE_LABELS } from '../../lib/labels';
import type { LessonFull } from '../../lib/types';

export default function LessonDetailPage() {
  const params = useParams();
  const id = Number(params.id);
  const navigate = useNavigate();
  const { data: lesson, isLoading } = useLessonFull(id);
  const muts = useLessonMutations();
  const payrollMuts = usePayrollMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const { me } = useAuth();
  const role = me?.role as Role;
  const canWrite = canWriteLessons(role);
  const canSeePayroll = canSeeLessonPayroll(role);

  if (isLoading) return <PageLoading />;
  if (!lesson) return <Navigate to="/admin/lessons" replace />;

  const fields: DetailField<LessonFull>[] = [
    { key: 'id', label: 'ID' },
    { key: 'lesson_date', label: 'Дата', cell: (r) => fmtDate(r.lesson_date) },
    { key: 'lesson_number', label: 'Номер урока' },
    { key: 'lesson_type', label: 'Тип', cell: (r) => LESSON_TYPE_LABELS[r.lesson_type] || r.lesson_type },
    { key: 'group_name', label: 'Группа',
      cell: (r) => <EntityLink section="groups" id={r.group_id} text={r.group_name} /> },
    { key: 'teacher_name', label: 'Преподаватель',
      cell: (r) => <EntityLink section="teachers" id={r.teacher_id} text={r.teacher_name} /> },
    { key: 'original_teacher_name', label: 'Оригинальный препод',
      cell: (r) => <EntityLink section="teachers" id={r.original_teacher_id} text={r.original_teacher_name} /> },
    { key: 'lesson_duration_minutes', label: 'Длительность, мин' },
    { key: 'record_url', label: 'Запись', cell: (r) => r.record_url || '—' },
    { key: 'submitted_by_token', label: 'Токен' },
    { key: 'submitted_at', label: 'Создано', cell: (r) => fmtDate(r.submitted_at) },
  ];

  const handleDelete = async () => {
    try {
      await muts.remove.mutateAsync(lesson.id);
      toast('Урок удалён', 'ok');
      navigate('/admin/lessons');
    } catch (err) { showError(err); }
  };

  const toggleAttendance = async (sid: number, present: boolean) => {
    try {
      await muts.toggleAttendance.mutateAsync({ lessonId: lesson.id, studentId: sid, present });
      toast('Сохранено', 'ok');
    } catch (err) { showError(err); }
  };

  const updatePayrollField = async (field: 'total_students' | 'present_count' | 'payment' | 'penalty', value: number) => {
    if (!lesson.payroll) return;
    try {
      await payrollMuts.update.mutateAsync({ id: lesson.payroll.id, body: { [field]: value } });
      toast('Сохранено', 'ok');
    } catch (err) { showError(err); }
  };

  return (
    <DetailShell<LessonFull>
      title={`Урок ${fmtDate(lesson.lesson_date)} · ${lesson.group_name || ''}`}
      subtitle={`№${lesson.lesson_number} · ${lesson.teacher_name || ''}${lesson.lesson_type !== 'regular' ? ' · ' + (LESSON_TYPE_LABELS[lesson.lesson_type] || lesson.lesson_type) : ''}`}
      row={lesson}
      fields={fields}
      cardTitle="Данные урока"
      onDelete={canWrite ? handleDelete : undefined}
      deleteLabel="Удалить урок"
      backTo="/admin/lessons"
        parentLabel="Уроки"
    >
      <div className="detail__section">
        <h3 className="detail__section-title">Посещаемость</h3>
        {lesson.attendance.length === 0 ? (
          <div className="memberships__empty">Нет записей посещаемости</div>
        ) : (
          <>
            <div className="memberships__head">
              <div>Ученик</div><div>Был</div><div /><div />
            </div>
            {lesson.attendance.map((a) => (
              <div key={a.student_id} className="memberships__row">
                <div className="memberships__group">
                  <EntityLink section="students" id={a.student_id} text={a.student_name || `#${a.student_id}`} />
                </div>
                <div>
                  <label className="modal__check" style={{ margin: 0 }}>
                    <input
                      type="checkbox"
                      defaultChecked={a.present}
                      disabled={!canWrite || a.compensated}
                      onChange={(e) => { void toggleAttendance(a.student_id, e.target.checked); }}
                    />
                    <span className="modal__check-box" />
                  </label>
                </div>
                <div>
                  {a.compensated && (
                    <span className="status-badge status-badge--info"
                      title="Пропуск закрыт доп.уроком или сожжён — правка только через раздел «Доп.уроки»">
                      Компенсирован
                    </span>
                  )}
                </div>
                <div />
              </div>
            ))}
          </>
        )}
      </div>

      {canSeePayroll && (
        <div className="detail__section">
          <h3 className="detail__section-title">Зарплата</h3>
          {!lesson.payroll ? (
            <div className="memberships__empty">Зарплата для этого урока не создана</div>
          ) : (
            <div className="payroll-edit">
              <div className="field">
                <label htmlFor="payroll-total-students">Всего учеников</label>
                <input id="payroll-total-students" type="number" defaultValue={lesson.payroll.total_students}
                  onBlur={(e) => { void updatePayrollField('total_students', Number(e.target.value)); }} />
              </div>
              <div className="field">
                <label htmlFor="payroll-present-count">Было на уроке</label>
                <input id="payroll-present-count" type="number" defaultValue={lesson.payroll.present_count}
                  onBlur={(e) => { void updatePayrollField('present_count', Number(e.target.value)); }} />
              </div>
              <div className="field">
                <label htmlFor="payroll-payment">Оплата, ₽</label>
                <input id="payroll-payment" type="number" step={0.01} defaultValue={String(lesson.payroll.payment)}
                  onBlur={(e) => { void updatePayrollField('payment', Number(e.target.value)); }} />
              </div>
              <div className="field">
                <label htmlFor="payroll-penalty">Штраф, ₽</label>
                <input id="payroll-penalty" type="number" step={0.01} defaultValue={String(lesson.payroll.penalty)}
                  onBlur={(e) => { void updatePayrollField('penalty', Number(e.target.value)); }} />
              </div>
            </div>
          )}
        </div>
      )}
    </DetailShell>
  );
}
