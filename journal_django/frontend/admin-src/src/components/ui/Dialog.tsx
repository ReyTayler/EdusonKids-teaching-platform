import { HelpPopover } from './HelpPopover';
import * as RadixDialog from '@radix-ui/react-dialog';
import { useCallback, type ReactNode } from 'react';
import { handleShiftWheel } from './shiftWheelScroll';

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  help?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
  className?: string;
}

export function Dialog({ open, onOpenChange, title, help, children, footer, wide, className }: DialogProps) {
  // Shift+колесо внутри модалки: замок прокрутки Radix (react-remove-scroll)
  // гасит его в Chromium — подробности в shiftWheelScroll.ts. Callback-ref с
  // очисткой (React 19) надёжно ловит монтирование содержимого через портал.
  const bodyRef = useCallback((node: HTMLDivElement | null) => {
    if (!node) return;
    const onWheel = (e: WheelEvent) => handleShiftWheel(e, node);
    node.addEventListener('wheel', onWheel, { passive: false });
    return () => node.removeEventListener('wheel', onWheel);
  }, []);

  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="modal-overlay">
          <RadixDialog.Content
            className={`modal${wide ? ' modal--wide' : ''}${className ? ` ${className}` : ''}`}
            aria-describedby={undefined}
            onInteractOutside={(e) => {
              // Выпадающие списки/календари рендерятся порталом в body (вне модалки).
              // Клик по ним не должен закрывать саму модалку.
              const t = e.target as HTMLElement | null;
              if (t?.closest('[data-floating-popover]')) e.preventDefault();
            }}
          >
            <div className="modal-header">
              <div className="help-label"><RadixDialog.Title className="modal-title">{title}</RadixDialog.Title>
                {help && <HelpPopover label={`Справка: ${title}`}>{help}</HelpPopover>}
              </div>
              <RadixDialog.Close className="modal-close" aria-label="Закрыть">×</RadixDialog.Close>
            </div>
            <div className="modal-body" ref={bodyRef}>{children}</div>
            {footer && <div className="modal-footer">{footer}</div>}
          </RadixDialog.Content>
        </RadixDialog.Overlay>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
