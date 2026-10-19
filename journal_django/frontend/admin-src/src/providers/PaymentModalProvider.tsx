import { createContext, useCallback, useContext, useState, type ReactNode } from 'react';
import { PaymentModal } from '../pages/payments/PaymentModal';

interface ModalState {
  open: boolean;
  studentId?: number;
  directionId?: number;
}

interface ContextValue {
  open: (opts?: { studentId?: number; directionId?: number }) => void;
}

const Ctx = createContext<ContextValue | null>(null);

export function PaymentModalProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<ModalState>({ open: false });

  const open = useCallback((opts?: { studentId?: number; directionId?: number }) => {
    setState({ open: true, studentId: opts?.studentId, directionId: opts?.directionId });
  }, []);
  const close = useCallback(() => setState((s) => ({ ...s, open: false })), []);

  return (
    <Ctx.Provider value={{ open }}>
      {children}
      <PaymentModal
        open={state.open}
        studentId={state.studentId}
        directionId={state.directionId}
        onClose={close}
      />
    </Ctx.Provider>
  );
}

export function usePaymentModal(): ContextValue {
  const v = useContext(Ctx);
  if (!v) throw new Error('usePaymentModal must be used within PaymentModalProvider');
  return v;
}
