import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useGroupsAll } from '../../hooks/useGroups';
import { useMemberships } from '../../hooks/useMemberships';
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

interface Props {
  /** Предзаполнить группу/ученика (напр. с профиля) — опционально. */
  defaultGroupId?: number;
  defaultStudentId?: number;
  onClose: () => void;
}

const DURATION_OPTIONS = [30, 45, 60, 90].map((v) => ({ value: v, label: `${v} мин` }));

// Ручной доп.урок СВЕРХ курса (kind='extra'): не привязан к пропуску. Менеджер
// сам выбирает группу, учеников (участников группы), «за какой урок» (опц.).
// Кейс: переведённому ученику назначить N доп.уроков, чтобы догнать прогресс.
export function ManualExtraLessonModal({ defaultGroupId, defaultStudentId, onClose }: Props) {
  const { data: groups = [] } = useGroupsAll();
  const { data: teachers = [] } = useTeachers();
  const muts = useExtraLessonMutations();
  const { toast } = useToast();
  const showError = useApiError();

  const [groupId, setGroupId] = useState(defaultGroupId ?? 0);
  const { data: members = [] } = useMemberships({ group_id: groupId || undefined });

  const [teacherId, setTeacherId] = useState(0);
  const [date, setDate] = useState('');
  const [time, setTime] = useState('');
  const [duration, setDuration] = useState(60);
  const [lessonNumber, setLessonNumber] = useState(''); // «за какой урок», опц.
  const [selected, setSelected] = useState<Record<number, boolean>>(
    () => (defaultStudentId ? { [defaultStudentId]: true } : {}),
  );

  const group = useMemo(() => groups.find((g) => g.id === groupId), [groups, groupId]);

  // При выборе группы подставляем её преподавателя и длительность занятия по
  // умолчанию (менеджер может переопределить). Не трогаем выбор учеников.
  useEffect(() => {
    if (!group) return;
    setTeacherId((t) => (t ? t : group.teacher_id));
    setDuration(group.lesson_duration_minutes);
  }, [group]);

  const toggle = (sid: number) => setSelected((s) => ({ ...s, [sid]: !s[sid] }));

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!groupId) { toast('Выберите группу', 'error'); return; }
    const studentIds = members.filter((m) => selected[m.student_id]).map((m) => m.student_id);
    if (studentIds.length === 0) { toast('Выберите хотя бы одного ученика', 'error'); return; }
    if (!teacherId) { toast('Выберите преподавателя', 'error'); return; }
    if (!date || !time) { toast('Укажите дату и время доп.урока', 'error'); return; }
    const body: Record<string, unknown> = {
      group_id: groupId,
      teacher_id: teacherId,
      student_ids: studentIds,
      scheduled_date: date,
      scheduled_time: time,
      duration_minutes: duration,
    };
    const num = lessonNumber.trim();
    if (num) body.lesson_number = Number(num);
    try {
      const res = await muts.createManual.mutateAsync(body);
      toast(res.created > 1 ? `Назначено доп.уроков: ${res.created}` : 'Доп.урок назначен', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Назначить доп.урок вручную"
      footer={
        <button
          type="submit"
          form="manual-extra-lesson-form"
          className="btn-primary"
          disabled={muts.createManual.isPending}
        >
          Назначить
        </button>
      }
    >
      <form id="manual-extra-lesson-form" className="modal-form" onSubmit={(e) => { void handleSubmit(e); }}>
        <Field label="Группа" full>
          <SelectInput
            options={groups.map((g) => ({ value: g.id, label: g.name }))}
            value={groupId}
            placeholder="Выберите группу"
            onChange={(e) => { setGroupId(Number(e.target.value)); setSelected({}); }}
          />
        </Field>
        <Field label="Преподаватель" full>
          <SelectInput
            options={teachers.map((t) => ({ value: t.id, label: t.name }))}
            value={teacherId}
            placeholder="Выберите преподавателя"
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
        <Field label="За какой урок (номер, опц.)" full>
          <input
            type="number"
            min="0.5"
            step="0.5"
            value={lessonNumber}
            onChange={(e) => setLessonNumber(e.target.value)}
            placeholder="напр. 16 — если урок уже проведён, доп.урок привяжется к нему"
          />
          <div className="field-hint">
            Если урок №N в группе уже проведён — доп.урок привяжется к нему (нельзя
            назначить, если ученик на нём был). Если урока №N ещё нет — доп.урок сверх курса.
          </div>
        </Field>
        <Field label="Ученики группы" full>
          {!groupId ? (
            <div className="memberships__empty">Сначала выберите группу</div>
          ) : members.length === 0 ? (
            <div className="memberships__empty">В группе нет учеников</div>
          ) : members.map((m) => (
            <Checkbox
              key={m.student_id}
              label={m.student_name || `#${m.student_id}`}
              checked={!!selected[m.student_id]}
              onChange={() => toggle(m.student_id)}
            />
          ))}
        </Field>
      </form>
    </Dialog>
  );
}
