import { fmtDateTime } from '../../lib/format';
import type { EntityComment } from '../../lib/entity-comments';

/**
 * Плитка «Последний комментарий» в шапке карточки (aside EntityHero).
 * Клик ведёт на вкладку с полной лентой.
 */
export function LastCommentCard({ comment, onOpen }: { comment: EntityComment; onOpen: () => void }) {
  return (
    <button
      type="button"
      className="hero-comment"
      onClick={onOpen}
      title="Открыть все комментарии"
    >
      <span className="hero-comment-head">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
        </svg>
        Последний комментарий
      </span>
      <span className="hero-comment-text">{comment.body}</span>
      <span className="hero-comment-meta">
        {comment.author_name || 'Неизвестный автор'} · {fmtDateTime(comment.created_at)}
      </span>
    </button>
  );
}
