import { useState, type FormEvent } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Field } from '../../components/form/Field';
import { NumberInput } from '../../components/form/NumberInput';
import { DateInput } from '../../components/form/DateInput';
import { Textarea } from '../../components/form/Textarea';
import { usePaymentMutations } from '../../hooks/usePayments';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';
import { todayMSK } from '../../lib/format';

interface Props {
  studentId: number;
  paymentId: number;
  subscriptionIndex: number;
  onClose: () => void;
}

/**
 * Доплата к конкретному абонементу: деньги без уроков, добивающие его цену
 * (недобор платежа со стороны банка). Баланс уроков не меняется, лимит курса не
 * занимается. См. docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
 */
export function SurchargeModal({ studentId, paymentId, subscriptionIndex, onClose }: Props) {
  const muts = usePaymentMutations();
  const showError = useApiError();
  const { toast } = useToast();
  const [amount, setAmount] = useState('');
  const [paidAt, setPaidAt] = useState(todayMSK());
  const [note, setNote] = useState('');

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!amount || Number(amount) <= 0) { toast('Введите сумму доплаты', 'error'); return; }
    try {
      await muts.surcharge.mutateAsync({
        student_id: studentId,
        parent_payment_id: paymentId,
        subscription_index: subscriptionIndex,
        total_amount: amount,
        paid_at: paidAt,
        note: note.trim() || null,
      });
      toast('Доплата внесена', 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`Доплата к абонементу №${subscriptionIndex}`}
      footer={
        <button type="submit" form="surcharge-form" className="btn-save"
                disabled={muts.surcharge.isPending}>
          Внести
        </button>
      }
    >
      <form id="surcharge-form" className="modal-form" onSubmit={(e) => { void onSubmit(e); }}>
        <p className="muted">
          Деньги добивают цену этого абонемента: уроков не прибавится, лимит курса
          не изменится. Отработанные деньги по его урокам пересчитаются, включая
          прошлые месяцы.
        </p>
        <Field label="Сумма, ₽" required full>
          <NumberInput min={1} step="0.01" value={amount}
                       onChange={(e) => setAmount(e.target.value)} required />
        </Field>
        <Field label="Дата поступления" required full>
          <DateInput value={paidAt} onChange={(e) => setPaidAt(e.target.value)} required />
        </Field>
        <Field label="Комментарий" full>
          <Textarea value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="например: недобор платежа банком" />
        </Field>
      </form>
    </Dialog>
  );
}
