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
import { useTeacherGroups } from '../../hooks/useGroups';
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

  const [teacherId, setTeacherId] = useState(editing ? String(editing.teacher_id) : '');
  const [groupId, setGroupId] = useState(editing ? String(editing.group_id) : '');
  const groups = useTeacherGroups(teacherId ? Number(teacherId) : null);
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
  // Только группы выбранного преподавателя (решение владельца 2026-10-21) — их
  // отдаёт сервер (filter[teacher_id]); он же отклонит чужую группу. Группа уже
  // сохранённой ставки остаётся в списке, даже если сейчас её ведёт другой, —
  // иначе поле при правке было бы пустым (сервер такую неизменённую пару пускает).
  const groupOptions = useMemo(() => {
    if (!teacherId) return [];
    const options = (groups.data ?? []).map((g) => ({ value: String(g.id), label: g.name, muted: !g.active }));
    if (editing && String(editing.teacher_id) === teacherId
        && !options.some((o) => o.value === String(editing.group_id))) {
      options.unshift({ value: String(editing.group_id), label: editing.group_name, muted: true });
    }
    return options;
  }, [groups.data, teacherId, editing]);

  // Сменили преподавателя — выбранная группа сбрасывается (у другого она не в списке;
  // своя группа сохранённой ставки возвращается вместе с её преподавателем).
  const pickTeacher = (id: string) => {
    setTeacherId(id);
    setGroupId(editing && String(editing.teacher_id) === id ? String(editing.group_id) : '');
  };

  const pending = create.isPending || update.isPending;
  // Выключенная ставка: уроки, вид и значение править нельзя (сервер ответит 400) — только примечание.
  const locked = !!editing && !editing.is_active;

  const submit = () => {
    const fields: Record<string, string> = {};
    if (editing && locked) {
      // Только примечание: период не создаётся, параметры не шлём.
      update.mutate({ id: editing.id, body: { note: note.trim() } }, {
        onSuccess: (res) => { toast(savedMessage(res.recalculated), 'ok'); onClose(); },
        onError: (e) => setErrors(parseServerErrors(e, FIELDS)),
      });
      return;
    }
    if (!teacherId) fields.teacher_id = 'Выберите преподавателя';
    if (!groupId) fields.group_id = 'Выберите группу';
    if (!norm(value)) fields.value = 'Укажите значение';
    if (!allLessons && !norm(from)) fields.lesson_from = 'Укажите урок';
    if (!allLessons && !norm(to)) fields.lesson_to = 'Укажите урок';
    if (Object.keys(fields).length) { setErrors({ fields, directions: {}, general: '' }); return; }

    // Правка: преподаватель и группа не меняются, на сервер идут только параметры и примечание.
    const params: Omit<PersonalRatePayload, 'teacher_id' | 'group_id'> = {
      lesson_from: allLessons ? null : norm(from),
      lesson_to: allLessons ? null : norm(to),
      kind,
      value: norm(value),
      note: note.trim(),
    };
    const body: PersonalRatePayload = { teacher_id: Number(teacherId), group_id: Number(groupId), ...params };
    const onSuccess = (res: { recalculated?: number }) => {
      toast(savedMessage(res.recalculated), 'ok');
      onClose();
    };
    const onError = (e: unknown) => setErrors(parseServerErrors(e, FIELDS));
    if (editing) update.mutate({ id: editing.id, body: params }, { onSuccess, onError });
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

        {editing ? (
          <>
            <p className="field-hint">
              Изменения действуют с сегодняшнего дня, прошлые начисления не меняются.
            </p>
            <Field label="Преподаватель" full>
              <TextInput value={editing.teacher_name} readOnly disabled aria-label="Преподаватель" />
            </Field>
            <Field label="Группа" full>
              <TextInput value={editing.group_name} readOnly disabled aria-label="Группа" />
            </Field>
            {locked && (
              <p className="field-hint field-hint--warn">
                Ставка выключена. Включите её переключателем в списке, чтобы изменить уроки, вид или значение;
                здесь можно поправить только примечание.
              </p>
            )}
          </>
        ) : (
          <>
            <Field label="Преподаватель" required error={fe.teacher_id} full>
              <Combobox value={teacherId} options={teacherOptions} placeholder="Выберите преподавателя"
                aria-label="Преподаватель" onChange={pickTeacher} />
            </Field>
            <Field label="Группа" required error={fe.group_id} full>
              <Combobox value={groupId} options={groupOptions}
                placeholder={teacherId ? 'Выберите группу преподавателя' : 'Сначала выберите преподавателя'}
                aria-label="Группа" onChange={setGroupId} />
            </Field>
          </>
        )}

        <div className="settings-block">
          <Checkbox label="Все уроки группы" checked={allLessons} disabled={locked}
            onChange={(e) => setAllLessons(e.target.checked)} />
          {!allLessons && (
            <div className="settings-inline">
              <Field label="С урока" required error={fe.lesson_from}>
                <NumberInput min={0.5} step="0.5" value={from} disabled={locked} onChange={(e) => setFrom(e.target.value)} />
              </Field>
              <Field label="По урок" required error={fe.lesson_to}>
                <NumberInput min={0.5} step="0.5" value={to} disabled={locked} onChange={(e) => setTo(e.target.value)} />
              </Field>
            </div>
          )}
        </div>

        <Field label="Вид" required error={fe.kind}>
          <SelectInput options={KIND_OPTIONS} value={kind} disabled={locked}
            onChange={(e) => setKind(e.target.value as PersonalRateKind)} />
        </Field>
        <Field label={kind === 'plus_percent' ? 'Значение, %' : 'Значение, ₽'} required error={fe.value}>
          <NumberInput min={0} step="0.01" value={value} disabled={locked} onChange={(e) => setValue(e.target.value)} />
        </Field>
        <Field label="Примечание" error={fe.note} full>
          <TextInput value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
