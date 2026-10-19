import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { Button } from '../../components/ui/Button';
import { TASK_RESOLUTION_LABELS } from '../../lib/labels';
import type { TaskResolution } from '../../lib/tasks';

const RESOLUTION_OPTIONS = Object.entries(TASK_RESOLUTION_LABELS)
  .map(([value, label]) => ({ value, label }));

interface Props {
  open: boolean;
  pending: boolean;
  onClose: () => void;
  onConfirm: (resolution: TaskResolution) => void;
}

/**
 * Диалог выбора результата при переносе задачи в закрытую стадию
 * (category='closed'). Бэкенд требует resolution на такой перенос — без него
 * move ответит 400, поэтому доска (TaskBoard) открывает этот диалог ДО
 * вызова move и передаёт выбранный результат вместе с ним.
 */
export function TaskCompleteDialog({ open, pending, onClose, onConfirm }: Props) {
  const [resolution, setResolution] = useState<TaskResolution | ''>('');

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => { if (!o) onClose(); }}
      title="Результат задачи"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>Отмена</Button>
          <Button
            variant="primary"
            disabled={!resolution || pending}
            onClick={() => resolution && onConfirm(resolution)}
          >
            Готово
          </Button>
        </>
      }
    >
      <Field label="Результат" required>
        <SelectInput
          value={resolution}
          onChange={(e) => setResolution(e.target.value as TaskResolution)}
          options={RESOLUTION_OPTIONS}
          placeholder="Выберите результат…"
        />
      </Field>
    </Dialog>
  );
}
