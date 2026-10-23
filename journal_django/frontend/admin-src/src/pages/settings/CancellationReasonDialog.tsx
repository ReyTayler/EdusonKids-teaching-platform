import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';
import { useCancellationReasonMutations } from '../../hooks/useCancellationReasons';
import { CANCELLATION_LABELS, CANCELLATION_SIDE_LABELS } from '../../lib/labels';
import type { CancellationReason, CancellationSide } from '../../lib/types';
import { parseServerErrors } from './motivationFormat';

const NAME_MAX = 200;

/** Та же нормализация, что на сервере: без невидимых символов (Cf) и с одиночными пробелами. */
const normalize = (s: string) => s.replace(/\p{Cf}/gu, '').split(/\s+/).filter(Boolean).join(' ');

interface Props {
  /** Сторона задана блоком, из которого открыт диалог; у правки — стороной причины. */
  side: CancellationSide;
  editing: CancellationReason | null;
  onClose: () => void;
}

/** Создание и переименование причины отмены. Сторону менять нельзя — сервер не даёт. */
export function CancellationReasonDialog({ side, editing, onClose }: Props) {
  const { create, update } = useCancellationReasonMutations();
  const [name, setName] = useState(editing?.name ?? '');
  const [errors, setErrors] = useState<{ name?: string; general: string }>({ general: '' });
  const pending = create.isPending || update.isPending;
  const trimmed = name.trim();

  const submit = () => {
    if (!trimmed || pending) return;
    // Имя не менялось — шлём ничего: пустой PATCH сервер отклонил бы («Нечего менять»).
    if (editing && normalize(name) === normalize(editing.name)) { onClose(); return; }
    const onError = (e: unknown) => {
      const p = parseServerErrors(e, ['name']);
      setErrors({ name: p.fields.name, general: p.general });
    };
    if (editing) update.mutate({ id: editing.id, body: { name: trimmed } }, { onSuccess: onClose, onError });
    else create.mutate({ side, name: trimmed }, { onSuccess: onClose, onError });
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !pending && onClose()}
      title={`${editing ? CANCELLATION_LABELS.editReasonTitle : CANCELLATION_LABELS.newReasonTitle} · ${CANCELLATION_SIDE_LABELS[side]}`}
      footer={
        <div className="settings-actions">
          <button type="button" className="btn-cancel" onClick={onClose} disabled={pending}>
            {CANCELLATION_LABELS.cancel}
          </button>
          <button type="button" className="btn-add" onClick={submit} disabled={pending || !trimmed}>
            {pending ? CANCELLATION_LABELS.saving : CANCELLATION_LABELS.save}
          </button>
        </div>
      }
    >
      <div className="modal-form">
        {errors.general && <div className="field-error">{errors.general}</div>}
        <Field label={CANCELLATION_LABELS.reasonName} required error={errors.name} full>
          <TextInput
            value={name}
            maxLength={NAME_MAX}
            autoFocus
            onChange={(e) => { setName(e.target.value); setErrors({ general: '' }); }}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit(); } }}
            aria-label={CANCELLATION_LABELS.reasonName}
          />
        </Field>
      </div>
    </Dialog>
  );
}
