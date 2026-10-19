import { useState } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { SelectInput } from '../../components/form/SelectInput';
import { Textarea } from '../../components/form/Textarea';
import { usePaymentModal } from '../../providers/PaymentModalProvider';
import { useAuth } from '../../hooks/useAuth';
import { canWritePayments, type Role } from '../../lib/permissions';
import { RENEWAL_LOST_REASON_LABELS } from '../../lib/labels';
import type { RenewalLostReason } from '../../lib/renewals';

/** Сделка, которую закрываем: данные для диалога и формы оплаты. */
export interface CloseDialogTarget {
  dealId: number;
  studentId: number;
  studentName: string;
  mode: 'won' | 'lost';
}

interface Props {
  target: CloseDialogTarget;
  onClose: () => void;
  /** Выполнить перенос: reason_code уходит в move, comment — отдельным комментарием. */
  onConfirm: (opts: { reason_code?: string; comment?: string }) => void;
  pending: boolean;
}

const REASON_OPTIONS = Object.entries(RENEWAL_LOST_REASON_LABELS)
  .map(([value, label]) => ({ value, label }));

/**
 * Диалог закрытия сделки продления. «Ушёл» требует причину. «Продлён» —
 * всегда окончательное ручное решение менеджера: оплата сама по себе сделку
 * не закрывает (только двигает по стадиям Не было урока/Урок 1–3 / Ждём
 * продление), поэтому тут одна кнопка подтверждения плюс необязательный
 * ярлык на форму оплаты.
 */
export function RenewalCloseDialog({ target, onClose, onConfirm, pending }: Props) {
  const { open: openPayment } = usePaymentModal();
  const { me } = useAuth();
  // Ярлык на форму оплаты — только тем, кто оплаты вносит (админ/суперадмин).
  // Само закрытие сделки остаётся менеджеру: это решение, а не деньги.
  const canPay = canWritePayments(me?.role as Role);
  const [reason, setReason] = useState<RenewalLostReason | ''>('');
  const [comment, setComment] = useState('');

  const lost = target.mode === 'lost';

  const footer = lost ? (
    <>
      <button type="button" className="btn-secondary" onClick={onClose}>Отмена</button>
      <button
        type="button"
        className="btn-danger"
        disabled={!reason || pending}
        onClick={() => onConfirm({ reason_code: reason, comment: comment.trim() || undefined })}
      >
        Закрыть сделку
      </button>
    </>
  ) : (
    <>
      <button type="button" className="btn-secondary" onClick={onClose}>Отмена</button>
      {canPay && (
        <button
          type="button"
          className="btn-secondary"
          onClick={() => {
            onClose();
            openPayment({ studentId: target.studentId });
          }}
        >
          Внести оплату
        </button>
      )}
      <button
        type="button"
        className="btn-primary"
        disabled={pending}
        onClick={() => onConfirm({ comment: comment.trim() || undefined })}
      >
        Отметить как продлён
      </button>
    </>
  );

  return (
    <Dialog
      open
      onOpenChange={(o) => { if (!o) onClose(); }}
      title={lost
        ? `${target.studentName} уходит — закрыть сделку?`
        : `Продление: ${target.studentName}`}
      footer={footer}
    >
      {lost ? (
        <>
          <p className="renewal-close-dialog__text">
            Сделка уйдёт в архив и исчезнет с доски (ученик останется в системе).
            Найти её можно в списке через «Показать закрытые», вернуть — кнопкой
            «Переоткрыть» в карточке сделки.
          </p>
          <Field label="Причина ухода" required>
            <SelectInput
              value={reason}
              onChange={(e) => setReason(e.target.value as RenewalLostReason)}
              options={REASON_OPTIONS}
              placeholder="Выберите причину…"
            />
          </Field>
          <Field label="Комментарий" full>
            <Textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Подробности (необязательно)…"
              rows={2}
            />
          </Field>
        </>
      ) : (
        <>
          <p className="renewal-close-dialog__text">
            Оплата сама по себе сделку не закрывает — она только двигает
            стадию (Не было урока / Урок 1–3 / Ждём продление) вместе с
            балансом. Продление подтверждает менеджер отдельным явным действием.
          </p>
          <p className="renewal-close-dialog__text">
            Если оплата ещё не внесена в систему — сначала откройте форму
            кнопкой <b>«Внести оплату»</b>. Когда решение принято окончательно,
            нажмите <b>«Отметить как продлён»</b> — сделка закроется и появится
            сделка следующего цикла.
          </p>
          <Field label="Комментарий" full>
            <Textarea
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Подробности (необязательно)…"
              rows={2}
            />
          </Field>
        </>
      )}
    </Dialog>
  );
}
