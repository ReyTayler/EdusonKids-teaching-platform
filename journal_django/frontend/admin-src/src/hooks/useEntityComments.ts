import {
  useInfiniteQuery, useMutation, useQuery, useQueryClient,
} from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated } from '../lib/shared-types';
import type { CommentEntity, EntityComment } from '../lib/entity-comments';

// Корень ключа — сегмент сущности: у учеников остаётся прежний ['students','comments'],
// у преподавателей ['teachers','comments'] (не пересекается с ключами списков).
const keyOf = (entity: CommentEntity) => [entity, 'comments'] as const;
const urlOf = (entity: CommentEntity, id: number | null) => `/api/admin/${entity}/${id}/comments`;

/**
 * Самый свежий комментарий (для шапки карточки). Общий префикс ключа с лентой —
 * инвалидация после add/delete обновляет и шапку, и вкладку.
 */
export function useLatestEntityComment(entity: CommentEntity, id: number | null) {
  return useQuery({
    queryKey: [...keyOf(entity), id, 'latest'],
    queryFn: () => api<Paginated<EntityComment>>('GET', `${urlOf(entity, id)}?page=1&page_size=1`),
    enabled: !!id,
    select: (d) => d.rows[0] ?? null,
  });
}

/**
 * Лента комментариев с накопительной пагинацией («Показать ещё»).
 * useInfiniteQuery сам хранит массив страниц и корректно перезагружает их при
 * инвалидации — без ручного накопления rows и риска дублей.
 */
export function useEntityComments(entity: CommentEntity, id: number | null, pageSize: number) {
  return useInfiniteQuery({
    queryKey: [...keyOf(entity), id, pageSize],
    queryFn: ({ pageParam }) =>
      api<Paginated<EntityComment>>('GET', `${urlOf(entity, id)}?page=${pageParam}&page_size=${pageSize}`),
    initialPageParam: 1,
    getNextPageParam: (lastPage) =>
      lastPage.page * lastPage.page_size < lastPage.total ? lastPage.page + 1 : undefined,
    enabled: !!id,
    // keepPreviousData намеренно нет: ключ меняется только при смене сущности,
    // и показывать на время загрузки ленту другого человека нельзя.
  });
}

export function useEntityCommentMutations(entity: CommentEntity, id: number | null) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: [...keyOf(entity), id] });
  return {
    add: useMutation({
      mutationFn: (body: string) => api<EntityComment>('POST', urlOf(entity, id), { body }),
      onSuccess: invalidate,
    }),
    remove: useMutation({
      mutationFn: (commentId: number) => api('DELETE', `${urlOf(entity, id)}/${commentId}`),
      onSuccess: invalidate,
    }),
  };
}
