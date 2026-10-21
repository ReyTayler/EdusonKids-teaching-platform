import { useMemo, useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { Combobox } from '../../components/form/Combobox';
import { Checkbox } from '../../components/form/Checkbox';
import { NumberInput } from '../../components/form/NumberInput';
import { TextInput } from '../../components/form/TextInput';
import { SelectInput } from '../../components/form/SelectInput';
import { useToast } from '../../components/ui/Toast';
import { useTeachers } from '../../hooks/useTeachers';
import { useGroupsAll } from '../../hooks/useGroups';
import { usePersonalRateMutations } from '../../hooks/useMotivation';
import { PERSONAL_RATE_KIND_LABELS } from '../../lib/labels';
import type { PersonalRate, PersonalRateKind, PersonalRatePayload } from '../../lib/types';
import { norm, parseServerErrors, savedMessage, toField, type ServerErrors } from './motivationFormat';

interface Props {
  editing: PersonalRate | null;
  onClose: () => void;
}

const KIND_OPTIONS = (Object.keys(PERSONAL_RATE_KIND_LABELS) as PersonalRateKind[])
  .map((k) => ({ value: k, label: PERSONAL_RATE_KIND_LABELS[k] }));

const FIELDS = ['teacher_id', 'group_id', 'lesson_from', 'lesson_to', 'kind', 'value', 'note'];

export function PersonalRateDialog({ editing, onClose }: Props) {
  const { toast } = useToast();
  const { create, update } = usePersonalRateMutations();
  const teachers = useTeachers(true);
  const groups = useGroupsAll(true);

  const [teacherId, setTeacherId] = useState(editing ? String(editing.teacher_id) : '');
  const [groupId, setGroupId] = useState(editing ? String(editing.group_id) : '');
  const [allLessons, setAllLessons] = useState(editing ? editing.lesson_from == null && editing.lesson_to == null : true);
  const [from, setFrom] = useState(toField(editing?.lesson_from));
  const [to, setTo] = useState(toField(editing?.lesson_to));
  const [kind, setKind] = useState<PersonalRateKind>(editing?.kind ?? 'fixed');
  const [value, setValue] = useState(toField(editing?.value));
  const [note, setNote] = useState(editing?.note ?? '');
  const [errors, setErrors] = useState<ServerErrors>({ fields: {}, directions: {}, general: '' });

  // Архивные и уволенные остаются в списках приглушёнными: иначе при правке
  // старой ставки поле было бы пустым.
  const teacherOptions = useMemo(
    () => (teachers.data ?? []).map((t) => ({ value: String(t.id), label: t.name, muted: !t.active })),
    [teachers.data],
  );
  // Доступны все группы (замена в чужой группе — законный случай), но группы
  // выбранного преподавателя идут первыми.
  const groupOptions = useMemo(() => {
    const mine = (g: { teacher_id: number }) => (teacherId && String(g.teacher_id) === teacherId ? 0 : 1);
    return [...(groups.data ?? [])]
      .sort((a, b) => mine(a) - mine(b))
      .map((g) => ({ value: String(g.id), label: g.name, muted: !g.active }));
  }, [groups.data, teacherId]);

  const pending = create.isPending || update.isPending;

  const submit = () => {
    const fields: Record<string, string> = {};
    if (!teacherId) fields.teacher_id = 'Выберите преподавателя';
    if (!groupId) fields.group_id = 'Выберите группу';
    if (!norm(value)) fields.value = 'Укажите значение';
    if (!allLessons && !norm(from)) fields.lesson_from = 'Укажите урок';
    if (!allLessons && !norm(to)) fields.lesson_to = 'Укажите урок';
    if (Object.keys(fields).length) { setErrors({ fields, directions: {}, general: '' }); return; }

    const body: PersonalRatePayload = {
      teacher_id: Number(teacherId),
      group_id: Number(groupId),
      lesson_from: allLessons ? null : norm(from),
      lesson_to: allLessons ? null : norm(to),
      kind,
      value: norm(value),
      note: note.trim(),
    };
    const onSuccess = (res: { recalculated?: number }) => {
      toast(savedMessage(res.recalculated), 'ok');
      onClose();
    };
    const onError = (e: unknown) => setErrors(parseServerErrors(e, FIELDS));
    if (editing) update.mutate({ id: editing.id, body }, { onSuccess, onError });
    else create.mutate(body, { onSuccess, onError });
  };

  const fe = errors.fields;
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !pending && onClose()}
      title={editing ? 'Изменить персональную ставку' : 'Новая персональная ставка'}
      footer={
        <div className="settings-actions">
          <button type="button" className="btn-cancel" onClick={onClose} disabled={pending}>Отмена</button>
          <button type="button" className="btn-add" onClick={submit} disabled={pending}>
            {pending ? 'Сохраняем…' : 'Сохранить'}
          </button>
        </div>
      }
    >
      <div className="modal-form">
        {errors.general && <div className="field-error">{errors.general}</div>}

        <Field label="Преподаватель" required error={fe.teacher_id} full>
          <Combobox value={teacherId} options={teacherOptions} placeholder="Выберите преподавателя"
            aria-label="Преподаватель" onChange={setTeacherId} />
        </Field>
        <Field label="Группа" required error={fe.group_id} full>
          <Combobox value={groupId} options={groupOptions} placeholder="Выберите группу"
            aria-label="Группа" onChange={setGroupId} />
        </Field>

        <div className="settings-block">
          <Checkbox label="Все уроки группы" checked={allLessons}
            onChange={(e) => setAllLessons(e.target.checked)} />
          {!allLessons && (
            <div className="settings-inline">
              <Field label="С урока" required error={fe.lesson_from}>
                <NumberInput min={0.5} step="0.5" value={from} onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="По урок" required error={fe.lesson_to}>
                <NumberInput min={0.5} step="0.5" value={to} onChange={(e) => setTo(e.target.value)} />
              </Field>
            </div>
          )}
        </div>

        <Field label="Вид" required error={fe.kind}>
          <SelectInput options={KIND_OPTIONS} value={kind}
            onChange={(e) => setKind(e.target.value as PersonalRateKind)} />
        </Field>
        <Field label={kind === 'plus_percent' ? 'Значение, %' : 'Значение, ₽'} required error={fe.value}>
          <NumberInput min={0} step="0.01" value={value} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <Field label="Примечание" error={fe.note} full>
          <TextInput value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
