import { Dialog } from '../../components/ui/Dialog';
import { fmtRub, fmtLessons } from '../../lib/format';
import { usePaymentMutations } from '../../hooks/usePayments';
import { useApiError } from '../../hooks/useApiError';
import { useToast } from '../../components/ui/Toast';

interface Props {
  open: boolean;
  onClose: () => void;
  studentId: number;
  remainingValue: number;
  remainingLessons: number;
}

export function RefundModal({ open, onClose, studentId, remainingValue, remainingLessons }: Props) {
  const muts = usePaymentMutations();
  const showError = useApiError();
  const { toast } = useToast();

  const handleConfirm = async () => {
    try {
      const res = await muts.refund.mutateAsync(studentId);
      toast(`Возврат оформлен: ${fmtRub(res.refunded_amount)}`, 'ok');
      onClose();
    } catch (err) { showError(err); }
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()} title="Возврат средств">
      <div className="refund-modal">
        <p>Будет списан весь неотработанный остаток ученика:</p>
        <div className="refund-modal__amount">
          К возврату клиенту: <strong>{fmtRub(remainingValue)}</strong>
        </div>
        <div className="refund-modal__lessons muted">
          Сгорит {fmtLessons(remainingLessons)} оплаченных уроков. Баланс станет 0.
        </div>
        <div className="payment-form__footer">
          <button type="button" className="btn-cancel" onClick={onClose}>Отмена</button>
          <button type="button" className="btn-save" onClick={() => { void handleConfirm(); }}
            disabled={muts.refund.isPending || remainingValue <= 0}>
            Подтвердить возврат
          </button>
        </div>
      </div>
    </Dialog>
  );
}
