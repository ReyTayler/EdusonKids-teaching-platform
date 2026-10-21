import { useState } from 'react';
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
import { fmtDate, fmtRub } from '../../lib/format';
import { Checkbox } from '../../components/form/Checkbox';
import { NumberInput } from '../../components/form/NumberInput';
import { StatusBadge } from '../../components/ui/StatusBadge';
import { LESSON_TYPE_LABELS } from '../../lib/labels';
import type { LessonFull } from '../../lib/types';

/** Чекбокс посещаемости с локальным состоянием: при отказе сервера (например,
 *  «Нельзя снять отметку с последнего пришедшего…») откатывается назад. */
function AttendanceBox({ present, disabled, onToggle }: {
  present: boolean; disabled: boolean; onToggle: (v: boolean) => Promise<boolean>;
}) {
  const [checked, setChecked] = useState(present);
  return (
    <Checkbox
      checked={checked}
      disabled={disabled}
      onChange={(e) => {
        const v = e.target.checked;
        setChecked(v);
        void onToggle(v).then((ok) => { if (!ok) setChecked(!v); });
      }}
    />
  );
}

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

  // Чекбокс держит своё состояние (AttendanceBox): при отказе сервера (например, «Нельзя снять отметку
  // с последнего пришедшего…») возвращаем его назад, иначе на экране останется
  // состояние, которого в базе нет.
  const toggleAttendance = async (sid: number, present: boolean): Promise<boolean> => {
    try {
      await muts.toggleAttendance.mutateAsync({ lessonId: lesson.id, studentId: sid, present });
      toast('Сохранено', 'ok');
      return true;
    } catch (err) {
      showError(err);
      return false;
    }
  };

  const updatePayrollField = async (field: 'payment' | 'penalty', box: HTMLInputElement, current: string | null) => {
    if (!lesson.payroll) return;
    // Без изменений ничего не шлём: PATCH сам по себе помечает начисление «вручную».
    const raw = box.value;
    if (raw === '' || Number(raw) === Number(current ?? 0)) return;
    try {
      await payrollMuts.update.mutateAsync({
        target: { kind: 'lesson', id: lesson.payroll.id }, body: { [field]: raw },
      });
      toast('Сохранено', 'ok');
    } catch (err) {
      // Поле неконтролируемое: возвращаем прежнее значение, иначе на экране
      // останется сумма, которой в базе нет.
      box.value = String(current ?? 0);
      showError(err);
    }
  };

  const resetPayroll = async () => {
    if (!lesson.payroll) return;
    try {
      await payrollMuts.reset.mutateAsync({ kind: 'lesson', id: lesson.payroll.id });
      toast('Сумма возвращена к расчётной', 'ok');
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
                  <AttendanceBox
                    present={a.present}
                    disabled={!canWrite || a.compensated}
                    onToggle={(v) => toggleAttendance(a.student_id, v)}
                  />
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
            <>
              <div className="payroll-edit">
                <div className="field">
                  <span className="label">Было на уроке</span>
                  <div>{lesson.payroll.present_count} из {lesson.payroll.total_students}</div>
                </div>
                <div className="field">
                  <label htmlFor="payroll-payment">Оплата, ₽</label>
                  <NumberInput id="payroll-payment" step={0.01} min={0}
                    key={`pay-${lesson.payroll.payment}`} defaultValue={String(lesson.payroll.payment)}
                    onBlur={(e) => { void updatePayrollField('payment', e.target, lesson.payroll!.payment); }} />
                </div>
                <div className="field">
                  <label htmlFor="payroll-penalty">Штраф, ₽</label>
                  <NumberInput id="payroll-penalty" step={0.01} min={0}
                    key={`pen-${lesson.payroll.penalty}`} defaultValue={String(lesson.payroll.penalty)}
                    onBlur={(e) => { void updatePayrollField('penalty', e.target, lesson.payroll!.penalty); }} />
                </div>
              </div>
              <div className="payroll-lesson-note">
                {lesson.payroll.is_manual && (
                  <p className="payroll-lesson-note__manual">
                    <StatusBadge tone="muted">вручную</StatusBadge>
                    {' '}расчёт системы: {fmtRub(lesson.payroll.calc_payment)}
                    {lesson.payroll.calc_penalty != null && Number(lesson.payroll.calc_penalty) > 0
                      ? `, штраф ${fmtRub(lesson.payroll.calc_penalty)}` : ''}
                    {' '}
                    <button type="button" className="btn-secondary" disabled={payrollMuts.reset.isPending}
                      onClick={() => { void resetPayroll(); }}>Сбросить к расчёту</button>
                  </p>
                )}
                <p className="payroll-note-full">
                  Как посчитано: {lesson.payroll.rule_note || '—'}
                </p>
              </div>
            </>
          )}
        </div>
      )}
    </DetailShell>
  );
}
