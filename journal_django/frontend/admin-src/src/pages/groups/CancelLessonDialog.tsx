import { useEffect, useMemo, useState } from 'react';
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
import { Textarea } from '../../components/form/Textarea';
import { ApiError } from '../../lib/api';
import { todayMSK } from '../../lib/format';
import { CANCELLATION_LABELS, CANCELLATION_SIDE_LABELS } from '../../lib/labels';
import type { PlanRow } from '../../hooks/useGroupPlanCalendar';
import type { CancelLessonPayload, CancellationSide } from '../../lib/types';
import { lessonLabel } from './planLabels';

/** Значение пункта «Другая причина» в списке: id причин числовые, пересечения нет. */
const OTHER = 'other';
const OTHER_MAX = 1000;
const FIELD_KEYS = ['reason_id', 'other_text', 'notified_on', 'side', 'lesson_date'];

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
 * Отмена занятия: занятие, сторона, причина (из справочника или «Другая»), для
 * клиента ещё и дата, когда он сообщил. О компенсации здесь намеренно ни слова:
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
  const [otherText, setOtherText] = useState('');
  const [notifiedOn, setNotifiedOn] = useState(() => todayMSK());
  const [errors, setErrors] = useState<Record<string, string>>({});

  const row = picked ? rows.find((r) => r.id === picked.id) : undefined;
  // План обновился (фокус окна, чужая отмена): строка с тем же id уехала на другую
  // дату или пропала — отменять «то же» занятие уже нельзя, выбор сбрасываем.
  // Пока идёт отправка, не проверяем: после своей успешной отмены строка тоже уедет,
  // и второй тост «занятие изменилось» противоречил бы «Занятие отменено».
  const stale = !cancelLesson.isPending && picked !== null && (!row || row.scheduled_date !== picked.date);
  useEffect(() => {
    if (!stale) return;
    setPicked(null);
    toast(CANCELLATION_LABELS.changed, 'error');
  }, [stale, toast]);

  const lessonOptions = useMemo(
    () => rows.map((r) => ({ value: String(r.id), label: lessonLabel(r) })),
    [rows],
  );
  const sideOptions = (Object.keys(CANCELLATION_SIDE_LABELS) as CancellationSide[])
    .map((k) => ({ value: k, label: CANCELLATION_SIDE_LABELS[k] }));
  // «Другая причина» только вместе с загруженным справочником: пока он не пришёл,
  // нельзя ни выбрать причину, ни подсунуть «другую» в обход компенсации.
  const reasonOptions = useMemo(() => {
    if (!side || !reasons) return [];
    return [
      ...(reasons[side] ?? []).map((r) => ({ value: String(r.id), label: r.name })),
      { value: OTHER, label: CANCELLATION_LABELS.otherReason },
    ];
  }, [reasons, side]);

  // После перезапроса справочника выбранной причины может уже не быть в списке.
  useEffect(() => {
    if (reasons && reasonKey && !reasonOptions.some((o) => o.value === reasonKey)) setReasonKey('');
  }, [reasons, reasonKey, reasonOptions]);

  const isOther = reasonKey === OTHER;
  const complete = Boolean(
    picked && !stale && reasons && side && reasonKey
    && (!isOther || otherText.trim())
    && (side !== 'client' || notifiedOn),
  );

  const clearError = (key: string) => setErrors((e) => (e[key] ? { ...e, [key]: '' } : e));

  const submit = async () => {
    // isPending гасит повторную отправку двойным кликом до ответа сервера.
    if (!complete || !picked || !side || cancelLesson.isPending) return;
    const body: CancelLessonPayload = {
      // Дата из снимка на момент выбора, а не из живого плана.
      lesson_date: picked.date.slice(0, 10),
      side,
      ...(isOther ? { other_text: otherText.trim() } : { reason_id: Number(reasonKey) }),
      ...(side === 'client' ? { notified_on: notifiedOn } : {}),
    };
    try {
      await cancelLesson.mutateAsync({ lessonId: picked.id, body });
      toast(CANCELLATION_LABELS.success, 'ok');
      onClose();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        // Занятие сдвинули или отменили в другом окне: план уже перезапрошен.
        toast(CANCELLATION_LABELS.changed, 'error');
        setPicked(null);
        return;
      }
      const fe = fieldErrors(err);
      setErrors(fe);
      const hasField = FIELD_KEYS.some((k) => fe[k]);
      // Причина отклонена (удалена другим админом, не той стороны) — список устарел.
      if (err instanceof ApiError && err.status === 400 && (fe.reason_id || (!hasField && /^Причина /.test(err.message)))) {
        void qc.invalidateQueries({ queryKey: CANCELLATION_REASONS_KEY });
        if (!isOther) setReasonKey('');
      }
      // Ошибки без своего поля (non_field_errors, {"error": ...}) — тостом.
      if (!hasField && !fe.non_field_errors) showError(err);
    }
  };

  const busy = cancelLesson.isPending;

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={CANCELLATION_LABELS.dialogTitle}
      footer={
        <Button variant="danger" onClick={() => { void submit(); }} disabled={!complete || busy}>
          {CANCELLATION_LABELS.submit}
        </Button>
      }
    >
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
            setOtherText('');
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
          <Field label={CANCELLATION_LABELS.reason} required error={errors.reason_id || errors.non_field_errors}>
            <SelectInput
              value={reasonKey}
              disabled={!reasons}
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
      {side && isOther && (
        <Field label={CANCELLATION_LABELS.otherText} required error={errors.other_text}>
          <Textarea
            value={otherText}
            onChange={(e) => { setOtherText(e.target.value); clearError('other_text'); }}
            maxLength={OTHER_MAX}
            rows={3}
            placeholder={CANCELLATION_LABELS.otherPlaceholder}
          />
          <div className="field-hint">{otherText.length}/{OTHER_MAX}</div>
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
    </Dialog>
  );
}
