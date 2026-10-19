import { createContext, useContext, useState, useCallback, type ReactNode } from 'react';

type ToastKind = 'ok' | 'error' | 'info';

/** Действие в toast'е — обычно «Отменить» у обратимой операции. */
export interface ToastAction {
  label: string;
  onClick: () => void;
}

export interface ToastOptions {
  kind?: ToastKind;
  /** Сколько висит, мс. Toast с действием должен жить дольше — его надо успеть нажать. */
  duration?: number;
  actions?: ToastAction[];
}

interface ToastItem extends ToastOptions { id: number; message: string; }

const DEFAULT_DURATION = 3000;
const ACTION_DURATION = 8000;

const ToastContext = createContext<{
  toast: (msg: string, kindOrOptions?: ToastKind | ToastOptions) => void;
}>(null!);

let nextId = 1;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  // Второй аргумент остался совместимым со старым `toast(msg, 'ok')` — вызовов
  // по всему admin SPA несколько десятков, переписывать их незачем.
  const toast = useCallback((
    message: string,
    kindOrOptions: ToastKind | ToastOptions = 'info',
  ) => {
    const options: ToastOptions = typeof kindOrOptions === 'string'
      ? { kind: kindOrOptions }
      : kindOrOptions;
    const id = nextId++;
    const hasActions = !!options.actions?.length;
    const duration = options.duration
      ?? (hasActions ? ACTION_DURATION : DEFAULT_DURATION);
    setItems((prev) => [...prev, { ...options, id, message, kind: options.kind ?? 'info' }]);
    setTimeout(() => dismiss(id), duration);
  }, [dismiss]);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div className="toast-container">
        {items.map((t) => (
          <div key={t.id} className={`toast toast--${t.kind}`}>
            <span className="toast__text">{t.message}</span>
            {t.actions?.map((action) => (
              <button
                key={action.label}
                type="button"
                className="toast__action"
                onClick={() => { dismiss(t.id); action.onClick(); }}
              >
                {action.label}
              </button>
            ))}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() { return useContext(ToastContext); }

export function showApiError(
  err: unknown,
  toast: (m: string, k?: ToastKind | ToastOptions) => void,
) {
  if (typeof err === 'object' && err && 'message' in err) {
    toast(String((err as { message: unknown }).message), 'error');
  } else {
    toast('Ошибка', 'error');
  }
}
