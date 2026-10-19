import { type InputHTMLAttributes } from 'react';

export function NumberInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" {...props} />;
}
