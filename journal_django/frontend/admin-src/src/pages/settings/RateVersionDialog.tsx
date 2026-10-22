import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { DateInput } from '../../components/form/DateInput';
import { NumberInput } from '../../components/form/NumberInput';
import { Checkbox } from '../../components/form/Checkbox';
import { SelectInput } from '../../components/form/SelectInput';
import { DirTag } from '../../components/ui/DirTag';
import { useToast } from '../../components/ui/Toast';
import { useRateVersionMutations } from '../../hooks/useMotivation';
import { CANCEL_MODE_LABELS, PENALTY_MODE_LABELS } from '../../lib/labels';
import { fmtRub } from '../../lib/format';
import type {
  CancelMode, PenaltyMode, RateVersion, RateVersionPayload, RateVersionsResponse,
} from '../../lib/types';
import { todayMSK } from '../../lib/format';
import { norm, parseServerErrors, savedMessage, toField, type ServerErrors } from './motivationFormat';

interface Props {
  directions: RateVersionsResponse['directions'];
  /** Версия, из которой берутся стартовые значения (текущая или правимая). */
  base: RateVersion | null;
  /** Правка существующей версии (PATCH); null — новая версия (POST). */
  editing: RateVersion | null;
  defaultDate: string;
  onClose: () => void;
}

const PENALTY_OPTIONS = (Object.keys(PENALTY_MODE_LABELS) as PenaltyMode[])
  .map((v) => ({ value: v, label: PENALTY_MODE_LABELS[v] }));

const FIELDS = ['effective_from', 'group_rate', 'individual_rate', 'cancel_value', 'penalty_amount', 'penalty_mode'];

export function RateVersionDialog({ directions, base, editing, defaultDate, onClose }: Props) {
  const { toast } = useToast();
  const { create, update } = useRateVersionMutations();

  const [date, setDate] = useState(editing ? editing.effective_from : defaultDate);
  const [groupRate, setGroupRate] = useState(toField(base?.group_rate));
  const [indRate, setIndRate] = useState(toField(base?.individual_rate));
  const [dirRates, setDirRates] = useState<Record<number, { g: string; i: string }>>(() => {
    const m: Record<number, { g: string; i: string }> = {};
    for (const d of base?.directions ?? []) {
      m[d.direction_id] = { g: toField(d.group_rate), i: toField(d.individual_rate) };
    }
    return m;
  });
  const [cancelMode, setCancelMode] = useState<CancelMode>(base?.cancel_mode ?? 'percent');
  const [cancelValue, setCancelValue] = useState(toField(base?.cancel_value ?? '50'));
  const [penaltyOn, setPenaltyOn] = useState(base?.penalty_enabled ?? false);
  const [penaltyMode, setPenaltyMode] = useState<PenaltyMode>(base?.penalty_mode ?? 'per_lesson');
  const [penaltyAmount, setPenaltyAmount] = useState(toField(base?.penalty_amount ?? '40'));
  const [errors, setErrors] = useState<ServerErrors>({ fields: {}, directions: {}, general: '' });

  const pending = create.isPending || update.isPending;

  const setDir = (id: number, key: 'g' | 'i', v: string) =>
    setDirRates((prev) => ({ ...prev, [id]: { g: prev[id]?.g ?? '', i: prev[id]?.i ?? '', [key]: v } }));

  const submit = () => {
    const fields: Record<string, string> = {};
    if (!date) fields.effective_from = 'Укажите дату';
    if (!norm(groupRate)) fields.group_rate = 'Укажите сумму';
    if (!norm(indRate)) fields.individual_rate = 'Укажите сумму';
    if (!norm(cancelValue)) fields.cancel_value = 'Укажите значение';
    if (penaltyOn && !norm(penaltyAmount)) fields.penalty_amount = 'Укажите сумму';
    if (Object.keys(fields).length) { setErrors({ fields, directions: {}, general: '' }); return; }

    const body: RateVersionPayload = {
      effective_from: date,
      group_rate: norm(groupRate),
      individual_rate: norm(indRate),
      cancel_mode: cancelMode,
      cancel_value: norm(cancelValue),
      penalty_enabled: penaltyOn,
      penalty_mode: penaltyMode,
      penalty_amount: norm(penaltyAmount) || '0',
      // Шлём всю таблицу: пара пусто/пусто сервер сам превращает в «как по умолчанию».
      directions: directions.map((d) => ({
        direction_id: d.id,
        group_rate: norm(dirRates[d.id]?.g ?? '') || null,
        individual_rate: norm(dirRates[d.id]?.i ?? '') || null,
      })),
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
      wide
      onOpenChange={(o) => !o && !pending && onClose()}
      title={editing ? 'Исправить ставки' : 'Изменить ставки с даты'}
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

        <Field label="Действует с" required error={fe.effective_from} full>
          <DateInput value={date} min={todayMSK()} onChange={(e) => setDate(e.target.value)} />
          <p className="field-hint">Начисления до этой даты не изменятся.</p>
        </Field>

        <div className="settings-block">
          <div className="modal-section-label">Ставка за урок, ₽</div>
          <div className="table-wrap">
            <table className="settings-table">
              <thead>
                <tr><th>Направление</th><th>Групповой урок</th><th>Индивидуальный урок</th></tr>
              </thead>
              <tbody>
                <tr>
                  <td>По умолчанию</td>
                  <td>
                    <Field label="" error={fe.group_rate}>
                      <NumberInput min={0} step="0.01" value={groupRate}
                        aria-label="Групповой урок, по умолчанию"
                        onChange={(e) => setGroupRate(e.target.value)} />
                    </Field>
                  </td>
                  <td>
                    <Field label="" error={fe.individual_rate}>
                      <NumberInput min={0} step="0.01" value={indRate}
                        aria-label="Индивидуальный урок, по умолчанию"
                        onChange={(e) => setIndRate(e.target.value)} />
                    </Field>
                  </td>
                </tr>
                {directions.map((d) => (
                  <tr key={d.id}>
                    <td><DirTag name={d.name} color={d.color} /></td>
                    <td>
                      <div className="field">
                        <NumberInput min={0} step="0.01" value={dirRates[d.id]?.g ?? ''}
                          aria-label={`${d.name}: групповой урок`}
                          placeholder={groupRate ? `как у всех (${fmtRub(norm(groupRate))})` : 'как по умолчанию'}
                          onChange={(e) => setDir(d.id, 'g', e.target.value)} />
                      </div>
                    </td>
                    <td>
                      <div className="field">
                        <NumberInput min={0} step="0.01" value={dirRates[d.id]?.i ?? ''}
                          aria-label={`${d.name}: индивидуальный урок`}
                          placeholder={indRate ? `как у всех (${fmtRub(norm(indRate))})` : 'как по умолчанию'}
                          onChange={(e) => setDir(d.id, 'i', e.target.value)} />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {directions.map((d, i) => errors.directions[i] && (
            <div key={d.id} className="field-error">{d.name}: {errors.directions[i]}</div>
          ))}
          <p className="field-hint">Пустое поле направления — действует ставка по умолчанию.</p>
        </div>

        <div className="settings-block">
          <div className="modal-section-label">Отмена урока</div>
          <div className="settings-inline">
            <div className="segmented" role="group" aria-label="Как считать оплату за отмену">
              {(Object.keys(CANCEL_MODE_LABELS) as CancelMode[]).map((m) => (
                <button key={m} type="button"
                  className={`segmented__btn${cancelMode === m ? ' is-active' : ''}`}
                  aria-pressed={cancelMode === m}
                  onClick={() => setCancelMode(m)}>
                  {CANCEL_MODE_LABELS[m]}
                </button>
              ))}
            </div>
            <Field label="" error={fe.cancel_value}>
              <NumberInput min={0} step="0.01" value={cancelValue}
                aria-label={cancelMode === 'percent' ? 'Процент от ставки' : 'Сумма за отмену, ₽'}
                onChange={(e) => setCancelValue(e.target.value)} />
            </Field>
          </div>
        </div>

        <div className="settings-block">
          <div className="modal-section-label">Штраф за отчёт не в день урока</div>
          <Checkbox label="Включён" checked={penaltyOn} onChange={(e) => setPenaltyOn(e.target.checked)} />
          {penaltyOn && (
            <div className="settings-inline">
              <Field label="Сумма, ₽" error={fe.penalty_amount}>
                <NumberInput min={0} step="0.01" value={penaltyAmount}
                  onChange={(e) => setPenaltyAmount(e.target.value)} />
              </Field>
              <Field label="Как считать" error={fe.penalty_mode}>
                <SelectInput options={PENALTY_OPTIONS} value={penaltyMode}
                  onChange={(e) => setPenaltyMode(e.target.value as PenaltyMode)} />
              </Field>
            </div>
          )}
        </div>

        {editing && (
          <p className="field-hint field-hint--warn">
            Версия ещё не начала действовать, поэтому начисления за прошлое не изменятся.
          </p>
        )}
      </div>
    </Dialog>
  );
}
