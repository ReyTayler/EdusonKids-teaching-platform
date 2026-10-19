import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';

const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
  'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

// 12 месяцев вперёд от текущего: заморозка «до» дальше года — не рабочий сценарий,
// а свободный ввод даты порождал бы вопрос «а день зачем?» (бэк всё равно
// нормализует значение к 1-му числу).
function monthOptions(): { value: string; label: string }[] {
  const now = new Date();
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() + i, 1);
    const month = String(d.getMonth() + 1).padStart(2, '0');
    return {
      value: `${d.getFullYear()}-${month}-01`,
      label: `${MONTHS[d.getMonth()]} ${d.getFullYear()}`,
    };
  });
}

interface Props {
  studentName: string;
  pending: boolean;
  /** Текущий месяц заморозки — задан, когда месяц меняют у уже замороженной сделки. */
  initialMonth?: string | null;
  onClose: () => void;
  onConfirm: (frozenUntilMonth: string) => void;
}

/**
 * Диалог заморозки сделки. Единственное поле — «до какого месяца»: заморозка
 * стала обычным переходом воронки и больше не снимает членства и не двигает
 * расписание (спека 2026-07-25) — это менеджер делает сам.
 *
 * Тот же диалог продлевает заморозку: переход «Заморожен → Заморожен» бэк
 * разрешает и просто перезаписывает месяц, поэтому размораживать ради этого не надо.
 */
export function FreezeDealDialog({
  studentName, pending, initialMonth, onClose, onConfirm,
}: Props) {
  const options = monthOptions();
  const editing = !!initialMonth;
  // Истёкший месяц в список 12 месяцев вперёд не попадает — тогда стартуем с
  // ближайшего, иначе SelectInput показал бы значение, которого нет в options.
  const [month, setMonth] = useState(
    options.some((o) => o.value === initialMonth) ? initialMonth! : options[0].value,
  );

  return (
    <Dialog
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={editing ? `Месяц заморозки: ${studentName}` : `Заморозить: ${studentName}`}
      footer={
        <>
          <button type="button" className="btn-secondary" onClick={onClose}>Отмена</button>
          <button
            type="button"
            className="btn-primary"
            disabled={pending}
            onClick={() => onConfirm(month)}
          >
            {editing ? 'Сохранить' : 'Заморозить'}
          </button>
        </>
      }
    >
      <p className="renewal-close-dialog__text">
        {editing
          ? 'Сделка останется в стадии «Заморожен» — изменится только месяц окончания.'
          : 'Сделка переедет в стадию «Заморожен». Членство в группах и расписание не '
            + 'меняются — снимите их вручную, если нужно.'}
      </p>
      {/* Без required-звёздочки: поле всегда предзаполнено ближайшим месяцем,
          пустым его отправить нельзя. */}
      <Field label="Заморозка до месяца">
        <SelectInput
          value={month}
          onChange={(e) => setMonth(e.target.value)}
          options={options}
        />
      </Field>
    </Dialog>
  );
}
