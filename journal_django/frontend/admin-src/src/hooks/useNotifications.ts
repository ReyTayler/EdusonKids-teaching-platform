import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { api } from '../lib/api';
import type { Paginated } from '../lib/types';

// ─── Типы раздела «Уведомления» ───────────────────────────────────────────────
// Форма строк — из apps/notifications/serializers.py и views.NotificationScheduleView.

export interface NotificationRow {
  id: number;
  kind: string;
  channel: string;
  /** null у писем: адресат там — recipient_email. */
  chat_id: number | null;
  /** null для сообщений в общий чат и для писем родителям. */
  teacher_name: string | null;
  /** Адрес получателя — только у канала email. */
  recipient_email: string | null;
  /** Тема письма — только у канала email. */
  subject: string | null;
  text: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
  sent_at: string | null;
  source_kind: string | null;
  source_id: number | null;
}

export interface NotificationScheduleJob {
  key: string;
  title: string;
  schedule: string;
  /** null у служебной задачи «Отправка очереди» — она не имеет своего типа сообщений. */
  kind: string | null;
  last_run_at: string | null;
}

export interface NotificationSchedule {
  jobs: NotificationScheduleJob[];
  counts: Record<string, number>;
}

export interface NotificationsQuery {
  page: number;
  pageSize?: number;
  kind?: string;
  channel?: string;
  status?: string;
}

/**
 * Лента доставки. Фильтры — плоские query-параметры (бэкенд читает
 * `kind`/`channel`/`status` напрямую из query_params, без обёртки `filter[...]`,
 * которую использует журнал изменений).
 */
export function useNotifications({ page, pageSize = 50, kind, channel, status }: NotificationsQuery) {
  const params = new URLSearchParams();
  params.set('page', String(page));
  params.set('page_size', String(pageSize));
  if (kind) params.set('kind', kind);
  if (channel) params.set('channel', channel);
  if (status) params.set('status', status);
  const query = params.toString();

  return useQuery({
    queryKey: ['notifications', query],
    queryFn: () => api<Paginated<NotificationRow>>('GET', `/api/admin/notifications?${query}`),
    // Без keepPreviousData таблица мигает пустотой на каждой смене страницы/фильтра.
    placeholderData: keepPreviousData,
  });
}

export function useNotificationSchedule() {
  return useQuery({
    queryKey: ['notifications', 'schedule'],
    queryFn: () => api<NotificationSchedule>('GET', '/api/admin/notifications/schedule'),
  });
}

// ─── Общешкольный выключатель рассылки ────────────────────────────────────────
// apps/notifications/views.py NotificationToggleView. Выключено = сообщения не
// создаются вовсе (ни точечные, ни дайджесты), уже стоящие в очереди не
// отправляются; при включении обратно накопившееся задним числом не досылается.

export type NotificationToggle = {
  is_enabled: boolean;
  /** Отдельный флаг для писем родителям; по умолчанию выключен. */
  parent_reminders_enabled: boolean;
  updated_at: string;
  updated_by: string | null;
};

export type NotificationTogglePatch = Partial<Pick<NotificationToggle, 'is_enabled' | 'parent_reminders_enabled'>>;

const TOGGLE_KEY = ['notifications', 'toggle'] as const;

export function useNotificationToggle() {
  return useQuery({
    queryKey: TOGGLE_KEY,
    queryFn: () => api<NotificationToggle>('GET', '/api/admin/notifications/toggle'),
  });
}

/**
 * Частичное обновление выключателей. Оптимистично: переключатель сразу
 * показывает новое значение, при ошибке откатывается к прежнему; итог
 * всё равно сверяем с сервером (updated_by/updated_at приходят оттуда).
 */
export function useSetNotificationToggle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: NotificationTogglePatch) =>
      api<NotificationToggle>('POST', '/api/admin/notifications/toggle', patch),
    onMutate: async (patch) => {
      await qc.cancelQueries({ queryKey: TOGGLE_KEY });
      const prev = qc.getQueryData<NotificationToggle>(TOGGLE_KEY);
      if (prev) qc.setQueryData<NotificationToggle>(TOGGLE_KEY, { ...prev, ...patch });
      return { prev };
    },
    onError: (_err, _patch, ctx) => {
      if (ctx?.prev) qc.setQueryData(TOGGLE_KEY, ctx.prev);
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: TOGGLE_KEY });
    },
  });
}

// ─── Telegram-аккаунты и привязка к преподавателю ─────────────────────────────

export type TelegramAccount = {
  chat_id: number;
  username: string | null;
  full_name: string;
  /** ФИО преподавателя, если аккаунт уже привязан; иначе null. */
  bound_to: string | null;
};

/**
 * Аккаунты, известные боту — для выбора при привязке.
 *
 * `enabled` обязателен к передаче осознанно: эндпоинт закрыт для менеджера
 * (admin/superadmin, как и сама привязка), и запрос из-под менеджера вернёт
 * 403. Раньше хук вызывался безусловно, и менеджер, открывая карточку
 * преподавателя, тянул список впустую — выпадашку он всё равно не видит.
 */
export function useTelegramAccounts(enabled = true) {
  return useQuery({
    queryKey: ['telegram-users'],
    queryFn: () => api<{ rows: TelegramAccount[]; total: number }>('GET', '/api/admin/telegram-users'),
    enabled,
  });
}

/**
 * Привязка/отвязка Telegram-аккаунта преподавателю.
 *
 * Инвалидируем и `teachers` (поле `telegram` в карточке и списке), и
 * `telegram-users` (пометка «уже привязан» у аккаунтов): после привязки оба
 * ответа устарели.
 */
export function useTeacherTelegramMutations(teacherId: number) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['teachers'] });
    void qc.invalidateQueries({ queryKey: ['telegram-users'] });
  };
  return {
    link: useMutation({
      mutationFn: (chatId: number) =>
        api<unknown>('POST', `/api/admin/teachers/${teacherId}/telegram`, { chat_id: chatId }),
      onSuccess: invalidate,
    }),
    unlink: useMutation({
      mutationFn: () => api<void>('DELETE', `/api/admin/teachers/${teacherId}/telegram`),
      onSuccess: invalidate,
    }),
  };
}
