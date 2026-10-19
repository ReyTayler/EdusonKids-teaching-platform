// web/admin/src/pages/lessons/LessonFormModal.tsx
import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLessonMutations } from '../../hooks/useLessons';
import { useTeachers } from '../../hooks/useTeachers';
import { useGroupsAll } from '../../hooks/useGroups';
import { useMemberships } from '../../hooks/useMemberships';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { useAuth } from '../../hooks/useAuth';
import { canSeeLessonPayroll, type Role } from '../../lib/permissions';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { NumberInput } from '../../components/form/NumberInput';
import { DateInput } from '../../components/form/DateInput';
import { SelectInput } from '../../components/form/SelectInput';
import { Checkbox } from '../../components/form/Checkbox';
import { calcPayment } from '../../lib/pricing';
import type { LessonType } from '../../lib/types';

interface Props { onClose: () => void; }

export default function LessonFormModal({ onClose }: Props) {
  const navigate = useNavigate();
  const muts = useLessonMutations();
  const { data: teachers = [] } = useTeachers();
  const { data: groups = [] } = useGroupsAll();
  const { toast } = useToast();
  const showError = useApiError();
  const { me } = useAuth();
  const canSeePayroll = canSeeLessonPayroll(me?.role as Role);

  const [lessonDate, setLessonDate] = useState(new Date().toISOString().slice(0, 10));
  const [groupId, setGroupId] = useState<string>('');
  const [teacherId, setTeacherId] = useState<string>('');
  const [lessonNumber, setLessonNumber] = useState('1');
  const [lessonType, setLessonType] = useState<LessonType>('regular');
  const [originalTeacherId, setOriginalTeacherId] = useState<string>('');
  const [recordUrl, setRecordUrl] = useState('');
  const [payment, setPayment] = useState('0');
  const [penalty, setPenalty] = useState('0');

  const { data: members = [] } = useMemberships(groupId ? { group_id: Number(groupId) } : { group_id: 0 });
  const [present, setPresent] = useState<Record<number, boolean>>({});

  const toggle = (sid: number) => setPresent((p) => {
    const next = { ...p, [sid]: !(sid in p ? p[sid] : true) };
    const presentCount = members.filter((m) => (m.student_id in next ? next[m.student_id] : true)).length;
    setPayment(String(calcPayment(members.length, presentCount, false)));
    return next;
  });

  const isPresent = (sid: number) => sid in present ? present[sid] : true;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!groupId || !teacherId) {
      toast('Группа и преподаватель обязательны', 'error');
      return;
    }
    const attendance = members.map((m) => ({ student_id: m.student_id, present: isPresent(m.student_id) }));
    const presentCount = attendance.filter((a) => a.present).length;
    try {
      const created = await muts.create.mutateAsync({
        lesson_date: lessonDate,
        group_id: Number(groupId),
        teacher_id: Number(teacherId),
        lesson_number: Number(lessonNumber),
        lesson_duration_minutes: 90,
        lesson_type: lessonType,
        record_url: recordUrl || null,
        original_teacher_id: lessonType === 'substitution' && originalTeacherId
          ? Number(originalTeacherId) : null,
        submitted_by_token: 'admin-imported',
        attendance,
        ...(canSeePayroll
          ? {
              payroll: {
                total_students: members.length,
                present_count: presentCount,
                payment: Number(payment) || 0,
                penalty: Number(penalty) || 0,
              },
            }
          : {}),
      });
      toast('Урок создан', 'ok');
      onClose();
      navigate(`/admin/lessons/${created.id}`);
    } catch (err) { showError(err); }
  };

  const teacherOpts = [{ value: '', label: '— выберите —' }, ...teachers
    .filter((t) => t.active).map((t) => ({ value: t.id, label: t.name }))];
  const groupOpts = [{ value: '', label: '— выберите —' }, ...groups
    .filter((g) => g.active).map((g) => ({ value: g.id, label: g.name }))];

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      wide
      title="Новый урок"
      footer={
        <button type="submit" form="lesson-form" className="btn-save" disabled={muts.create.isPending}>
          Создать урок
        </button>
      }
    >
      <form id="lesson-form" className="modal-form" onSubmit={onSubmit}>
        <Field label="Дата" required>
          <DateInput required value={lessonDate} onChange={(e) => setLessonDate(e.target.value)} />
        </Field>
        <Field label="Группа" required>
          <SelectInput required value={groupId} onChange={(e) => setGroupId(e.target.value)} options={groupOpts} />
        </Field>
        <Field label="Преподаватель" required>
          <SelectInput required value={teacherId} onChange={(e) => setTeacherId(e.target.value)} options={teacherOpts} />
        </Field>
        <Field label="Номер урока" required>
          <NumberInput required step={0.5} min={0.5} value={lessonNumber} onChange={(e) => setLessonNumber(e.target.value)} />
        </Field>
        <Field label="Тип">
          <SelectInput
            value={lessonType}
            onChange={(e) => setLessonType(e.target.value as LessonType)}
            options={[
              { value: 'regular', label: 'Обычный' },
              { value: 'substitution', label: 'Замена' },
              { value: 'reschedule', label: 'Перенос' },
            ]}
          />
        </Field>
        {lessonType === 'substitution' && (
          <Field label="Оригинальный препод.">
            <SelectInput value={originalTeacherId} onChange={(e) => setOriginalTeacherId(e.target.value)} options={teacherOpts} />
          </Field>
        )}
        <Field label="Ссылка на запись" full>
          <TextInput value={recordUrl} onChange={(e) => setRecordUrl(e.target.value)} placeholder="https://..." />
        </Field>

        {groupId && (
          <>
            <h4 className="memberships__title">Посещаемость</h4>
            {members.length === 0 ? (
              <div className="memberships__empty">В группе нет учеников</div>
            ) : members.map((m) => (
              <div key={m.student_id} className="memberships__row">
                <div className="memberships__group">{m.student_name || `#${m.student_id}`}</div>
                <div>
                  <label className="modal__check" style={{ margin: 0 }}>
                    <Checkbox checked={isPresent(m.student_id)} onChange={() => toggle(m.student_id)} />
                    <span className="modal__check-box" />
                  </label>
                </div>
                <div /><div />
              </div>
            ))}

            {canSeePayroll && (
              <>
                <h4 className="memberships__title">Зарплата</h4>
                <div className="memberships__row">
                  <div>Оплата ₽</div>
                  <NumberInput step={0.01} value={payment} onChange={(e) => setPayment(e.target.value)} />
                  <div>Штраф ₽</div>
                  <NumberInput step={0.01} value={penalty} onChange={(e) => setPenalty(e.target.value)} />
                </div>
              </>
            )}
          </>
        )}
      </form>
    </Dialog>
  );
}
