import { Dialog } from './Dialog';

export interface ConfirmModalProps {
  title: string;
  message: string;
  confirmLabel: string;
  /** Красная кнопка подтверждения — для необратимых действий. */
  danger?: boolean;
  isPending: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

/**
 * Подтверждение опасного действия одним вопросом «да/нет».
 *
 * Нужен там, где действие живёт в меню «…»: меню закрывается по выбору пункта,
 * поэтому приём «нажми второй раз для подтверждения» прямо на кнопке там не
 * работает — подтверждать надо в отдельном слое.
 */
export function ConfirmModal({
  title, message, confirmLabel, danger, isPending, onConfirm, onClose,
}: ConfirmModalProps) {
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={title}
      footer={
        <div style={{ display: 'flex', gap: 'var(--space-2)' }}>
          <button type="button" className="btn-cancel" onClick={onClose} disabled={isPending}>
            Отмена
          </button>
          <button
            type="button"
            className={danger ? 'btn-danger' : 'btn-add'}
            onClick={onConfirm}
            disabled={isPending}
          >
            {isPending ? 'Подождите…' : confirmLabel}
          </button>
        </div>
      }
    >
      <p style={{ color: 'var(--text2)', margin: 0 }}>{message}</p>
    </Dialog>
  );
}
