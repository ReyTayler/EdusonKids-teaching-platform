import type { ReactNode } from 'react';

export type BadgeTone = 'positive' | 'negative' | 'info' | 'muted';

/**
 * Бейдж состояния на общих тонах проекта (.status-badge в components.css).
 *
 * Тон — это смысл, а не украшение: зелёный «идёт хорошо», красный «сломалось»,
 * синий «в процессе», серый «ничего не происходит». Компонент нужен затем,
 * чтобы имя класса не переписывали руками на каждом экране и тон нельзя было
 * выбрать «покрасивее».
 */
export function StatusBadge({
  tone,
  children,
  dim,
}: {
  tone: BadgeTone;
  children: ReactNode;
  /** Приглушить — для вторичных пометок рядом с основной. */
  dim?: boolean;
}) {
  return (
    <span className={`status-badge status-badge--${tone}${dim ? ' status-badge--dim' : ''}`}>
      {children}
    </span>
  );
}

/** Статус документа базы знаний → тон и подпись. Одно место на весь раздел. */
export function DocumentStatusBadge({ status }: { status: 'draft' | 'published' }) {
  return status === 'published'
    ? <StatusBadge tone="positive">Опубликован</StatusBadge>
    : <StatusBadge tone="muted">Черновик</StatusBadge>;
}
