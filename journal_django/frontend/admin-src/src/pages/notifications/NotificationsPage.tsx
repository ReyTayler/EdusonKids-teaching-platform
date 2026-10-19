import { useState } from 'react';
import { useNotifications, type NotificationRow } from '../../hooks/useNotifications';
import { useListSearchParams } from '../../hooks/useListSearchParams';
import { DataTable, type Column } from '../../components/table/DataTable';
import { TableSkeleton } from '../../components/ui/Skeleton';
import { EmptyState } from '../../components/ui/EmptyState';
import { Tabs, type TabItem } from '../../components/ui/Tabs';
import { PageHeader } from '../../components/shell/PageHeader';
import { fmtDateTime } from '../../lib/format';
import {
  NOTIFICATION_CHANNEL_LABELS,
  NOTIFICATION_CHANNEL_OPTIONS,
  NOTIFICATION_KIND_LABELS,
  NOTIFICATION_KIND_OPTIONS,
  NOTIFICATION_STATUS_OPTIONS,
} from '../../lib/labels';
import { NotificationDetailModal, NotificationStatusBadge } from './NotificationDetailModal';
import { NotificationToggleBar } from './NotificationToggleBar';
import { SchedulePanel } from './SchedulePanel';

const TABS = ['log', 'schedule'] as const;
type NotificationsTab = (typeof TABS)[number];
const DEFAULT_TAB: NotificationsTab = 'log';

function isTab(v: string | undefined): v is NotificationsTab {
  return !!v && (TABS as readonly string[]).includes(v);
}

/**
 * Первая строка сообщения. Тексты многострочные (дайджест — список занятий),
 * в ячейку влезает только зачин; целиком читается в модалке.
 */
function firstLine(text: string): string {
  const line = text.split('\n', 1)[0].trim();
  return line || '—';
}

/** Лента доставки: фильтры, серверная пагинация, модалка полного текста. */
function NotificationsLog() {
  const {
    page, pageSize, sortBy, sortDir, filters,
    setPage, setPageSize, setSort, setFilters,
  } = useListSearchParams({ sortBy: 'created_at', sortDir: 'desc' });

  const [opened, setOpened] = useState<NotificationRow | null>(null);

  const { data, isLoading, isFetching } = useNotifications({
    page,
    pageSize,
    kind: filters.kind,
    channel: filters.channel,
    status: filters.status,
  });

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const hasFilters = Object.values(filters).some((v) => v);

  const columns: Column<NotificationRow>[] = [
    {
      key: 'kind',
      label: 'Тип',
      width: '13rem',
      sortable: false,
      searchable: true,
      searchOptions: NOTIFICATION_KIND_OPTIONS,
      cell: (r) => NOTIFICATION_KIND_LABELS[r.kind] ?? r.kind,
    },
    {
      key: 'channel',
      label: 'Канал',
      width: '8rem',
      sortable: false,
      searchable: true,
      searchOptions: NOTIFICATION_CHANNEL_OPTIONS,
      cell: (r) => NOTIFICATION_CHANNEL_LABELS[r.channel] ?? r.channel,
    },
    {
      key: 'teacher_name',
      label: 'Получатель',
      width: '13rem',
      sortable: false,
      // Сообщение в общий чат персонального адресата не имеет — показываем chat_id.
      cell: (r) => r.teacher_name ?? <span className="mono">{r.chat_id}</span>,
    },
    {
      key: 'text',
      label: 'Текст',
      sortable: false,
      cell: (r) => <span className="notifications-text">{firstLine(r.text)}</span>,
    },
    {
      key: 'status',
      label: 'Статус',
      width: '9rem',
      sortable: false,
      searchable: true,
      searchOptions: NOTIFICATION_STATUS_OPTIONS,
      cell: (r) => <NotificationStatusBadge status={r.status} />,
    },
    {
      key: 'created_at',
      label: 'Время',
      width: '10rem',
      sortable: false,
      cell: (r) => fmtDateTime(r.created_at),
    },
  ];

  if (isLoading) return <TableSkeleton rows={12} cols={6} />;

  // Пустая таблица без объяснений — тупик: разводим «ещё ничего не слали» и
  // «под фильтр ничего не попало».
  if (rows.length === 0 && !hasFilters) {
    return (
      <EmptyState hint="Сообщения появятся здесь, как только бот начнёт их отправлять.">
        Сообщений пока нет
      </EmptyState>
    );
  }

  return (
    <>
      <DataTable<NotificationRow>
        data={rows}
        columns={columns}
        title="Журнал уведомлений"
        isLoading={isFetching}
        onRowClick={(r) => setOpened(r)}
        serverPagination={{
          page,
          pageSize,
          total,
          sortBy,
          sortDir,
          filters,
          onPageChange:     setPage,
          onPageSizeChange: setPageSize,
          onSortChange:     setSort,
          onFiltersChange:  setFilters,
        }}
      />
      {opened && (
        <NotificationDetailModal row={opened} onClose={() => setOpened(null)} />
      )}
    </>
  );
}

export default function NotificationsPage() {
  const { getExtra, setExtra } = useListSearchParams({ sortBy: 'created_at', sortDir: 'desc' });
  const raw = getExtra('tab');
  const activeTab: NotificationsTab = isTab(raw) ? raw : DEFAULT_TAB;

  const tabs: TabItem[] = [
    { value: 'log',      label: 'Журнал',     content: <NotificationsLog /> },
    { value: 'schedule', label: 'Расписание', content: <SchedulePanel /> },
  ];

  return (
    <>
      <PageHeader
        title="Уведомления и напоминания"
        sub="Что бот отправил преподавателям и когда в последний раз отрабатывали регулярные задачи."
      />
      <NotificationToggleBar />
      <Tabs
        items={tabs}
        value={activeTab}
        onChange={(v) => setExtra('tab', v === DEFAULT_TAB ? null : v)}
      />
    </>
  );
}
