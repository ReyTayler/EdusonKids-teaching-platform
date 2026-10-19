import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated } from '../lib/shared-types';
import type { StudentComment } from '../lib/student-comments';

const KEY = ['students', 'comments'] as const;

/**
 * Самый свежий комментарий ученика (для шапки карточки). Общий префикс ключа с
 * лентой — инвалидация после add/delete обновляет и шапку, и вкладку.
 */
export function useLatestStudentComment(studentId: number | null) {
  return useQuery({
    queryKey: [...KEY, studentId, 'latest'],
    queryFn: () =>
      api<Paginated<StudentComment>>(
        'GET',
        `/api/admin/students/${studentId}/comments?page=1&page_size=1`,
      ),
    enabled: !!studentId,
    select: (d) => d.rows[0] ?? null,
  });
}

/**
 * Лента комментариев ученика с накопительной пагинацией («Показать ещё»).
 * useInfiniteQuery сам хранит массив страниц и корректно перезагружает их при
 * инвалидации — без ручного накопления rows и риска дублей.
 */
export function useStudentComments(studentId: number | null, pageSize: number) {
  return useInfiniteQuery({
    queryKey: [...KEY, studentId, pageSize],
    queryFn: ({ pageParam }) =>
      api<Paginated<StudentComment>>(
        'GET',
        `/api/admin/students/${studentId}/comments?page=${pageParam}&page_size=${pageSize}`,
      ),
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.page * lastPage.page_size < lastPage.total ? lastPage.page + 1 : undefined,
    enabled: !!studentId,
  });
}

export function useStudentCommentMutations(studentId: number | null) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: [...KEY, studentId] });
  return {
    add: useMutation({
      mutationFn: (body: string) =>
        api<StudentComment>('POST', `/api/admin/students/${studentId}/comments`, { body }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (commentId: number) =>
        api('DELETE', `/api/admin/students/${studentId}/comments/${commentId}`),
      onSuccess: invalidate,
    }),
  };
}
