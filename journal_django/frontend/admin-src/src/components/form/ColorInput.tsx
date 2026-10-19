import { type InputHTMLAttributes } from 'react';

export function ColorInput(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="color" {...props} />;
}
