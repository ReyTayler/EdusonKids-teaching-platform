import type { ReactNode } from 'react';
import * as Dialog from '@radix-ui/react-dialog';

/** Общий для обеих SPA диалог с удержанием и восстановлением фокуса. */
export function ScheduleDialog({ title, children, onClose, busy = false }: {
  title: string; children: ReactNode; onClose: () => void; busy?: boolean;
}) {
  return (
    <Dialog.Root open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
      <Dialog.Portal>
        <Dialog.Overlay className="sched-dialog-overlay" />
        <Dialog.Content className="sched-dialog" aria-describedby={undefined}
          onInteractOutside={(e) => {
            if ((e.target as HTMLElement)?.closest('[data-floating-popover]')) e.preventDefault();
          }}>
          <Dialog.Title className="sched-dialog-title">{title}</Dialog.Title>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
