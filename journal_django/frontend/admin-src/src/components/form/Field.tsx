import { HelpPopover } from '../ui/HelpPopover';
import { type ReactNode } from 'react';

interface Props {
  label: string;
  required?: boolean;
  help?: ReactNode;
  error?: string;
  /** Растянуть поле на всю ширину в 2-колоночной сетке формы (.modal-form). */
  full?: boolean;
  children: ReactNode;
}

export function Field({ label, required, error, help, full, children }: Props) {
  return (
    <div className={`field${full ? ' field--full' : ''}`}>
      {(label || required || help) && <div className="field__label-row">
        <label>{label}{required && <span className="field-req"> *</span>}</label>
        {help && <HelpPopover label={`Подсказка: ${label}`}>{help}</HelpPopover>}
      </div>}
      {children}
      {error && (
        <div className="field-error">{error}</div>
      )}
    </div>
  );
}
