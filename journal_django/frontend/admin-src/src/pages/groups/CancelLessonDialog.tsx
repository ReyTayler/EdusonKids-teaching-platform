import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useCancelLesson } from '../../hooks/useGroupPlan';
import { CANCELLATION_REASONS_KEY, useCancellationReasons } from '../../hooks/useCancellationReasons';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { DateInput } from '../../components/form/DateInput';
import { TimeInput } from '../../components/form/TimeInput';
import { Textarea } from '../../components/form/Textarea';
import { ApiError } from '../../lib/api';
import { todayMSK } from '../../lib/format';
import { CANCELLATION_LABELS, CANCELLATION_SIDE_LABELS } from '../../lib/labels';
import type { PlanRow } from '../../hooks/useGroupPlanCalendar';
import type { CancelLessonPayload, CancellationSide } from '../../lib/types';
import { lessonLabel } from './planLabels';

const DETAILS_MAX = 1000;
/** Поля первого шага: ошибка по ним возвращает пользователя к форме. */
const FORM_FIELD_KEYS = ['reason_id', 'details', 'notified_on', 'side', 'lesson_date', 'non_field_errors'];
const MOVE_FIELD_KEYS = ['new_date', 'new_time'];

/** Цепочка окон: форма отмены → вопрос о переносе → новая дата. */
type Step = 'form' | 'ask' | 'move';

interface Props {
  open: boolean;
  onClose: () => void;
  groupId: number;
  /** Курсовые строки плана, которые можно отменить (pending/overdue, seq != null). */
  rows: PlanRow[];
  initialLessonId?: number | null;
}

/** Ошибки полей из 400 `{error, details: {field: [..]}}` — показываем под полем. */
function fieldErrors(err: unknown): Record<string, string> {
  if (!(err instanceof ApiError) || err.status !== 400) return {};
  const d = err.details;
  if (!d || typeof d !== 'object') return {};
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(d as Record<string, unknown>)) {
    if (Array.isArray(v) && v.length > 0) out[k] = String(v[0]);
    else if (typeof v === 'string') out[k] = v;
  }
  return out;
}

/**
 * Оболочка: форма монтируется заново при каждом открытии, поэтому прежнее
 * состояние не «мигает» и сегодняшняя дата считается на момент открытия.
 */
export function CancelLessonDialog(props: Props) {
  if (!props.open) return null;
  return <CancelLessonForm {...props} />;
}

/** Выбранное занятие запоминаем снимком: id в плане переживает чужую отмену, а дата уезжает. */
interface Picked { id: number; date: string }

/**
 * Отмена занятия: занятие, сторона, причина из справочника, необязательные
 * детали, для клиента ещё и дата, когда он сообщил. Затем вопрос о переносе: «Нет» —
 * обычная отмена, «Да» — урок встанет на указанную дату. О компенсации здесь намеренно ни слова:
 * решение о деньгах преподавателя принимает суперадмин в справочнике причин.
 */
function CancelLessonForm({ onClose, groupId, rows, initialLessonId }: Props) {
  const { toast } = useToast();
  const qc = useQueryClient();
  const showError = useApiError();
  const cancelLesson = useCancelLesson(groupId);
  const reasonsQ = useCancellationReasons();
  const reasons = reasonsQ.data;

  const [picked, setPicked] = useState<Picked | null>(() => {
    const r = initialLessonId != null ? rows.find((x) => x.id === initialLessonId) : undefined;
    return r ? { id: r.id, date: r.scheduled_date } : null;
  });
  const [side, setSide] = useState<CancellationSide | ''>('');
  const [reasonKey, setReasonKey] = useState('');
  const [details, setDetails] = useState('');
  const [notifiedOn, setNotifiedOn] = useState(() => todayMSK());
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [step, setStep] = useState<Step>('form');
  const [newDate, setNewDate] = useState('');
  const [newTime, setNewTime] = useState('');

  const row =picked ? rows.find((r) => r.id === picked.id) : undefined;
  // План обновился (фокус окна, чужая отмена): строка с тем же id уехала на другую
  // дату или пропала — отменять «то же» занятие уже нельзя, выбор сбрасываем.
  // Пока идёт отправка, не проверяем: после своей успешной отмены строка тоже уедет,
  // и второй тост «занятие изменилось» противоречил бы «Занятие отменено».
  const stale = !cancelLesson.isPending && picked !== null && (!row || row.scheduled_date !== picked.date);
  useEffect(() => {
    if (!stale) return;
    setPicked(null);
    setStep('form');
    toast(CANCELLATION_LABELS.changed, 'error');
  }, [stale, toast]);

  const lessonOptions = useMemo(
    () => rows.map((r) => ({ value: String(r.id), label: lessonLabel(r) })),
    [rows],
  );
  const sideOptions = (Object.keys(CANCELLATION_SIDE_LABELS) as CancellationSide[])
    .map((k) => ({ value: k, label: CANCELLATION_SIDE_LABELS[k] }));
  const reasonOptions = useMemo(() => {
    if (!side || !reasons) return [];
    return (reasons[side] ?? []).map((r) => ({ value: String(r.id), label: r.name }));
  }, [reasons, side]);

  // После перезапроса справочника выбранной причины может уже не быть в списке.
  useEffect(() => {
    if (reasons && reasonKey && !reasonOptions.some((o) => o.value === reasonKey)) setReasonKey('');
  }, [reasons, reasonKey, reasonOptions]);

  // Причина обязательна: без причин у стороны отменить занятие нельзя, форма объясняет почему.
  const noReasons = Boolean(reasons && side && reasonOptions.length === 0);
  const complete = Boolean(
    picked && !stale && reasons && side && reasonKey
    && (side !== 'client' || notifiedOn),
  );

  const clearError = (key: string) => setErrors((e) => (e[key] ? { ...e, [key]: '' } : e));

  const openMoveStep = () => {
    // Время по умолчанию — время отменяемого занятия: переносят чаще всего только день.
    setNewTime((row?.scheduled_time ?? '').slice(0, 5));
    setStep('move');
  };

  const submit = async (move: boolean) => {
    // isPending гасит повторную отправку двойным кликом до ответа сервера.
    if (!complete || !picked || !side || cancelLesson.isPending) return;
    const body: CancelLessonPayload = {
      // Дата из снимка на момент выбора, а не из живого плана.
      lesson_date: picked.date.slice(0, 10),
      side,
      reason_id: Number(reasonKey),
      ...(details.trim() ? { details: details.trim() } : {}),
      ...(side === 'client' ? { notified_on: notifiedOn } : {}),
      ...(move ? { new_date: newDate, new_time: newTime } : {}),
    };
    try {
      await cancelLesson.mutateAsync({ lessonId: picked.id, body });
      toast(move ? CANCELLATION_LABELS.successMoved : CANCELLATION_LABELS.success, 'ok');
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Занятие сдвинули или отменили в другом окне: план уже перезапрошен.
        toast(CANCELLATION_LABELS.changed, 'error');
        setPicked(null);
        setStep('form');
        return;
      }
      const fe = fieldErrors(err);
      setErrors(fe);
      const hasFormField = FORM_FIELD_KEYS.some((k) => fe[k]);
      const hasMoveField = MOVE_FIELD_KEYS.some((k) => fe[k]);
      // Причина отклонена (удалена другим админом, не той стороны) — список устарел.
      const reasonRejected = err instanceof ApiError && err.status === 400
        && (fe.reason_id || (!hasFormField && !hasMoveField && /^Причина /.test(err.message)));
      if (reasonRejected) {
        void qc.invalidateQueries({ queryKey: CANCELLATION_REASONS_KEY });
        setReasonKey('');
      }
      if (hasFormField || reasonRejected) setStep('form');
      // Ошибки без своего поля ({"error": ...}, например занятое время) — тостом.
      if (!hasFormField && !hasMoveField) showError(err);
    }
  };

  const busy = cancelLesson.isPending;

  const footers: Record<Step, ReactNode> = {
    form: (
      <Button variant="primary" onClick={() => setStep('ask')} disabled={!complete}>
        {CANCELLATION_LABELS.next}
      </Button>
    ),
    ask: (
      <>
        <Button onClick={() => setStep('form')} disabled={busy}>{CANCELLATION_LABELS.back}</Button>
        <Button variant="danger" onClick={() => { void submit(false); }} disabled={!complete || busy}>
          {CANCELLATION_LABELS.moveNo}
        </Button>
        <Button variant="primary" onClick={openMoveStep} disabled={!complete || busy}>
          {CANCELLATION_LABELS.moveYes}
        </Button>
      </>
    ),
    move: (
      <>
        <Button onClick={() => setStep('ask')} disabled={busy}>{CANCELLATION_LABELS.back}</Button>
        <Button
          variant="danger"
          onClick={() => { void submit(true); }}
          disabled={!complete || !newDate || !newTime || busy}
        >
          {CANCELLATION_LABELS.submitMove}
        </Button>
      </>
    ),
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={CANCELLATION_LABELS.dialogTitle}
      footer={footers[step]}
    >
      {step === 'ask' && <p>{CANCELLATION_LABELS.moveQuestion}</p>}
      {step === 'move' && (
        <>
          <Field label={CANCELLATION_LABELS.newDate} required error={errors.new_date}>
            <DateInput
              value={newDate}
              onChange={(e) => { setNewDate(e.target.value); clearError('new_date'); }}
              min={todayMSK()}
            />
          </Field>
          <Field label={CANCELLATION_LABELS.newTime} required error={errors.new_time}>
            <TimeInput
              value={newTime}
              onChange={(e) => { setNewTime(e.target.value); clearError('new_time'); }}
            />
          </Field>
        </>
      )}
      {step === 'form' && (
        <>
          <Field label={CANCELLATION_LABELS.lesson} required error={errors.lesson_date}>
            <SelectInput
              value={picked ? String(picked.id) : ''}
              onChange={(e) => {
                const r = rows.find((x) => String(x.id) === e.target.value);
                setPicked(r ? { id: r.id, date: r.scheduled_date } : null);
                clearError('lesson_date');
              }}
              options={lessonOptions}
              placeholder={CANCELLATION_LABELS.pickLesson}
            />
          </Field>
          <Field label={CANCELLATION_LABELS.side} required error={errors.side}>
            <SelectInput
              value={side}
              onChange={(e) => {
                setSide(e.target.value as CancellationSide);
                setReasonKey('');
                setDetails('');
                setErrors({});
              }}
              options={sideOptions}
              placeholder={CANCELLATION_LABELS.pickSide}
            />
          </Field>
          {side && (
            reasonsQ.isError && !reasons ? (
              <Field label={CANCELLATION_LABELS.reason} required error={CANCELLATION_LABELS.reasonsError}>
                <button type="button" className="btn-cancel" onClick={() => { void reasonsQ.refetch(); }}>
                  {CANCELLATION_LABELS.retry}
                </button>
              </Field>
            ) : (
              <Field
                label={CANCELLATION_LABELS.reason}
                required
                error={errors.reason_id || errors.non_field_errors || (noReasons ? CANCELLATION_LABELS.noReasons : undefined)}
              >
                <SelectInput
                  value={reasonKey}
                  disabled={!reasons || noReasons}
                  onChange={(e) => {
                    setReasonKey(e.target.value);
                    clearError('reason_id');
                    clearError('non_field_errors');
                  }}
                  options={reasonOptions}
                  placeholder={reasons ? CANCELLATION_LABELS.pickReason : CANCELLATION_LABELS.reasonsLoading}
                />
              </Field>
            )
          )}
          {side && (
            <Field label={CANCELLATION_LABELS.details} error={errors.details}>
              <Textarea
                value={details}
                onChange={(e) => { setDetails(e.target.value); clearError('details'); }}
                maxLength={DETAILS_MAX}
                rows={3}
                placeholder={CANCELLATION_LABELS.detailsPlaceholder}
              />
              <div className="field-hint">{details.length}/{DETAILS_MAX}</div>
            </Field>
          )}
          {side === 'client' && (
            <Field label={CANCELLATION_LABELS.notifiedOn} required error={errors.notified_on}>
              <DateInput
                value={notifiedOn}
                onChange={(e) => { setNotifiedOn(e.target.value); clearError('notified_on'); }}
                max={todayMSK()}
              />
            </Field>
          )}
        </>
      )}
    </Dialog>
  );
}
