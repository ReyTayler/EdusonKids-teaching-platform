import { Dialog } from '../ui/Dialog';

interface Props {
  /** Текст блокировки из ответа бэкенда (ApiError.message). */
  message: string;
  onClose: () => void;
}

/**
 * Блокирующая модалка: снять ученика из группы нельзя, пока по его пропускам в
 * этой группе есть НАЗНАЧЕННЫЕ доп.уроки. Показывается по коду ошибки
 * MEMBERSHIP_HAS_SCHEDULED_MAKEUPS (lib/api.scheduledMakeupsBlockMessage) при
 * удалении/переводе/деактивации членства и смене статуса ученика.
 */
export function ScheduledMakeupsBlockModal({ message, onClose }: Props) {
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title="Есть назначенные доп.уроки"
      footer={<button type="button" className="btn-primary" onClick={onClose}>Понятно</button>}
    >
      <p className="transfer-modal__text">{message}</p>
    </Dialog>
  );
}
