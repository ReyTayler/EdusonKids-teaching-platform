import { useState, type FormEvent } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { useStageMutations } from '../../hooks/useTaskStructure';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { conflictError } from './TaskBoard';
import { ColorSwatches } from '../../components/form/ColorSwatches';
import { STAGE_PALETTE, stageTone } from '../../lib/stage-tone';
import type { StageCategory } from '../../lib/tasks';

const FORM_ID = 'task-stage-settings-form';

// Подписи с пояснением прямо в варианте: «открыта/закрыта» без расшифровки
// читается как состояние стадии, а не как то, что происходит с задачей на ней.
const CATEGORY_OPTIONS: { value: StageCategory; label: string }[] = [
  { value: 'open', label: 'Открытая — задача в работе' },
  { value: 'closed', label: 'Закрытая — задача завершена' },
];

interface Props {
  stage: { id: number; label: string; color: string | null; category: StageCategory };
  boardId: number;
  onClose: () => void;
}

/**
 * Настройки стадии прямо с доски: цвет плашки и вид стадии.
 *
 * Название здесь не правится намеренно — переименование живёт в самой шапке
 * колонки (Enter/blur), и дублировать его полем в модалке значило бы завести
 * два способа сделать одно и то же.
 *
 * Монтируется только пока открыта: размонтирование
 * сбрасывает буфер, иначе следующее открытие начиналось бы с прошлого выбора.
 */
export function StageSettingsDialog({ stage, boardId, onClose }: Props) {
  const { update } = useStageMutations(boardId);
  const showError = useApiError();
  const { toast } = useToast();

  const [color, setColor] = useState<string | null>(stage.color);
  const [category, setCategory] = useState<StageCategory>(stage.category);

  const preview = stageTone(color, stage.label);

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (update.isPending) return;
    update.mutate(
      { id: stage.id, color, category },
      {
        onSuccess: () => { toast('Стадия обновлена', 'ok'); onClose(); },
        // Смену вида бэкенд отклоняет с 409 (has_tasks — на стадии есть
        // карточки; last_stage_of_category — она последняя открытая либо
        // последняя закрытая в воронке). Так защищён инвариант «задача
        // закрыта ⇔ её стадия имеет category='closed'», поэтому код обязан
        // доехать до человека текстом, а не утонуть молча.
        onError: (err) => showError(conflictError(err), 'Не удалось изменить стадию'),
      },
    );
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => { if (!open) onClose(); }}
      title={`Стадия «${stage.label}»`}
      footer={(
        <button type="submit" form={FORM_ID} className="btn-save" disabled={update.isPending}>
          {update.isPending ? 'Сохраняем…' : 'Сохранить'}
        </button>
      )}
    >
      <form id={FORM_ID} className="modal-form" onSubmit={onSubmit}>
        <Field label="Цвет" full>
          <ColorSwatches
            value={color}
            onChange={setColor}
            colors={STAGE_PALETTE}
            aria-label="Цвет стадии"
          />
          {/* Как стадия будет выглядеть в шапке колонки: цвет выбирают на глаз,
              а на доске он идёт с подписью, которую stageTone красит сам. */}
          <div className="stage-settings__preview" style={{ background: preview.bg, color: preview.ink }}>
            {stage.label}
          </div>
        </Field>

        <Field label="Вид стадии" full>
          <SelectInput
            value={category}
            onChange={(e) => setCategory(e.target.value as StageCategory)}
            options={CATEGORY_OPTIONS}
          />
          <p className="stage-settings__hint">
            Вид нельзя сменить, если на стадии уже есть карточки, и если это
            последняя открытая или последняя закрытая стадия воронки: иначе
            задачи разошлись бы с собственным признаком «закрыта». Штатный путь —
            завести стадию нужного вида и перенести карточки на доске.
          </p>
        </Field>
      </form>
    </Dialog>
  );
}
