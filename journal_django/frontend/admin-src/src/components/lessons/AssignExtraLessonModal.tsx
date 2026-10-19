import { useState, type FormEvent } from 'react';
import { useTeachers } from '../../hooks/useTeachers';
import { useExtraLessonMutations } from '../../hooks/useExtraLessons';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../ui/Toast';
import { Dialog } from '../ui/Dialog';
import { Field } from '../form/Field';
import { SelectInput } from '../form/SelectInput';
import { DateInput } from '../form/DateInput';
import { TimeInput } from '../form/TimeInput';
import { Checkbox } from '../form/Checkbox';

interface Candidate {
  student_id: number;
  student_name: string;
}

interface Props {
  missedLessonId: number;
  candidates: Candidate[];
  defaultTeacherId: number;
  onClose: () => void;
}

const DURATION_OPTIONS = [30, 45, 60, 90].map((v) => ({ value: v, label: `${v} мин` }));

// v1: кандидаты — только ученики, отмеченные отсутствующими на этом уроке
// (present: false). Ручное добавление других учеников API уже поддерживает
// (student_ids принимает любые id), но в этой форме пока не реализовано —
// осознанное упрощение, а не пропуск (см. план задачи 13).
export function AssignExtraLessonModal({ missedLessonId, candidates, defaultTeacherId, onClose }: Props) {
  const { data: teachers = [] } = useTeachers();
  const muts = useExtraLessonMutations();
  const { toast } = useToast();
  const showError = useApiError();

  const [teacherId, setTeacherId] = useState(defaultTeacherId);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [duration, setDuration] = useState(45);
  const [selected, setSelected] = useState<Record<number, boolean>>(
    () => Object.fromEntries(candidates.map((c) => [c.student_id, true])),
  );

  const toggle = (sid: number) => setSelected((s) => ({ ...s, [sid]: !s[sid] }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const studentIds = candidates.filter((c) => selected[c.student_id]).map((c) => c.student_id);
    if (studentIds.length === 0) {
      toast('Выберите хотя бы одного ученика', 'error');
      return;
    }
    if (!date || !time) {
      toast('Укажите дату и время доп.урока', 'error');
      return;
    }
    try {
      const res = await muts.create.mutateAsync({
        missed_lesson_id: missedLessonId,
        teacher_id: teacherId,
        student_ids: studentIds,
        scheduled_date: date,
        scheduled_time: time,
        duration_minutes: duration,
      });
      toast(res.created > 1 ? `Назначено доп.уроков: ${res.created}` : 'Доп.урок назначен', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Назначить доп.урок"
      footer={
        <button
          type="submit"
          form="assign-extra-lesson-form"
          className="btn-primary"
          disabled={muts.create.isPending}
        >
          Назначить
        </button>
      }
    >
      <form id="assign-extra-lesson-form" className="modal-form" onSubmit={(e) => { void handleSubmit(e); }}>
        <Field label="Преподаватель" full>
          <SelectInput
            options={teachers.map((t) => ({ value: t.id, label: t.name }))}
            value={teacherId}
            onChange={(e) => setTeacherId(Number(e.target.value))}
          />
        </Field>
        <Field label="Дата">
          <DateInput value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="Время">
          <TimeInput value={time} onChange={(e) => setTime(e.target.value)} />
        </Field>
        <Field label="Длительность">
          <SelectInput
            options={DURATION_OPTIONS}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
          />
        </Field>
        <Field label="Ученики (отсутствовавшие на этом уроке)" full>
          {candidates.length === 0 ? (
            <div className="memberships__empty">Нет отсутствовавших учеников</div>
          ) : candidates.map((c) => (
            <Checkbox
              key={c.student_id}
              label={c.student_name}
              checked={!!selected[c.student_id]}
              onChange={() => toggle(c.student_id)}
            />
          ))}
        </Field>
      </form>
    </Dialog>
  );
}
