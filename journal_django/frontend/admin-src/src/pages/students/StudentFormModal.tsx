import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useStudentMutations } from '../../hooks/useStudents';
import { useApiError } from '../../hooks/useApiError';
import { ApiError } from '../../lib/api';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { DateInput } from '../../components/form/DateInput';
import type { Student } from '../../lib/types';

interface FormState {
  full_name: string;
  birth_date: string;
  phone: string;
  telegram: string;
  email: string;
  parent1_name: string;
  parent1_phone: string;
  parent1_email: string;
  parent1_telegram: string;
  parent2_name: string;
  parent2_phone: string;
  parent2_email: string;
  platform_id: string;
  crm_link: string;
}

const toYMD = (v: string | null | undefined): string => (v ? String(v).slice(0, 10) : '');

const FORM_KEYS = toFormKeys();

function toFormKeys(): Record<string, true> {
  return Object.fromEntries(Object.keys(toForm(null)).map((k) => [k, true]));
}

function toForm(s: Student | null): FormState {
  return {
    full_name: s?.full_name || '',
    birth_date: toYMD(s?.birth_date),
    phone: s?.phone || '',
    telegram: s?.telegram || '',
    email: s?.email || '',
    parent1_name: s?.parent1_name || '',
    parent1_phone: s?.parent1_phone || '',
    parent1_email: s?.parent1_email || '',
    parent1_telegram: s?.parent1_telegram || '',
    parent2_name: s?.parent2_name || '',
    parent2_phone: s?.parent2_phone || '',
    parent2_email: s?.parent2_email || '',
    platform_id: s?.platform_id || '',
    crm_link: s?.crm_link || '',
  };
}

/** Достаёт из 400-ответа ошибки по известным полям формы; нет ни одной — null (показываем тост). */
function pickFieldErrors(err: unknown): { fields: Partial<Record<keyof FormState, string>>; hasOther: boolean } | null {
  if (!(err instanceof ApiError) || !err.details || typeof err.details !== 'object') return null;
  let hasOther = false; // ошибки вне формы (non_field_errors и т.п.) — их тоже надо показать
  const out: Partial<Record<keyof FormState, string>> = {};
  for (const [k, v] of Object.entries(err.details as Record<string, unknown>)) {
    if (!(k in FORM_KEYS)) { hasOther = true; continue; }
    const text = Array.isArray(v) ? v.map(String).join(' ') : typeof v === 'string' ? v : '';
    if (text) out[k as keyof FormState] = text;
  }
  return Object.keys(out).length ? { fields: out, hasOther } : null;
}

interface Props { initial: Student | null; onClose: () => void; }

export default function StudentFormModal({ initial, onClose }: Props) {
  const isNew = !initial;
  const navigate = useNavigate();
  const muts = useStudentMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [form, setForm] = useState<FormState>(() => toForm(initial));
  // Ошибки валидации сервера по полям ({details: {поле: [текст]}}) — под самим полем,
  // чтобы человек видел, какая именно почта или ссылка не подошла.
  const [errors, setErrors] = useState<Partial<Record<keyof FormState, string>>>({});

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();

    setErrors({});
    const body: Partial<Student> = {
      full_name: form.full_name,
      birth_date: form.birth_date || null,
      phone: form.phone || null,
      telegram: form.telegram || null,
      email: form.email || null,
      parent1_name: form.parent1_name || null,
      parent1_phone: form.parent1_phone || null,
      parent1_email: form.parent1_email || null,
      parent1_telegram: form.parent1_telegram || null,
      parent2_name: form.parent2_name || null,
      parent2_phone: form.parent2_phone || null,
      parent2_email: form.parent2_email || null,
      platform_id: form.platform_id || null,
      crm_link: form.crm_link || null,
    };

    try {
      let resultId: number;
      if (isNew) {
        const created = await muts.create.mutateAsync(body);
        toast('Создано', 'ok');
        resultId = created.id;
      } else {
        const updated = await muts.update.mutateAsync({ id: initial!.id, body });
        toast('Сохранено', 'ok');
        resultId = updated.id;
      }

      onClose();
      if (isNew) navigate(`/admin/students/${resultId}`);
    } catch (err) {
      const fieldErrors = pickFieldErrors(err);
      if (fieldErrors) setErrors(fieldErrors.fields);
      if (!fieldErrors || fieldErrors.hasOther) showError(err);
    }
  };

  const set = <K extends keyof FormState>(k: K, v: FormState[K]) => {
    setForm((f) => ({ ...f, [k]: v }));
    setErrors((er) => (er[k] ? { ...er, [k]: undefined } : er));
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      wide
      title={isNew ? 'Новый ученик' : `Редактировать: ${initial!.full_name}`}
      footer={
        <button
          type="submit"
          form="student-form"
          className="btn-add"
          disabled={muts.create.isPending || muts.update.isPending}
        >Сохранить</button>
      }
    >
      <form id="student-form" className="modal-form" onSubmit={onSubmit}>
        <div className="modal-section-label">Личные данные</div>
        <Field label="Имя ученика" required full error={errors.full_name}>
          <TextInput required value={form.full_name} onChange={(e) => set('full_name', e.target.value)} placeholder="Например, Миша Иванов" />
        </Field>
        {/* Возраст не вводится — он вычисляется из даты рождения. */}
        <Field label="Дата рождения" error={errors.birth_date}>
          <DateInput value={form.birth_date} onChange={(e) => set('birth_date', e.target.value)} />
        </Field>

        <div className="modal-section-label">Контакты ученика</div>
        <Field label="Телефон ученика" error={errors.phone}>
          <TextInput value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+7 (___) ___-__-__" />
        </Field>
        <Field label="Telegram ученика" error={errors.telegram}>
          <TextInput value={form.telegram} onChange={(e) => set('telegram', e.target.value)} placeholder="@username" />
        </Field>
        <Field label="Почта ученика" error={errors.email}>
          <TextInput type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="student@example.com" />
        </Field>

        <div className="modal-section-label">Родитель 1</div>
        <Field label="Имя родителя 1" error={errors.parent1_name}>
          <TextInput value={form.parent1_name} onChange={(e) => set('parent1_name', e.target.value)} />
        </Field>
        <Field label="Телефон родителя 1" error={errors.parent1_phone}>
          <TextInput value={form.parent1_phone} onChange={(e) => set('parent1_phone', e.target.value)} placeholder="+7 (___) ___-__-__" />
        </Field>
        <Field label="Почта родителя 1" error={errors.parent1_email}>
          <TextInput type="email" value={form.parent1_email} onChange={(e) => set('parent1_email', e.target.value)} placeholder="parent@example.com" />
        </Field>
        <Field label="Telegram родителя 1" error={errors.parent1_telegram}>
          <TextInput value={form.parent1_telegram} onChange={(e) => set('parent1_telegram', e.target.value)} placeholder="@username" />
        </Field>

        <div className="modal-section-label">Родитель 2</div>
        <Field label="Имя родителя 2" error={errors.parent2_name}>
          <TextInput value={form.parent2_name} onChange={(e) => set('parent2_name', e.target.value)} />
        </Field>
        <Field label="Телефон родителя 2" error={errors.parent2_phone}>
          <TextInput value={form.parent2_phone} onChange={(e) => set('parent2_phone', e.target.value)} placeholder="+7 (___) ___-__-__" />
        </Field>
        <Field label="Почта родителя 2" error={errors.parent2_email}>
          <TextInput type="email" value={form.parent2_email} onChange={(e) => set('parent2_email', e.target.value)} placeholder="parent@example.com" />
        </Field>

        <div className="modal-section-label">Система</div>
        <Field label="Platform ID" error={errors.platform_id}>
          <TextInput value={form.platform_id} onChange={(e) => set('platform_id', e.target.value)} placeholder="внешний идентификатор" />
        </Field>
        <Field label="Ссылка в CRM" error={errors.crm_link}>
          <TextInput type="url" value={form.crm_link} onChange={(e) => set('crm_link', e.target.value)} placeholder="https://..." />
        </Field>
      </form>
    </Dialog>
  );
}
