import { HelpPopover } from '../../components/ui/HelpPopover';
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
import { fmtRub, todayMSK } from '../../lib/format';
import type {
  CancelMode, PenaltyMode, RateVersion, RateVersionPayload, RateVersionsResponse,
} from '../../lib/types';
import { norm, parseServerErrors, savedMessage, toField, cancelText, penaltyText, type ServerErrors } from './motivationFormat';

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
  const summary = {
    cancel_mode: cancelMode, cancel_value: norm(cancelValue), penalty_enabled: penaltyOn,
    penalty_mode: penaltyMode, penalty_amount: norm(penaltyAmount) || '0',
  };
  const changes: { label: string; before: string; after: string }[] = [];
  if (base) {
    if (Number(groupRate) !== Number(base.group_rate)) changes.push({ label: 'Групповой урок', before: fmtRub(base.group_rate), after: groupRate ? fmtRub(groupRate) : '—' });
    if (Number(indRate) !== Number(base.individual_rate)) changes.push({ label: 'Индивидуальный урок', before: fmtRub(base.individual_rate), after: indRate ? fmtRub(indRate) : '—' });
    if (cancelText(summary) !== cancelText(base)) changes.push({ label: 'Компенсация отмены', before: cancelText(base), after: cancelText(summary) });
    if (penaltyText(summary) !== penaltyText(base)) changes.push({ label: 'Поздний отчёт', before: penaltyText(base), after: penaltyText(summary) });
  }
  const changedDirections = directions.filter((direction) => {
    const old = base?.directions.find((item) => item.direction_id === direction.id);
    const same = (a: string, b: string | null | undefined) => !a && b == null || a !== '' && b != null && Number(a) === Number(b);
    return !same(norm(dirRates[direction.id]?.g ?? ''), old?.group_rate) || !same(norm(dirRates[direction.id]?.i ?? ''), old?.individual_rate);
  });
  return <Dialog open wide className="motivation-editor"
    onOpenChange={(open) => !open && !pending && onClose()}
    title={editing ? 'Исправить ставки школы' : 'Ставки школы с даты'}
    footer={<div className="motivation-editor__footer"><span>Общая версия для групповых и индивидуальных занятий</span>
      <div className="settings-actions">
        <button type="button" className="btn-cancel" onClick={onClose} disabled={pending}>Отмена</button>
        <button type="button" className="btn-add" onClick={submit} disabled={pending}>{pending ? 'Сохраняем…' : 'Сохранить'}</button>
      </div>
    </div>}>
    <div className="motivation-editor__layout">
      <fieldset className="motivation-editor__fields" disabled={pending}>
        {errors.general && <div role="alert" className="motivation-form-error">{errors.general}</div>}
        <section className="motivation-form-section" aria-labelledby="rate-period-heading">
          <h3 id="rate-period-heading">Период действия</h3>
          <div className="motivation-period-field">
            <Field label="Действует с" help="Ставка выбирается по дате занятия. Начисления за предыдущие даты не изменятся." required error={fe.effective_from}>
              <DateInput aria-label="Действует с" value={date} min={todayMSK()} onChange={(e) => setDate(e.target.value)} />
            </Field>

          </div>
        </section>
        <section className="motivation-form-section" aria-labelledby="rate-default-heading">
          <h3 id="rate-default-heading">Оплата занятий <HelpPopover>Фиксированная сумма за занятие. Длительность и число учеников не меняют базовую ставку.</HelpPopover></h3>
          <div className="motivation-rate-field"><div><strong>Групповой урок</strong><span>По умолчанию · за занятие</span></div>
            <Field label="" error={fe.group_rate}><NumberInput min={0} step="0.01" value={groupRate}
              aria-label="Групповой урок, по умолчанию" onChange={(e) => setGroupRate(e.target.value)} /></Field>
          </div>
          <div className="motivation-rate-field"><div><strong>Индивидуальный урок</strong><span>По умолчанию · за занятие</span></div>
            <Field label="" error={fe.individual_rate}><NumberInput min={0} step="0.01" value={indRate}
              aria-label="Индивидуальный урок, по умолчанию" onChange={(e) => setIndRate(e.target.value)} /></Field>
          </div>
        </section>
        <details className="motivation-directions" open={Object.keys(errors.directions).length ? true : undefined}>
          <summary>Ставки по направлениям <span>{directions.length}</span></summary>
          <span className="help-label">По направлениям <HelpPopover>Пустое поле — ставка по умолчанию для соответствующего формата.</HelpPopover></span>
          {directions.map((direction, index) => <div className="motivation-direction" key={direction.id}>
            <DirTag name={direction.name} color={direction.color} />
            <div className="motivation-direction__fields">
              <Field label="Групповой урок, ₽"><NumberInput min={0} step="0.01" value={dirRates[direction.id]?.g ?? ''}
                aria-label={`${direction.name}: групповой урок`} placeholder={groupRate ? toField(groupRate) : 'По умолчанию'}
                onChange={(e) => setDir(direction.id, 'g', e.target.value)} /></Field>
              <Field label="Индивидуальный урок, ₽"><NumberInput min={0} step="0.01" value={dirRates[direction.id]?.i ?? ''}
                aria-label={`${direction.name}: индивидуальный урок`} placeholder={indRate ? toField(indRate) : 'По умолчанию'}
                onChange={(e) => setDir(direction.id, 'i', e.target.value)} /></Field>
            </div>
            {errors.directions[index] && <p role="alert" className="field-error">{direction.name}: {errors.directions[index]}</p>}
          </div>)}
          {!directions.length && <p className="motivation-inline-note">Направлений пока нет. Для новых направлений будет действовать ставка по умолчанию.</p>}
        </details>
        <section className="motivation-form-section" aria-labelledby="rate-cancel-heading">
          <h3 id="rate-cancel-heading">Компенсация отмены <HelpPopover>Для отмен, по которым предусмотрена компенсация преподавателю.</HelpPopover></h3>
          <div className="motivation-conditional-fields">
            <Field label="Как считать"><SelectInput aria-label="Как считать оплату за отмену" value={cancelMode}
              options={(Object.keys(CANCEL_MODE_LABELS) as CancelMode[]).map((value) => ({ value, label: CANCEL_MODE_LABELS[value] }))}
              onChange={(e) => setCancelMode(e.target.value as CancelMode)} /></Field>
            <Field label={cancelMode === 'percent' ? 'Процент от ставки' : 'Сумма, ₽'} error={fe.cancel_value}>
              <NumberInput min={0} max={cancelMode === 'percent' ? 100 : undefined} step="0.01" value={cancelValue}
                aria-label={cancelMode === 'percent' ? 'Процент от ставки' : 'Сумма за отмену, ₽'}
                onChange={(e) => setCancelValue(e.target.value)} /></Field>
          </div>
        </section>
        <section className="motivation-form-section" aria-labelledby="rate-penalty-heading">
          <h3 id="rate-penalty-heading">Поздний отчёт <HelpPopover>Штраф применяется только к позднему отчёту преподавателя из кабинета.</HelpPopover></h3>
          <Checkbox label="Штрафовать за отчёт позже дня занятия" checked={penaltyOn} onChange={(e) => setPenaltyOn(e.target.checked)} />
          {penaltyOn && <div className="motivation-conditional-fields">
            <Field label="Как считать" error={fe.penalty_mode}><SelectInput aria-label="Как начислять штраф" options={PENALTY_OPTIONS} value={penaltyMode}
              onChange={(e) => setPenaltyMode(e.target.value as PenaltyMode)} /></Field>
            <Field label="Сумма, ₽" error={fe.penalty_amount}><NumberInput min={0} step="0.01" value={penaltyAmount}
              aria-label="Сумма штрафа, ₽" onChange={(e) => setPenaltyAmount(e.target.value)} /></Field>
          </div>}

        </section>
      </fieldset>
      <aside className="motivation-preview" aria-label="Проверка новых условий">
        <h3>Новые условия <HelpPopover>Проверьте суммы перед сохранением. Суммы за занятие. Для направлений с собственной ставкой действуют их значения; затем учитываются персональные ставки.</HelpPopover></h3>
        <div className="motivation-example"><span>Групповой урок</span><strong>{groupRate ? fmtRub(groupRate) : '—'}</strong></div>
        <div className="motivation-example"><span>Индивидуальный урок</span><strong>{indRate ? fmtRub(indRate) : '—'}</strong></div>

        <div className="motivation-preview__rule"><span>Компенсация отмены</span><strong>{cancelValue ? cancelText(summary) : '—'}</strong></div>
        <div className="motivation-preview__rule"><span>Поздний отчёт</span><strong>{penaltyText(summary)}</strong></div>
        <div className="motivation-changes"><h3>Что изменится</h3>
          {changes.length > 0 && <ul>{changes.map((item) => <li key={item.label}><span>{item.label}</span><div><del>{item.before}</del> → <strong>{item.after}</strong></div></li>)}</ul>}
          {changedDirections.length > 0 && <p>Изменены ставки направлений: {changedDirections.map((item) => item.name).join(', ')}.</p>}
          {!changes.length && !changedDirections.length && <p>{base ? 'Суммы совпадают с выбранной версией.' : 'Будет создана первая версия ставок школы.'}</p>}
          <p>{editing ? 'Исправляется будущая версия.' : 'Будет сохранена новая версия с указанной даты.'} Ручные суммы сохраняются при пересчёте.</p>
        </div>
      </aside>
    </div>
  </Dialog>;
}
