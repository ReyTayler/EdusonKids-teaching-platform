// Комментарии к сущностям карточек (ученик, преподаватель). Бэкенд:
// /api/admin/students/:id/comments и /api/admin/teachers/:id/comments —
// одинаковый контракт, поэтому тип и хуки общие.

export interface EntityComment {
  id: number;
  body: string;
  created_at: string;
  author_id: number | null;
  author_name: string | null;
}

/** Сущности, у которых есть комментарии. Значение = сегмент URL и корень ключа кэша. */
export type CommentEntity = 'students' | 'teachers';
