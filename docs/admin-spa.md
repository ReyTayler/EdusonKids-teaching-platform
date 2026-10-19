# Admin SPA (web/admin/)

React 19 + TanStack Query v5 + React Router v7 + Radix UI Dialog + @dnd-kit + Recharts. Прод-сборка в `public/admin-dist/`. Server state только в TanStack Query.

## Структура src/

```
main.tsx, App.tsx
styles/                  # токены → base → shell → components → table → forms → modal → pages → overrides
providers/
  QueryProvider
  AuthProvider            # GET /api/auth/me → поле `me` (не `user`!)
  ThemeProvider
  PaymentModalProvider    # глобальный context для модалки оплаты
hooks/
  useAuth, useApiError
  useListSearchParams     # URL state sync для list-страниц
  useStudents / useStudentsAll, useGroups / useGroupsAll
  useLessons / useLessonsForGroup, usePayroll
  useTeachers, useTokens, useDirections, useMemberships
  useArchive, useStudentBalance
  usePayments / usePaymentMutations
  useDiscounts / useDiscountMutations
  useDashboard            # staleTime 30s
  useMonthlyFinance
lib/
  types.ts                # re-export shared/types
  api.ts                  # api<T>() + 401-handler → dispatch 'admin:auth-expired'
  format.ts               # fmtDate, fmtRub, fmtLessons
  labels.ts               # ENROLLMENT_STATUS_LABELS/OPTIONS, LESSON_TYPE_LABELS/OPTIONS
  pricing.ts, slots.ts (DOW + MONTHS_RU), direction-color.ts
  export-csv.ts           # UTF-8 BOM + `;` для Excel RU
  table-settings.ts
components/
  shell/                  # AuthGate, AppShell, Sidebar, ThemeToggle, ErrorBoundary, MobileNav
  ui/                     # Dialog (Radix), Toast, Skeleton, EmptyState, Pill, MonoBadge, DirTag
  form/                   # TextInput, NumberInput, Textarea, ColorInput, Field
                          # SelectInput, Combobox, DateInput, Checkbox  ← все native заменены
  table/
    DataTable.tsx          # client + server mode, sortable, per-column filter
    Paginator.tsx          # « 1 2 3 … N »
  detail/DetailShell.tsx
  memberships/MembershipsBlock.tsx
  lessons/                # LessonGrid, LessonEditor
  EntityLink, Avatar, StatusBadge
pages/
  dashboard/              # KpiCard, DebtsCard, FinanceCharts/MonthlyAreaChart (Recharts, lazy)
  accounts/               # admin-only
  audit/                  # admin-only
  students/               # List + Detail (StatsBlock + BalanceBlock + Memberships) + FormModal
  groups/                 # List + Detail + FormModal
  teachers/, tokens/, directions/, lessons/, archive/, settings/
  payroll/                # tabs Список/Сводка, date-range через DateInput
  payments/               # PaymentModal + BlockSelector
  subscriptions/          # табы Абонементы/Скидки (SubscriptionsView + DiscountsView)
```

## URL state sync (useListSearchParams)

Все paginated list-страницы держат state в URL:
```
?page=2&page_size=50&sort_by=age&sort_dir=desc&f.full_name=Иван&f.enrollment_status=enrolled
```
- Префикс `f.` для фильтров
- `getExtra`/`setExtra`/`setExtras` для нестандартных params (mode, date_from/date_to)
- `replace: true` — не засоряет history
- F5 сохраняет state, back/forward работает

## Известные баги (решены, держать в памяти)

### Filter focus-loss

Две независимые причины:

1. `ErrorBoundary` в `AppShell` с `key={location.key}` — ремоунтит на каждый `setSearchParams`.  
   **Правильно**: `key={location.pathname}` (сброс только при смене раздела).

2. `.data-table--loading` с `pointer-events: none` на всей таблице гасит фокус при isFetching.  
   **Правильно**: `pointer-events: none` только на `tbody`.

Общий инвариант: `placeholderData: keepPreviousData` обязателен во всех server-paginated хуках — иначе смена фильтра → `isLoading=true` → `<TableSkeleton/>` вместо `<DataTable/>` → ремоунт инпута.

### Sort-dir

`sort_dir === 'asc' ? 'asc' : default` превращало явный `'desc'` в default.  
**Правильно**: `(val==='asc'||val==='desc') ? val : default` — чинить в `parsePaginationRequest` И в `paginate()`.

## DB-схема (направления в UI)

- **`directions.color`** — `#RRGGBB` или NULL → frontend генерирует hue из hash имени
- **`--dir-color`** — set inline через style на компоненте; используется в tooltips, lesson-squares, dir-tags
- **`.id-cell` / `.cell-num` / `.mono`** — utility classes для tabular-nums + monospace font

## PG-соглашения (важные для репо)

- **`lesson_number` = numeric(5,1)** — полусчёт (1.5, 2.5 на 45-минутках)
- **`group_schedule_slots`** — отдельная таблица, UNIQUE(group_id, day_of_week, start_time)
- **`enrollment_status` + `frozen_until_month` CHECK**: `((status='frozen') = (frozen_until_month IS NOT NULL))`
- **`submitted_by_token` в lessons** — text, не FK (исторический токен мог уйти)
- **Auto-freeze**: при сохранении с `frozen|declined` фронт DELETE'ит memberships студента
- **Soft-delete**: `active=false` для teachers/groups/directions/tokens/discounts. У students soft-delete НЕТ: статус `not_enrolled` и DELETE-эндпоинт удалены (миграция students/0015), уход оформляется статусом `declined`
