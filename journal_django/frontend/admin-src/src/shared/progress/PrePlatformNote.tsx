import { preplatformNoteText } from '../../lib/labels';

/**
 * Плашка «Уроки 1–N пройдены до перехода на платформу». Один компонент на admin
 * и teacher (teacher берёт его через @shared): формулировка не должна разойтись.
 * Ничего не рисует, если офсета нет (обычная группа).
 */
export function PrePlatformNote({ offset, className }: { offset?: number | string | null; className?: string }) {
  const n = Number(offset);
  if (!Number.isFinite(n) || n <= 0) return null;
  return (
    <div className={`preplatform-note${className ? ` ${className}` : ''}`} role="note">
      {preplatformNoteText(n)}
    </div>
  );
}
