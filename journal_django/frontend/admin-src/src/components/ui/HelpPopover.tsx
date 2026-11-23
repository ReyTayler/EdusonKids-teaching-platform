import { useCallback, useId, useState, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import './help-popover.css';

/** Пояснение по нажатию: доступно с клавиатуры и на сенсорном экране. */
export function HelpPopover({ children, label = 'Подсказка' }: { children: ReactNode; label?: string }) {
  const [container, setContainer] = useState<HTMLElement | null>(null);
  const triggerRef = useCallback((node: HTMLButtonElement | null) => {
    setContainer(node?.closest<HTMLElement>('[role="dialog"]') ?? null);
  }, []);
  const descriptionId = useId();
  return (
    <Popover.Root>
      <span className="help-popover">
        <Popover.Trigger asChild>
          <button ref={triggerRef} type="button" className="help-popover__trigger" aria-label={label}
            onPointerDown={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}
            onClick={(e) => e.stopPropagation()}>
            <svg width="16" height="16" viewBox="0 0 20 20" fill="none" aria-hidden="true">
              <circle cx="10" cy="10" r="8" stroke="currentColor" strokeWidth="1.4" />
              <path d="M7.8 7.3a2.2 2.2 0 0 1 4.4 0c0 1.6-2.2 1.6-2.2 3.3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              <circle cx="10" cy="13.7" r=".85" fill="currentColor" />
            </svg>
          </button>
        </Popover.Trigger>
      </span>
      {/* Внутри модалки портал остаётся в её focus scope и scroll lock. */}
      <Popover.Portal container={container ?? undefined}>
        <Popover.Content className="help-popover__panel" side="bottom" align="start" sideOffset={6}
          collisionPadding={12} hideWhenDetached aria-label={label} aria-describedby={descriptionId}
          onClick={(e) => e.stopPropagation()} onEscapeKeyDown={(e) => e.stopPropagation()}>
          <div className="help-popover__text" id={descriptionId}>{children}</div>
          <Popover.Close className="help-popover__close" aria-label="Закрыть подсказку">×</Popover.Close>
          <Popover.Arrow className="help-popover__arrow" width={10} height={5} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
