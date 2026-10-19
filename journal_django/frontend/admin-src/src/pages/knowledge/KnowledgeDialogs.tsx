import { useEffect, useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { TextInput } from '../../components/form/TextInput';

/**
 * Диалоги раздела «Wiki».
 *
 * Заменяют window.prompt/confirm: браузерные окна нельзя оформить, они рвут
 * визуальный ряд админки, блокируют поток и на части браузеров показывают
 * пугающее «Ввод данных на сайте …». Здесь — обычная модалка проекта на
 * Radix (фокус-ловушка, Escape, клик вне уже внутри Dialog).
 */

interface NameDialogProps {
  open: boolean;
  title: string;
  label: string;
  /** Подпись кнопки подтверждения: «Создать», «Переименовать». */
  submitLabel: string;
  initialValue?: string;
  placeholder?: string;
  hint?: string;
  busy?: boolean;
  onSubmit: (value: string) => void;
  onClose: () => void;
}

/** Модалка с одним текстовым полем — создание и переименование. */
export function NameDialog({
  open,
  title,
  label,
  submitLabel,
  initialValue = '',
  placeholder,
  hint,
  busy = false,
  onSubmit,
  onClose,
}: NameDialogProps) {
  const [value, setValue] = useState(initialValue);

  // Значение сбрасывается на каждое открытие: иначе после переименования одного
  // раздела в поле осталось бы название предыдущего.
  useEffect(() => {
    if (open) setValue(initialValue);
  }, [open, initialValue]);

  const trimmed = value.trim();
  const canSubmit = trimmed.length > 0 && !busy;

  const submit = () => {
    if (!canSubmit) return;
    onSubmit(trimmed);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button variant="primary" onClick={submit} disabled={!canSubmit}>
            {busy ? 'Сохранение…' : submitLabel}
          </Button>
        </>
      }
    >
      {/* form + onSubmit: Enter в поле подтверждает, как в нативном prompt. */}
      <form
        className="modal-form"
        onSubmit={(e) => { e.preventDefault(); submit(); }}
      >
        <Field label={label} full required>
          {/* autoFocus + выделение текста при фокусе — чтобы можно было сразу
              печатать поверх старого имени, как это делал prompt. Radix
              размонтирует содержимое модалки при закрытии, поэтому autoFocus
              срабатывает на каждое открытие. */}
          <TextInput
            autoFocus
            value={value}
            placeholder={placeholder}
            maxLength={200}
            onFocus={(e) => e.target.select()}
            onChange={(e) => setValue(e.target.value)}
          />
        </Field>
        {hint && <p className="kb-dialog__hint">{hint}</p>}
      </form>
    </Dialog>
  );
}

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/** Подтверждение необратимого действия. */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel,
  danger = false,
  busy = false,
  onConfirm,
  onClose,
}: ConfirmDialogProps) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>Отмена</Button>
          <Button variant={danger ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy}>
            {busy ? 'Удаление…' : confirmLabel}
          </Button>
        </>
      }
    >
      <p className="kb-dialog__text">{message}</p>
    </Dialog>
  );
}
