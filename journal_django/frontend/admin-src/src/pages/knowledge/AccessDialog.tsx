import type { ReactNode } from 'react';
import { Dialog } from '../../components/ui/Dialog';
import { Button } from '../../components/ui/Button';

/**
 * Настройка доступа к документу — отдельным окном, а не полем над текстом.
 *
 * Права задают один раз и потом почти не трогают, поэтому держать их постоянно
 * на виду поверх редактора незачем: они оттягивали внимание от текста и мешали
 * читать первую строку. Кнопка «Доступ» в шапке открывает окно ровно тогда,
 * когда это нужно.
 *
 * Изменения применяются вместе с текстом, по кнопке «Сохранить» — отдельного
 * запроса на права нет, чтобы не расходились две половины одного документа.
 */
export function AccessDialog({
  open,
  onClose,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  /** Поле выбора ролей — приходит со страницы, чтобы состояние жило там же. */
  footer: ReactNode;
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { if (!next) onClose(); }}
      title="Доступ к документу"
      footer={<Button variant="primary" onClick={onClose}>Готово</Button>}
    >
      <div className="kb-access">
        {footer}
        <p className="kb-dialog__hint">
          Изменения вступят в силу после сохранения документа.
        </p>
      </div>
    </Dialog>
  );
}
