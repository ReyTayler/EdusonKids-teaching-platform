import { useEffect, useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';
import { Field } from '../../components/form/Field';
import { DateInput } from '../../components/form/DateInput';
import { useFreezePreview, useFreezeGroup, type FreezePayload } from '../../hooks/useGroupPlan';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { ApiError } from '../../lib/api';
import { fmtDate, todayMSK } from '../../lib/format';
import { AFFECTED_OP_LABELS } from '../../lib/labels';

interface Props {
  groupId: number;
  open: boolean;
  onClose: () => void;
}

const DEBOUNCE_MS = 400;

/** Человекочитаемый текст ошибки сервера: берём первое сообщение из details
 * (поля вроде resume_date), иначе message. Общий extractErrorDetail знает
 * только часть ключей, а здесь ошибка приходит именно под именем поля. */
function errorText(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Не удалось рассчитать заморозку';
  const d = err.details;
  if (d && typeof d === 'object') {
    for (const v of Object.values(d as Record<string, unknown>)) {
      if (Array.isArray(v) && v.length > 0) return String(v[0]);
      if (typeof v === 'string' && v) return v;
    }
  }
  return err.message || 'Не удалось рассчитать заморозку';
}

/**
 * Заморозка группы: пауза в занятиях с даты «заморозить с» до «возобновить с».
 * Превью приходит автоматически (с задержкой, чтобы не слать запрос на каждое
 * нажатие клавиши) — пользователь видит последствия до применения.
 */
export default function FreezeGroupDialog({ groupId, open, onClose }: Props) {
  const { toast } = useToast();
  const showError = useApiError();
  const freeze = useFreezeGroup(groupId);

  const [from, setFrom] = useState('');
  const [resume, setResume] = useState('');
  // Значение, ушедшее в запрос превью, — отстаёт от полей на DEBOUNCE_MS.
  const [debounced, setDebounced] = useState<FreezePayload | null>(null);

  // При каждом открытии начинаем с чистого листа: «с» = сегодня, «до» пусто.
  useEffect(() => {
    if (open) {
      setFrom(todayMSK());
      setResume('');
      setDebounced(null);
    }
  }, [open]);

  // Совпадающие/перевёрнутые даты не отправляем — это очевидная ошибка ввода.
  const orderOk = !!from && !!resume && resume > from;

  useEffect(() => {
    if (!open || !orderOk) { setDebounced(null); return undefined; }
    const t = setTimeout(() => setDebounced({ freeze_from: from, resume_date: resume }), DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [open, orderOk, from, resume]);

  const preview = useFreezePreview(groupId, debounced);
  const data = preview.data;
  // Превью актуально, только если соответствует текущим полям (иначе после правки
  // даты на миг светились бы цифры от прежних дат).
  const fresh = !!debounced && debounced.freeze_from === from && debounced.resume_date === resume;
  const pending = orderOk && (!fresh || preview.isFetching);

  const submit = async () => {
    if (!orderOk) return;
    try {
      await freeze.mutateAsync({ freeze_from: from, resume_date: resume });
      toast('Заморозка применена', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => !o && onClose()}
      title="Заморозка группы"
      footer={
        <>
          <Button onClick={onClose} disabled={freeze.isPending}>Отмена</Button>
          <Button
            variant="primary"
            onClick={() => { void submit(); }}
            disabled={!orderOk || pending || !data || preview.isError || freeze.isPending}
          >
            Заморозить
          </Button>
        </>
      }
    >
      <div className="schedule-form__hint">
        Занятия в выбранный период не проводятся: все последующие занятия курса
        сдвигаются на время паузы. Разовые переносы, замены и отмены после даты
        заморозки сбрасываются.
      </div>
      <Field label="Заморозить с" required>
        <DateInput value={from} onChange={(e) => setFrom(e.target.value)} />
      </Field>
      <Field label="Возобновить с" required>
        <DateInput value={resume} onChange={(e) => setResume(e.target.value)} />
      </Field>

      {!!from && !!resume && !orderOk && (
        <div className="field-hint" role="alert">Дата возобновления должна быть позже даты заморозки.</div>
      )}
      {orderOk && pending && <div className="field-hint">Считаем, что сдвинется…</div>}
      {orderOk && !pending && preview.isError && (
        <div className="field-hint" role="alert">{errorText(preview.error)}</div>
      )}
      {orderOk && !pending && data && (
        <div>
          <p>
            Сдвинется {data.lessons_count} {pluralLessons(data.lessons_count)}: с {fmtDate(data.first_date)}
            {' → '}первое после паузы {fmtDate(data.resume_first_date)}, курс закончится {fmtDate(data.last_date)}.
          </p>
          {data.affected.length > 0 && (
            <>
              <div className="schedule-form__hint">Сбросятся разовые операции:</div>
              <ul className="affected-ops-list">
                {data.affected.map((op, i) => (
                  <li key={i} className="affected-ops-list__item">
                    <span className="status-badge status-badge--negative">{AFFECTED_OP_LABELS[op.kind]}</span>
                    {op.seq != null && <span> урок {op.seq}</span>}
                    <span> — {fmtDate(op.date)}</span>
                    {op.time && <span> {op.time.slice(0, 5)}</span>}
                    {op.from_date && <span> (было {fmtDate(op.from_date)})</span>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </Dialog>
  );
}

function pluralLessons(n: number): string {
  const m10 = n % 10;
  const m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return 'занятие';
  if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return 'занятия';
  return 'занятий';
}
