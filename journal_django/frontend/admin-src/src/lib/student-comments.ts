// Тип для комментариев к ученику. Бэкенд: /api/admin/students/:id/comments
// (Django+DRF, см. docs/superpowers/plans/2026-07-10-student-comments.md).

export interface StudentComment {
  id: number;
  body: string;
  created_at: string;
  author_id: number | null;
  author_name: string | null;
}
