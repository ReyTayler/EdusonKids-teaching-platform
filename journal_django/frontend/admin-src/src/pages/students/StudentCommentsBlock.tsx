import { useState, type KeyboardEvent } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { Textarea } from '../../components/form/Textarea';
import { fmtDateTime } from '../../lib/format';
import { canDeleteStudentComments, type Role } from '../../lib/permissions';
import { useStudentComments, useStudentCommentMutations } from '../../hooks/useStudentComments';
import type { StudentComment } from '../../lib/student-comments';

const PAGE_SIZE = 20;
const MAX_LEN = 5000;

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return (name.trim().slice(0, 2) || '??').toUpperCase();
}

function hueOf(s: string): number {
  return [...s].reduce((a, c) => a + c.charCodeAt(0), 0) % 360;
}

interface Props {
  studentId: number;
}

export default function StudentCommentsBlock({ studentId }: Props) {
  const { me } = useAuth();
  const canDelete = canDeleteStudentComments(me?.role as Role);

  const [text, setText] = useState('');
  const [confirmId, setConfirmId] = useState<number | null>(null);

  const {
    data, isLoading, hasNextPage, fetchNextPage, isFetchingNextPage,
  } = useStudentComments(studentId, PAGE_SIZE);
  const { add, remove } = useStudentCommentMutations(studentId);

  // useInfiniteQuery хранит страницы сам — просто разворачиваем в плоский список.
  const rows: StudentComment[] = data?.pages.flatMap((p) => p.rows) ?? [];

  const submit = () => {
    const body = text.trim();
    if (!body) return;
    add.mutate(body, { onSuccess: () => setText('') });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      submit();
    }
  };

  const confirmDelete = (id: number) => {
    remove.mutate(id, { onSuccess: () => setConfirmId(null) });
  };

  const remaining = MAX_LEN - text.length;
  const showEmpty = !isLoading && rows.length === 0;

  return (
    <div className="comments">
      <div className="comments__composer">
        <Textarea
          className="comments__input"
          value={text}
          maxLength={MAX_LEN}
          rows={3}
          placeholder="Оставьте комментарий об ученике…"
          aria-label="Новый комментарий"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
        <div className="comments__composer-foot">
          <span className="comments__hint">
            {remaining <= 200 ? `Осталось ${remaining}` : 'Ctrl + Enter — отправить'}
          </span>
          <button
            type="button"
            className="btn-add"
            disabled={!text.trim() || add.isPending}
            onClick={submit}
          >
            {add.isPending ? 'Добавляем…' : 'Добавить'}
          </button>
        </div>
      </div>

      {isLoading && rows.length === 0 ? (
        <div className="comments__loading">Загружаем комментарии…</div>
      ) : showEmpty ? (
        <div className="comments__empty">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" />
          </svg>
          <p>Пока нет комментариев</p>
          <span>Здесь появятся заметки об ученике</span>
        </div>
      ) : (
        <ul className="comments__list">
          {rows.map((c) => {
            const name = c.author_name || 'Неизвестный автор';
            const hue = hueOf(name);
            return (
              <li key={c.id} className="comments__item">
                <div
                  className="comments__avatar"
                  style={{
                    background: `hsl(${hue},55%,92%)`,
                    borderColor: `hsl(${hue},50%,80%)`,
                    color: `hsl(${hue},55%,35%)`,
                  }}
                  aria-hidden="true"
                >
                  {initialsOf(name)}
                </div>
                <div className="comments__body">
                  <div className="comments__head">
                    <span className="comments__author">{name}</span>
                    <time className="comments__time">{fmtDateTime(c.created_at)}</time>
                    {canDelete && (confirmId === c.id ? (
                      <span className="comments__confirm">
                        <button
                          type="button"
                          className="comments__confirm-yes"
                          disabled={remove.isPending}
                          onClick={() => confirmDelete(c.id)}
                        >
                          Удалить
                        </button>
                        <button
                          type="button"
                          className="comments__confirm-no"
                          onClick={() => setConfirmId(null)}
                        >
                          Отмена
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        className="comments__delete"
                        title="Удалить комментарий"
                        aria-label="Удалить комментарий"
                        onClick={() => setConfirmId(c.id)}
                      >
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                          <path d="M3 6h18" />
                          <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                          <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                        </svg>
                      </button>
                    ))}
                  </div>
                  <p className="comments__text">{c.body}</p>
                </div>
              </li>
            );
          })}
        </ul>
      )}

      {hasNextPage && (
        <button
          type="button"
          className="btn-secondary comments__more"
          disabled={isFetchingNextPage}
          onClick={() => fetchNextPage()}
        >
          {isFetchingNextPage ? 'Загрузка…' : 'Показать ещё'}
        </button>
      )}
    </div>
  );
}
