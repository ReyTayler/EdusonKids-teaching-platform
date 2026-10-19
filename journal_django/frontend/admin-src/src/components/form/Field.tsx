import { type ReactNode } from 'react';

interface Props {
  label: string;
  required?: boolean;
  error?: string;
  /** Растянуть поле на всю ширину в 2-колоночной сетке формы (.modal-form). */
  full?: boolean;
  children: ReactNode;
}

export function Field({ label, required, error, full, children }: Props) {
  return (
    <div className={`field${full ? ' field--full' : ''}`}>
      <label>{label}{required && <span className="field-req"> *</span>}</label>
      {children}
      {error && (
        <div className="field-error">{error}</div>
      )}
    </div>
  );
}
