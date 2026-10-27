import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTeacherMutations } from '../../hooks/useTeachers';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { Checkbox } from '../../components/form/Checkbox';
import type { Teacher } from '../../lib/types';

interface Props { initial: Teacher | null; onClose: () => void; }

export default function TeacherFormModal({ initial, onClose }: Props) {
  const isNew = !initial;
  const navigate = useNavigate();
  const muts = useTeacherMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [name, setName] = useState(initial?.name || '');
  const [email, setEmail] = useState(initial?.email || '');
  const [phone, setPhone] = useState(initial?.phone || '');
  const [callUrl, setCallUrl] = useState(initial?.call_url || '');
  const [vkUrl, setVkUrl] = useState(initial?.vk_url || '');
  const [active, setActive] = useState(initial?.active ?? true);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const body: Partial<Teacher> = { name, email: email || null, phone: phone || null,
      call_url: callUrl.trim() || null, vk_url: vkUrl.trim() || null };
    if (!isNew) body.active = active;
    try {
      if (isNew) {
        const created = await muts.create.mutateAsync(body);
        toast('Создано', 'ok');
        onClose();
        navigate(`/admin/teachers/${created.id}`);
      } else {
        await muts.update.mutateAsync({ id: initial!.id, body });
        toast('Сохранено', 'ok');
        onClose();
      }
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      wide
      title={isNew ? 'Новый преподаватель' : `Редактировать: ${initial!.name}`}
      footer={
        <button type="submit" form="teacher-form" className="btn-primary"
          disabled={muts.create.isPending || muts.update.isPending}>Сохранить</button>
      }
    >
      <form id="teacher-form" className="modal-form" onSubmit={onSubmit}>
        <Field label="Имя" required full>
          <TextInput required value={name} onChange={(e) => setName(e.target.value)} placeholder="Иванов Алексей" />
        </Field>
        <Field label="Email">
          <TextInput type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="teacher@example.com" />
        </Field>
        <Field label="Телефон">
          <TextInput value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="+7 (___) ___-__-__" />
        </Field>
        <Field label="Ссылка на звонок" full>
          <TextInput type="url" value={callUrl} onChange={(e) => setCallUrl(e.target.value)}
            placeholder="https://telemost.yandex.ru/j/…" />
        </Field>
        <Field label="VK" full>
          <TextInput type="url" value={vkUrl} onChange={(e) => setVkUrl(e.target.value)}
            placeholder="https://vk.com/…" />
        </Field>
        {!isNew && (
          <Field label="Активен">
            <Checkbox checked={active} onChange={(e) => setActive(e.target.checked)} />
          </Field>
        )}
      </form>
    </Dialog>
  );
}
