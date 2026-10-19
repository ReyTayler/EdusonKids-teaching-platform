import { useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { useDirectionMutations } from '../../hooks/useDirections';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { NumberInput } from '../../components/form/NumberInput';
import { ColorField } from '../../components/form/ColorField';
import type { Direction } from '../../lib/types';

interface Props { initial: Direction | null; onClose: () => void; }

export default function DirectionFormModal({ initial, onClose }: Props) {
  const isNew = !initial;
  const navigate = useNavigate();
  const muts = useDirectionMutations();
  const { toast } = useToast();
  const showError = useApiError();
  const [name, setName] = useState(initial?.name || '');
  const [totalLessons, setTotalLessons] = useState<string>(
    initial?.total_lessons != null ? String(initial.total_lessons) : '',
  );
  const [color, setColor] = useState(initial?.color || '#0d9488');
  const [subscriptionPrice, setSubscriptionPrice] = useState<string>(
    initial?.subscription_price != null ? String(initial.subscription_price) : '',
  );

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    const body: Partial<Direction> = {
      name,
      total_lessons: totalLessons === '' ? null : Number(totalLessons),
      subscription_price: subscriptionPrice.trim() === '' ? null : Number(subscriptionPrice),
      color: color || null,
    };

    try {
      if (isNew) {
        const created = await muts.create.mutateAsync(body);
        toast('Создано', 'ok');
        onClose();
        navigate(`/admin/directions/${created.id}`);
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
      title={isNew ? 'Новое направление' : `Редактировать: ${initial!.name}`}
      footer={
        <button type="submit" form="direction-form" className="btn-save"
          disabled={muts.create.isPending || muts.update.isPending}>Сохранить</button>
      }
    >
      <form id="direction-form" className="modal-form" onSubmit={onSubmit}>
        <Field label="Название" required full>
          <TextInput required value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Уроков на направление">
          <NumberInput min={0} value={totalLessons} onChange={(e) => setTotalLessons(e.target.value)} placeholder="например, 36" />
        </Field>
        <Field label="Цена за абонемент (₽)">
          <NumberInput
            value={subscriptionPrice}
            min={0}
            step="0.01"
            onChange={(e) => setSubscriptionPrice(e.target.value)}
            placeholder="не настроена"
          />
        </Field>
        <Field label="Цвет направления" full>
          <ColorField value={color} onChange={setColor} />
        </Field>
      </form>
    </Dialog>
  );
}
