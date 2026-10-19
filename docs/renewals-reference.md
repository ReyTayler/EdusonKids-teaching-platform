# Продления — справочник по реализованному функционалу

> Снимок состояния на 2026-07-08. Описывает то, что реально есть в коде (`apps/renewals/`,
> `frontend/admin-src/src/pages/renewals/`), а не то, что задумывалось в исходном плане
> (`docs/renewals-plan.md`, `docs/superpowers/plans/2026-07-08-renewals-crm-pipeline.md`).
> Где реализация разошлась с планом или чего-то не хватает — отмечено в разделе «Чего нет».

---

## 1. Что это

CRM-воронка продлений учеников. Единица работы — **сделка** (`RenewalDeal`) = связка
**ученик × направление × номер цикла** (цикл = 1 оплаченный месяц = 4 урока). Сделки
не заводятся руками — движок сам порождает и закрывает их по фактам оплаты и посещаемости.
Менеджер работает со сделками через канбан или список, двигая их между стадиями.

**Доступ:** `/admin/renewals` и все API — роли `manager / admin / superadmin`
(`IsManagerOrAdmin`). Настройка стадий воронки — только `superadmin`
(`ReadStaffWriteSuperAdmin`: читают все staff, пишут только superadmin).

---

## 2. Доменная модель

4 таблицы, все `managed=True`, все под `@pghistory.track` (журнал изменений).

### `renewal_pipeline`
Воронка. Практически всегда одна (`is_default=True`, UNIQUE-констрейнт гарантирует
единственность дефолтной воронки). Поддержка нескольких воронок — не используется нигде
в коде (везде `RenewalPipeline.objects.get(is_default=True)`), хотя схема это позволяет.

### `renewal_stage`
Стадия воронки. Поля: `key` (машинный, уникален в рамках воронки), `label`, `color`
(`#RRGGBB` или `NULL`), `sort_order`, `kind` (`progress|decision|won|lost`), `is_auto`
(двигает движок vs менеджер руками).

**Текущий состав воронки (10 стадий, `sort_order` по порядку):**

| # | key | label | kind | is_auto |
|---|---|---|---|---|
| 0 | `lesson_1` | Урок 1 | progress | ✅ |
| 1 | `lesson_2` | Урок 2 | progress | ✅ |
| 2 | `lesson_3` | Урок 3 | progress | ✅ |
| 3 | `lesson_4` | Урок 4 | progress | ✅ |
| 4 | `awaiting_payment` | Ждём оплату | decision | — |
| 5 | `thinking` | Думает | decision | — |
| 6 | `frozen` | Заморожен | decision | — |
| 7 | `ignoring` | Игнорит | decision | — |
| 8 | `renewed` | Продлён | won | — |
| 9 | `churned` | Ушёл | lost | — |

`won`/`lost` — терминальные: переход ИЗ них запрещён валидатором (`transitions.py`)
независимо от того, кто/что инициирует перевод.

Через настройку стадий (superadmin) можно добавлять **свои** `decision`-стадии
(и даже `progress`/`won`/`lost` — сериализатор это не запрещает), переименовывать,
красить, переставлять порядком, удалять. См. §7.

### `renewal_deal`
Сама сделка. `UNIQUE(student, direction, cycle_no)` — идемпотентность генерации.
`stage`/`pipeline`/`student`/`direction` — `RESTRICT` (нельзя физически удалить ученика/
направление/стадию, если на неё есть сделки — сначала переносите сделки). `assignee` —
`SET NULL` (учётку менеджера можно удалить, сделка не потеряется). `outcome_at IS NOT NULL`
⇒ сделка закрыта (won/lost), выходит из активной доски.

Хранится: `expected_amount`, `next_touch_at`, `reason_code`, `stage_entered_at`,
`created_at`/`updated_at`. **Не хранится** (вычисляется на чтении): прогресс урока
в цикле, баланс, «дней в стадии» — согласно инварианту проекта «балансы выводятся,
не хранятся».

### `renewal_activity`
Таймлайн сделки. `kind`: `stage_change | comment | payment_linked | system`. Только
`Insert`+`Delete` под pghistory (это уже сам лог — откатывать `Update` незачем).
Создаётся движком (`system`, `payment_linked`) и вручную (`comment` через API,
`stage_change` при любом `move`).

---

## 3. Как сделка живёт — весь жизненный цикл

```
Активное членство в группе (group_memberships.active=true)
        │
        ▼
  ensure_deal(student, direction, cycle_no)  ─── идемпотентно, UNIQUE-констрейнт
        │  создаёт сделку на первой auto-стадии (Урок 1), если её ещё нет
        ▼
  ┌─────────────────────────────────────────────────────────┐
  │  Урок 1 → Урок 2 → Урок 3 → Урок 4   (progress, auto)    │
  │  двигает sync_lesson_stage() по факту посещаемости       │
  └─────────────────────────────────────────────────────────┘
        │                                    │
        │ менеджер решил (drag на канбане    │ пришла оплата
        │ / POST move)                       │ (signal on Payment)
        ▼                                    ▼
  Ждём оплату / Думает /              close_deal_won():
  Заморожен / Игнорит                 стадия → «Продлён»,
  (decision, руками)                  outcome_at = now(),
        │                             + ensure_deal(cycle+1)
        │ move → won/lost                    │
        ▼                                    │
  Продлён ✅ / Ушёл ❌  ◄──────────────────────┘
  (терминал, outcome_at заполнен, дальше переходов нет)
        │
        │ если won — ещё раз ensure_deal(cycle_no+1)
        ▼
  Новая сделка следующего цикла на «Урок 1»
```

### 3.1 Кто и как двигает стадию

| Триггер | Функция | Что делает | Где вызывается |
|---|---|---|---|
| Новое активное членство / первый прогон | `engine.ensure_deal()` | Создаёт сделку на первой auto-стадии, если её ещё нет (get_or_create по UNIQUE) | сигнал оплаты (после закрытия), `move_deal` (после won), ночная команда `rebuild_renewal_deals` |
| Урок отмечен/снят/удалён | `engine.sync_lesson_stage()` через `sync_lesson_stage_safe()` | Пересчитывает посещённые уроки в цикле и переставляет сделку на нужную `lesson_N`-стадию | `apps.teacher_spa.services.submit_lesson` (главный поток — учитель отмечает урок), `apps.lessons.repository.create_lesson_full` / `update_attendance_cell` / `delete_lesson_full` (admin-путь) |
| Оплата создана | `signals.on_payment_created` → `engine.close_deal_won()` | Закрывает открытую сделку как «Продлён», порождает сделку следующего цикла | `post_save` на `Payment` (только ORM-создание, `Payment.objects.create`) |
| Менеджер перетащил карточку / вызвал API | `repository.move_deal()` | Валидирует переход (`transitions.assert_allowed`), переносит стадию, пишет activity; если стадия `won` — тоже порождает следующий цикл | `POST /:id/move` |
| Любое из вышеперечисленного не сработало | `rebuild_renewal_deals` (management-команда) | Для каждого активного (ученик×направление) — гарантирует сделку текущего цикла + подтягивает `sync_lesson_stage` | ручной запуск / должно быть в cron (см. §9) |

### 3.2 Защитные правила движка

- **Авто-прогресс никогда не переопределяет ручное решение.** `sync_lesson_stage` трогает
  сделку только если её текущая стадия `kind == 'progress'`. Как только менеджер увёл
  карточку в «Ждём оплату»/«Думает»/что угодно ещё — авто-прогресс для неё замирает
  навсегда (до закрытия и респавна следующего цикла, который снова стартует с «Урок 1»).
- **Сбой CRM-логики никогда не роняет основной поток.** Оплата (`Payment`) и посещаемость
  (`LessonAttendance`/`lessons_done`) — денежный/учебный инвариант проекта. Вызовы в
  renewals всегда идут через `transaction.on_commit(...)` + `try/except` с логированием
  (`logger.exception`). Если движок упал — оплата или урок всё равно сохранились,
  ночная команда самозаживления досоздаст/поправит сделку позже.
- **Идемпотентность everywhere.** `ensure_deal` — `get_or_create` поверх
  `UNIQUE(student,direction,cycle_no)`. `sync_lesson_stage` — no-op, если сделка уже
  на нужной стадии. `close_deal_won` — no-op, если открытой сделки нет.
- **Терминальные стадии заморожены.** Переход ИЗ `won`/`lost` запрещён валидатором
  (`transitions.is_allowed`) — не важно, drag это или прямой API-вызов.

---

## 4. API — полный справочник

Базовый путь: `/api/admin/renewals`. Все — JWT-cookie, `APPEND_SLASH=False`.

| Метод | Путь | Права | Тело запроса | Ответ |
|---|---|---|---|---|
| GET | `?view=board&filter[assignee_id]=&filter[direction_id]=&filter[overdue]=true` | staff | — | `{columns: [{stage_id,key,label,kind,color,count,sum_potential,cards:[...]}]}`, до 50 карточек на колонку |
| GET | `?view=list&page=&page_size=&sort_by=&sort_dir=&filter[assignee_id]=&filter[direction_id]=&filter[stage_id]=` | staff | — | `{rows:[...], total, page, page_size}` |
| GET | `/columns/:stage_id?offset=&filter[...]=` | staff | — | `RenewalCard[]` — «Показать ещё» одной колонки, та же сортировка/фильтры, что и в board |
| GET | `/:id` | staff | — | детальная сделка + вычисляемые поля (`lesson_in_cycle`, `balance`, `days_in_stage`) |
| PATCH | `/:id` | staff | `{assignee_id?, next_touch_at?, reason_code?, expected_amount?}` | обновлённая детальная сделка |
| POST | `/:id/move` | staff | `{to_stage_id, reason_code?}` | 200 детальная сделка / **409** `{error}` если переход запрещён |
| POST | `/:id/comment` | staff | `{body}` | 201 `{id, created_at}` |
| GET | `/:id/activity` | staff | — | `RenewalActivityItem[]`, новые сверху |
| GET | `/stages` | staff (read) | — | `RenewalStage[]` дефолтной воронки, по `sort_order` |
| POST | `/stages` | **superadmin** | `{label, kind, color?, key?}` | 201 новая стадия (`is_auto=False` всегда) |
| PATCH | `/stages/:id` | **superadmin** | `{label?, color?, kind?}` (partial) | обновлённая стадия |
| DELETE | `/stages/:id` | **superadmin** | — | 204 / **409** `{error: 'has_open_deals'|'protected'}` |
| POST | `/stages/reorder` | **superadmin** | `{order: number[]}` (id стадий в новом порядке) | обновлённый список стадий |
| GET | `/analytics?group_by=` | staff | — | `{stages:[{key,label,kind,cnt,sum_amt}], renewal_rate_30d, won_30d, lost_30d}` (`group_by` принимается, но **не используется**) |

Числовые query-параметры (`page`, `page_size`, `offset`, `filter[assignee_id\|direction_id\|stage_id]`)
валидируются на входе — нечисловое значение даёт `400`, а не `500`.

**Асимметрия фильтров** (реальная, не баг, но важно знать): `board`/`column_cards`
понимают `assignee_id/direction_id/overdue`; `list_deals` понимает
`assignee_id/direction_id/stage_id/include_closed`, но **не** `overdue`. Список и доска
фильтруются не полностью одинаково.

---

## 5. Фронтенд

### 5.1 Роуты

| Путь | Компонент | Права (`RequireRole`) |
|---|---|---|
| `/admin/renewals` | `RenewalsPage` (канбан/список, тумблер в URL `?view=`) | manager/admin/superadmin |
| `/admin/renewals/analytics` | `RenewalAnalyticsPage` (lazy-chunk — тянет Recharts) | manager/admin/superadmin |
| `/admin/renewals/stages` | `RenewalStagesSettings` | superadmin |

Пункт «Продления» есть в основном сайдбаре (между «Абонементы» и «Зарплата»),
виден всем staff безусловно (доп. RBAC-фильтрации в `Sidebar.tsx` нет — полагается
на API). Ссылки «Аналитика» / «Настройка стадий» — в шапке самой страницы `/admin/renewals`
(вторая видна только superadmin).

### 5.2 Компоненты (`pages/renewals/`)

| Файл | Что делает |
|---|---|
| `RenewalsPage.tsx` | Оболочка: тумблер Канбан/Список, ссылки на аналитику/настройку, состояние выбранной карточки → `RenewalDrawer` |
| `RenewalBoard.tsx` | `DndContext` + `DragOverlay` (портал в `document.body`, чтобы карточка не обрезалась overflow колонки при перетаскивании). Оптимистичное перемещение карточки между колонками с откатом + toast на ошибку (409 и т.п.) |
| `RenewalColumn.tsx` | Одна колонка: шапка (label + count + sum_potential), карточки, кнопка «Показать ещё» (реально дозагружает через `GET /columns/:id`, локальный стейт, сброс при смене фильтров) |
| `RenewalCardView.tsx` | Карточка (draggable) + `RenewalCardContent` — переиспользуемая разметка (используется также в `DragOverlay`). SLA-бейдж красным, если `days_in_stage > 5` |
| `RenewalList.tsx` | Списочный вид через общий `DataTable` + `useListSearchParams` (server-pagination, сортировка только по `next_touch_at/stage_entered_at/cycle_no/student_name`) |
| `RenewalDrawer.tsx` | Боковая панель карточки: имя ученика (ссылка на карточку студента), направление+цикл+бейдж стадии, баланс, кнопка «Внести оплату» (открывает существующую модалку оплаты с предзаполненным студентом/направлением), комментарий, лента активности. Закрытие по Esc/клику вне |
| `RenewalStagesSettings.tsx` | CRUD стадий (только superadmin): список с ↑/↓ (reorder) и ✕ (удаление, если не protected), форма создания (название/вид/цвет) |
| `RenewalAnalyticsPage.tsx` | 3 KPI-плитки (renewal rate 30д, продлили, ушли) + барчарт распределения открытых сделок по стадиям (Recharts, один акцентный цвет) |
| `StageBadge.tsx` | Единая точка маппинга `kind → тон бейджа` (progress→info, decision→muted, won→positive, lost→negative) |

### 5.3 Хуки (`hooks/`)

| Хук | Назначение |
|---|---|
| `useRenewalBoard(filters)` | `useQuery`, `keepPreviousData`, доска |
| `useRenewalList(params)` | `useQuery`, `keepPreviousData`, список |
| `fetchRenewalColumnCards(stageId, offset, filters)` | Обычная (не кэшируемая) функция — императивная догрузка колонки |
| `useRenewalDeal(id)` | `useQuery`, детальная сделка (для drawer) |
| `useRenewalActivity(id)` | `useQuery`, лента активности |
| `useRenewalMutations()` | `{move, patch, comment}` — все `useMutation`, инвалидируют `['renewals']` (кроме comment — точечно инвалидирует только свою activity) |
| `useRenewalStages()` / `useRenewalStageMutations()` | Чтение/CRUD/reorder стадий |
| `useRenewalAnalytics()` | Воронка конверсии |

**Важно:** `patch` и `move` из `useRenewalMutations()` реально вызываются только:
`move` — из drag-and-drop на канбане; `patch` — **нигде во фронтенде не используется**
(см. §6 — это дыра в UI, не в API).

---

## 6. Чего нет / известные ограничения

Список того, что либо не реализовано, либо реализовано не полностью — чтобы не искать
самостоятельно и не считать багом то, что является осознанным недоделом.

1. **PATCH сделки не вызывается нигде во фронтенде.** Бэкенд умеет менять
   `assignee_id/next_touch_at/reason_code/expected_amount` (`PATCH /:id`), но в
   `RenewalDrawer` нет ни одного поля/кнопки, которая это дергает. Назначить
   ответственного, поставить дату следующего касания или ожидаемую сумму сейчас
   **нельзя через UI вообще** — только руками через API/БД.
2. **Нет фильтров в UI.** `RenewalsPage` прокидывает `assignee_id/direction_id/overdue`
   из URL query-параметров в хуки, но нет ни одного `SelectInput`/чекбокса, чтобы
   их выставить — только вручную вписать в адресную строку.
3. **Одна воронка.** Схема (`RenewalPipeline`) допускает несколько, но весь код
   везде читает `is_default=True` напрямую — переключения воронок нет и не
   предполагается текущей реализацией.
4. **`analytics?group_by=` принимается, но игнорируется.** Разрез по менеджеру/
   направлению/месяцу из плана не реализован — аналитика всегда общая по всей базе.
5. **`cycle.in_renewal_window()` нигде не используется.** Функция есть, покрыта
   тестом, но никакая логика не проверяет «дошёл ли ученик до окна продления» —
   сделки существуют для ЛЮБОГО активного членства с первого дня, а не только
   когда урок 4 близко/баланс исчерпан.
6. **Напоминания только консольной командой.** `send_renewal_reminders` шлёт
   дайджест по `next_touch_at`, но: (а) её некому вызывать — cron не настроен нигде
   в проекте; (б) поставить `next_touch_at` через UI нельзя (см. п.1) — то есть
   вся фича напоминаний сейчас функционально недоступна конечному пользователю.
7. **`list_deals` не поддерживает `overdue`-фильтр** (есть у board/column_cards).
8. **Reason-код и заморозка не синхронизированы с `students.enrollment_status`.**
   В исходном плане предполагалась двусторонняя связь «стадия Заморожен ↔
   `enrollment_status='frozen'`» — в реализации `move_deal` пишет `reason_code`,
   но никак не трогает таблицу `students`.
9. **Драг «Показать ещё»-карточек в саму колонку** не тестировался явно на предмет
   визуальных коллизий при очень длинных колонках (сотни карточек) — `overflow-y:
   auto` с `max-height: calc(100vh - 220px)` должен справляться, но полноценной
   виртуализации списка нет.
10. **`journal_test` (тестовая БД) не синхронизируется миграциями автоматически** —
    она клонируется через `pg_dump --schema-only` из `journal`, а не через
    `manage.py migrate` (см. `scripts/recreate_test_db.sh`). После любой новой миграции
    `apps.renewals`, меняющей сид-данные (`renewal_pipeline`/`renewal_stage`), их нужно
    докладывать в `journal_test` вручную — иначе тесты в приложениях с no-op
    `django_db_setup` (`lessons`, `teacher_spa` и почти все легаси-приложения) не увидят
    дефолтную воронку. Приложение `apps.renewals` само по себе (без этих легаси-соседей)
    тестируется в отдельной эфемерной БД, которую pytest создаёт и мигрирует с нуля
    каждый прогон — там миграции всегда актуальны.

---

## 7. Настройка стадий (что реально можно делать)

Экран `/admin/renewals/stages`, только superadmin:

- **Создать** стадию: название + вид (`progress/decision/won/lost`) + опционально цвет.
  `key` генерируется автоматически (транслитерация невозможна для кириллицы → общий
  fallback `stage`, `stage_2`, ... — коллизии разруливаются суффиксом, см.
  `repository._unique_stage_key`). `is_auto` всегда `False` для стадий, созданных руками —
  **движок никогда не будет сам переставлять сделки на такую стадию** (см. §3.2 про
  `_progress_stages` — фильтрует именно `is_auto=True`).
- **Переименовать/перекрасить/сменить вид** (`kind`) — `PATCH`, частичное обновление.
- **Переставить порядок** — кнопки ↑/↓, шлют весь новый порядок (`POST /stages/reorder`).
- **Удалить** — нельзя, если: это авто-стадия (`is_auto`), ЛЮБАЯ сделка (открытая или
  закрытая — `RESTRICT`) ссылается на неё, или это единственная стадия своего вида
  среди `won/lost/progress`. Во всех случаях — понятный `409`, не молчаливый провал.

---

## 8. Ночные/ручные команды

```bash
# Самозаживление: гарантирует сделку текущего цикла для каждого активного
# (ученик×направление) + подтягивает авто-стадию «Урок N». Идемпотентно,
# безопасно гонять сколько угодно раз.
python manage.py rebuild_renewal_deals

# Дайджест напоминаний по next_touch_at <= сегодня, по одному письму на менеджера.
# См. ограничение №6 — next_touch_at сейчас некому проставлять через UI.
python manage.py send_renewal_reminders
```

Ни одна из них не привязана к cron/scheduler — запуск полностью ручной.

---

## 9. Журнал изменений (аудит)

`apps/changelog/registry.py`: все 4 модели зарегистрированы (`RenewalActivity` —
`revertable=False`, это уже лог). `apps/changelog/labels.py`: мутирующие URL размечены
человекочитаемыми метками (`renewal.move`, `renewal.update`, `renewal.comment`,
`renewal.stage_create/update/delete/reorder`) — в журнале изменений эти операции
показываются с русской подписью и (где применимо) с возможностью отката из строки.

---

## 10. Файловая карта

```
apps/renewals/
  models.py               # 4 модели, все под pghistory
  migrations/
    0001_initial.py       # таблицы + pghistory-события
    0002_seed_default_pipeline.py   # сид: 1 воронка (тогда — 7 стадий)
    0003_split_lesson_progress_stage.py  # разбивка «Урок 1–4» на 4 стадии
  cycle.py                # cycle_no_from_attended, in_renewal_window (не используется)
  transitions.py          # валидатор переходов по kind
  engine.py                # ensure_deal, close_deal_won, sync_lesson_stage(_safe)
  signals.py               # post_save(Payment) → close_deal_won (on_commit, safe)
  repository.py            # вся SQL-логика: board/list/column_cards/move/patch/stages CRUD
  serializers.py            # Move/Patch/Comment/StageWrite/StageReorder
  services.py               # тонкая обёртка views → repository/engine
  views.py                  # 12 APIView, все с explicit permission_classes
  urls.py                   # смонтирован в /api/admin/renewals (config/urls.py)
  analytics.py               # funnel()
  management/commands/
    rebuild_renewal_deals.py
    send_renewal_reminders.py
  tests/                     # 55 тестов (модели/движок/API/стадии/аналитика/уроки)

apps/lessons/repository.py     # create_lesson_full/update_attendance_cell/delete_lesson_full
                                # → transaction.on_commit(sync_lesson_stage_safe)
apps/teacher_spa/{repository,services}.py   # submit_lesson → тот же хук (главный поток)

frontend/admin-src/src/
  lib/renewals.ts             # все TS-типы
  hooks/{useRenewals,useRenewalStages,useRenewalAnalytics}.ts
  pages/renewals/              # см. §5.2
  styles/pages/renewals.css
```
