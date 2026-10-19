# Раздел «Продления» — план реализации (2 варианта)

> Статус: **черновик на выбор**. Ниже — понимание бизнес-процесса, доменная модель,
> два целостных варианта реализации (бэкенд Django+DRF + admin SPA React), сравнение,
> UX/UI-макеты и рекомендация. Итоговый вариант выбирает владелец продукта.
>
> Автор-роль: системный архитектор + Django-разработчик + тестировщик.
> Дата составления: 2026-07-08.

---

## 0. TL;DR

Нужен CRM-подобный раздел управления **воронкой продлений** учеников: канбан-доска
(как сделки в Bitrix24) + переключение в списочный вид. Доступ — `manager / admin / superadmin`
(в проекте это ровно `IsManagerOrAdmin`). Ученик обучается «месяцами», где **1 месяц = 4 урока = 1 абонемент**,
и каждые 4 урока школа отрабатывает вопрос продления (доплата или подтверждение следующего месяца).

Предлагаю два варианта, отличающихся **глубиной моделирования воронки**:

| | **Вариант 1 — «Производная воронка» (Lean)** | **Вариант 2 — «CRM-пайплайн» (Full)** |
|---|---|---|
| Идея | Карточка = тонкий overlay поверх уже существующих данных (баланс, посещаемость, membership). Стадии-прогресс вычисляются, хранится только ручной статус | Полноценный движок сделок: настраиваемые стадии, история переходов, назначения, напоминания, аналитика конверсии |
| Новых таблиц | 1 (`renewal_card`) | 3–4 (`renewal_pipeline`, `renewal_stage`, `renewal_deal`, `renewal_activity`) |
| Стадии | Полу-фиксированный набор (enum + вычисляемые) | Конфигурируемые в UI (superadmin) |
| Объём работ | ~1.5–2 недели | ~3.5–5 недель |
| Риск | Низкий | Средний |
| Расширяемость | Ограниченная | Высокая (задачи, SLA, аналитика, A/B стадий) |

**Рекомендация (см. §9):** стартовать по **Варианту 1** как MVP, но заложить в схему БД два
поля-«крючка» из Варианта 2 (`pipeline_stage_id` вместо enum и таблица `renewal_activity`),
чтобы апгрейд до полноценного CRM был эволюцией, а не переписыванием.

---

## 1. Бизнес-процесс: как я его понял (+ мои дополнения)

### 1.1 Как есть

- Школа живёт на **продлениях**: ученик оплатил месяц (4 урока), к концу месяца менеджер
  должен «отработать продление» — либо потребовать оплату следующего месяца (если все оплаты
  отработаны), либо просто подтвердить переход на следующий месяц.
- Раньше вели в Bitrix24 как **сделки со стадиями**: минимум 4 стадии (урок 1–4) + промежуточные
  (думает, должен оплатить, заморожен, игнорит, отказывается, …).
- Нужно перенести это на платформу: **канбан** (основной вид) + **список** (переключатель).

### 1.2 Что уже есть в системе и на что опираемся

Раздел **не строится с нуля** — большая часть сигналов уже в БД:

| Сущность | Что даёт для продлений |
|---|---|
| `students.enrollment_status` (`enrolled/not_enrolled/frozen/declined`) + `frozen_until_month` | Готовые терминальные состояния «заморожен / отказ» — синхронизируем с исходом карточки |
| `students.pm` (менеджер) | Естественный владелец карточки (assignee по умолчанию) |
| `memberships.GroupMembership.lessons_done / remaining` (Decimal, half-lesson) | Прогресс по месяцу; `remaining ≤ 0` ⇒ окно продления |
| `finances.balance` = `purchased − attended` по направлению | Финансовый сигнал: `balance ≤ 0` ⇒ «все оплаты отработаны, нужна доплата» |
| `payments.Payment` (immutable, POST/DELETE) | Факт продления-оплатой; появление нового платежа ⇒ авто-переход в «Продлён» |
| `lessons.LessonAttendance` | Триггер авто-продвижения по стадиям «урок 1→4» |
| pghistory + `changelog/registry.py` + `changelog/labels.py` | Журнал изменений и откат — новые модели ОБЯЗАНЫ туда попасть |
| `core/permissions.IsManagerOrAdmin` | Ровно нужная аудитория раздела |

### 1.3 Мои дополнения к процессу (то, чего в Bitrix обычно нет / делалось руками)

1. **Автогенерация карточек на границе цикла.** Карточка «созревает» сама, когда ученик входит
   в окно продления (`remaining ≤ 1` урок ИЛИ идёт 4-й урок месяца), а не заводится менеджером руками.
2. **Авто-переход в «Продлён» по факту оплаты.** Новый `Payment` по направлению карточки закрывает
   текущий цикл как won и **порождает карточку следующего цикла**. Ноль ручной синхронизации с деньгами.
3. **Индикатор «здоровья» ученика** на карточке: посещаемость % за месяц + история своевременных оплат
   → менеджер видит, кто «в зоне риска» ещё до отказа.
4. **Владение и очередь.** У каждой карточки есть ответственный (`assignee`, по умолчанию `student.pm`);
   менеджер видит фильтр «мои продления».
5. **SLA / «протухание».** Карточка, застрявшая в «Ждём оплату» дольше N дней, подсвечивается красным
   (`days_in_stage`), чтобы не терять деньги на «зависших».
6. **Дата следующего касания (`next_touch_at`)** + причины заморозки/отказа (`reason_code`) —
   для аналитики «почему уходят».
7. **Аналитика конверсии продлений** (Вариант 2): renewal rate по менеджеру / направлению / месяцу,
   сумма «в работе» (потенциальная выручка), среднее время до продления.
8. **Оплата прямо из карточки** — переиспользуем существующую модалку «Внести оплату».

---

## 2. Доменная модель продлений (общая для обоих вариантов)

Единые понятия, чтобы варианты говорили на одном языке.

- **Цикл продления (renewal cycle).** Один оплаченный «месяц» = 4 урока по направлению.
  Номер цикла `cycle_no` ученика по направлению = `floor(attended_in_direction / 4) + 1`.
- **Окно продления (renewal window).** Момент, когда карточка требует действия:
  `remaining ≤ 1` ИЛИ `balance ≤ 0` по направлению.
- **Единица работы = карточка (card/deal).** Грань — **(ученик × направление × cycle_no)**.
  Обоснование: балансы и уроки считаются **по направлению**; ученик на двух направлениях имеет
  два независимых цикла продления. Группировка «по ученику» в UI решается свёрткой (см. §8), но
  строка данных — по направлению.
- **Стадия (stage).** Позиция в воронке. Делятся на:
  - *авто-прогресс* (Урок 1 / 2 / 3 / 4) — двигаются событиями посещаемости;
  - *решение* (Думает / Ждём оплату / Заморожен / Игнорит) — двигает менеджер;
  - *терминальные* (Продлён ✅ / Ушёл ❌) — закрывают карточку.
- **Исход (outcome).** `renewed / frozen / declined / churned` — синхронизируется с
  `student.enrollment_status`, где применимо.

### 2.1 Дефолтная воронка (стадии)

```
[Урок 1] → [Урок 2] → [Урок 3] → [Урок 4 · пора продлевать]
                                        │
          ┌───────────────┬────────────┼─────────────┬──────────────┐
          ▼               ▼            ▼              ▼              ▼
     [Ждём оплату]    [Думает]   [Заморожен]   [Игнорит]        (сразу)
          │               │            │              │              │
          └───────────────┴─────┬──────┴──────────────┘              │
                                ▼                                    ▼
                          [Продлён ✅ won]                     [Ушёл ❌ lost]
```

- В Варианте 1 этот набор — полу-фиксированный (enum + вычисляемый прогресс).
- В Варианте 2 — дефолтный сид конфигурируемого пайплайна (стадии редактируются в UI).

---

## 3. Ключевые архитектурные развилки (осознанный выбор)

| Развилка | Вариант 1 (Lean) | Вариант 2 (Full) |
|---|---|---|
| **Грань карточки** | (student × direction × cycle_no) — одинаково в обоих |||
| **Хранение стадии** | enum-поле `status` + вычисляемый прогресс `lesson_stage` из membership | FK `stage_id` → настраиваемая `renewal_stage` |
| **Прогресс урок 1–4** | вычисляется на лету из `lessons_done` | материализуется движком в стадию |
| **История переходов** | pghistory-события `renewal_card` (достаточно для отката/аудита) | отдельная `renewal_activity` (таймлайн, комментарии, причины) |
| **Генерация карточек** | management-command + сигнал на `Payment`/attendance | тот же движок + пересчёт стадий |
| **Конфиг стадий** | нет (меняется миграцией) | CRUD в UI (superadmin) |

Обе ветки соблюдают проектные инварианты: `permission_classes` обязателен, pghistory +
`changelog/registry.py` + `labels.py`, design tokens, form-компоненты, server-pagination,
`placeholderData: keepPreviousData`, `ErrorBoundary key={location.pathname}`.

---

## 4. ВАРИАНТ 1 — «Производная воронка» (Lean, data-derived)

**Философия:** максимум переиспользуем то, что уже посчитано (баланс, `lessons_done`, `enrollment_status`).
Храним только тонкий «ручной» слой: статус-решение, ответственный, дата касания, заметка, исход.
Прогресс «урок 1–4» **не дублируем** — выводим (в духе инварианта «балансы выводятся, не хранятся»).

### 4.1 Схема БД (1 новая таблица)

```sql
-- db/migrations/0NN_renewals.sql  (managed=True поверх существующей схемы)
CREATE TABLE renewal_card (
    id              BIGSERIAL PRIMARY KEY,
    student_id      INTEGER NOT NULL REFERENCES students(id)   ON DELETE RESTRICT,
    direction_id    INTEGER NOT NULL REFERENCES directions(id) ON DELETE RESTRICT,
    cycle_no        INTEGER NOT NULL,                 -- номер месяца/цикла по направлению
    status          TEXT    NOT NULL DEFAULT 'active' -- машинная стадия-решение
        CHECK (status IN ('active','awaiting_payment','thinking','frozen',
                          'ignoring','renewed','churned')),
    assignee_id     INTEGER REFERENCES accounts(id) ON DELETE SET NULL, -- ответственный
    next_touch_at   DATE,                            -- дата следующего касания
    reason_code     TEXT,                            -- причина заморозки/отказа (аналитика)
    note            TEXT,
    stage_entered_at TIMESTAMPTZ NOT NULL DEFAULT now(), -- для days_in_stage / SLA
    outcome_at      TIMESTAMPTZ,                     -- когда закрыта (won/lost)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, direction_id, cycle_no)      -- идемпотентность автогенерации
);
CREATE INDEX renewal_card_status_idx    ON renewal_card(status) WHERE outcome_at IS NULL;
CREATE INDEX renewal_card_assignee_idx  ON renewal_card(assignee_id);
CREATE INDEX renewal_card_student_idx   ON renewal_card(student_id);
```

**Вычисляемые (не хранимые) поля** отдаются API-слоем на каждый запрос:
`lesson_stage` (1–4 из `lessons_done % 4`), `remaining`, `balance`, `attendance_pct`,
`days_in_stage`, `is_overdue`, `student_name`, `direction_name/color`.

### 4.2 Django-приложение `apps/renewals/`

Структура — как у всех доменных приложений (`models / repository / serializers / services / views / urls / tests`):

```
apps/renewals/
  models.py        # RenewalCard (managed=True, @pghistory.track(...))
  repository.py    # SQL/ORM: board(), list(), get(), move(), assign(), ensure_cards()
  services.py      # тонкий слой + бизнес-правила переходов (валидатор allowed transitions)
  serializers.py   # RenewalCardSerializer, MoveSerializer, PatchSerializer
  views.py         # APIView, permission_classes = [IsManagerOrAdmin]
  urls.py
  signals.py       # on Payment create → close+respawn; on attendance → touch stage_entered_at
  management/commands/rebuild_renewal_cards.py
  tests/
```

**`RenewalCard` (models.py)** — обязательно под pghistory:

```python
@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class RenewalCard(models.Model):
    ...
    class Meta:
        managed = True
        db_table = 'renewal_card'
```

**Правила переходов (services.py).** Явный whitelist `ALLOWED_TRANSITIONS: dict[str, set[str]]`,
чтобы канбан не пускал карточку в невалидную стадию. Пример: из `renewed/churned` (терминальные)
переходов нет; из любой открытой можно в терминальную.

**Автогенерация / синхронизация исхода (signals.py, идемпотентно):**
- `post_save(Payment)` по `(student, direction)` → найти открытую карточку этого направления,
  выставить `status='renewed', outcome_at=now()`, затем `ensure_next_cycle_card()`.
- `post_save(LessonAttendance)` → обновить `stage_entered_at`/пересчитать окно (для `is_overdue`),
  без записи стадии (прогресс вычисляемый).
- `frozen`/`declined` в `student.enrollment_status` ↔ `status='frozen'/'churned'` (двусторонняя мягкая связь).
- Ночной `rebuild_renewal_cards` — самозаживление на случай пропущенного сигнала (как safety net).

> Замечание про производительность (VPS 2CPU/2GB): сигналы делают точечные UPDATE по индексу,
> не «читают всё». Ночная команда идёт батчами. Совпадает с проектным правилом «не читать всё».

### 4.3 API (все — `IsManagerOrAdmin`)

| Метод | URL | Назначение | Операция для `labels.py` |
|---|---|---|---|
| GET | `/api/admin/renewals?view=board` | доска, сгруппировано по стадиям (+ лимит на колонку) | — |
| GET | `/api/admin/renewals?view=list&page=…` | список, server-pagination | — |
| GET | `/api/admin/renewals/:id` | карточка + вычисляемые поля | — |
| POST | `/api/admin/renewals/:id/move` | сменить стадию `{to_status, reason_code?}` | `renewal.move` |
| PATCH | `/api/admin/renewals/:id` | assignee / next_touch_at / note | `renewal.update` |
| POST | `/api/admin/renewals/rebuild` (super) | ручной пересчёт | `renewal.rebuild` |

Ответ `board` — компактный (для канбана без DnD-лагов):
```jsonc
{
  "columns": [
    { "status": "active", "label": "Урок 1–4", "count": 42, "sum_potential": 168000,
      "cards": [ /* первые N; остальное — «Показать ещё» */ ] },
    ...
  ]
}
```

### 4.4 Журнал изменений / аудит

- `renewals.RenewalCard` → строка в `changelog/registry.py`:
  `'renewals.RenewalCard': TrackedModel('renewal_card', True, 35)` (после membership, до lessons).
  Иначе упадёт `test_registry_covers_all_tracked_models`.
- Новые мутирующие URL → правила в `changelog/labels.py` (`renewal.move`, `renewal.update`, `renewal.rebuild`).
- Русские подписи операций — в `frontend/.../lib/labels.ts`.

### 4.5 Фронт (admin SPA)

- Nav: пункт **«Продления»** в `Sidebar.SECTIONS` (иконка «повтор/цикл»), между «Абонементы» и «Зарплата».
  Виден всем staff — отдельный `canSeeRenewals = isStaff` в `lib/permissions.ts`.
- Роут `/admin/renewals` → без `RequireRole`-обёртки супер-роли (доступ у staff уже на API);
  для чистоты добавить `<RequireRole roles={['manager','admin','superadmin']}>`.
- Хук `hooks/useRenewals.ts`: `useRenewalBoard(filters)`, `useRenewalList(params)` (`keepPreviousData`),
  `useRenewalMutations()` (move/patch с **оптимистичным** обновлением доски и rollback при ошибке).
- Страницы `pages/renewals/`: `RenewalsPage.tsx` (тумблер Канбан/Список + фильтры),
  `RenewalBoard.tsx`, `RenewalList.tsx`, `RenewalCard.tsx`, `RenewalDrawer.tsx`.
- DnD: `@dnd-kit/core` (React 19-совместим; visx/некоторые DnD — нет). Только он — новая зависимость.
- Всё через design tokens, `SelectInput/DateInput/Combobox`, `EntityLink` на ученика, enum-подписи из `lib/labels.ts`.

### 4.6 Фазы (Вариант 1)

| Фаза | Содержание | Тесты |
|---|---|---|
| 1. Схема | миграция `renewal_card`, модель + pghistory, registry + labels | `test_registry_covers_all_tracked_models`, миграции применяются |
| 2. Автогенерация | `rebuild_renewal_cards` + сигналы Payment/attendance, идемпотентность | pytest: цикл создаётся/закрывается/респавнится |
| 3. API | board/list/get/move/patch, whitelist переходов, RBAC | pytest: 403 без роли, 200 staff, невалидный переход → 400 |
| 4. Фронт-список | список + фильтры + пагинация (проще канбана — валидируем данные) | смоук |
| 5. Канбан | доска, DnD, оптимистика, drawer, «Внести оплату» из карточки | e2e-смоук по гайду |
| 6. Полировка | SLA-подсветка, «мои продления», аналитика-лайт (счётчики колонок) | — |

**Оценка:** ~1.5–2 недели. **Риск:** низкий.

---

## 5. ВАРИАНТ 2 — «CRM-пайплайн» (Full, configurable deals engine)

**Философия:** воспроизводим Bitrix «как есть» — настраиваемые стадии, история переходов,
таймлайн активности, назначения, напоминания, аналитика конверсии. Карточка становится
полноценной «сделкой».

### 5.1 Схема БД (3–4 таблицы)

```sql
CREATE TABLE renewal_pipeline (          -- обычно одна воронка, но заложено на будущее
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    is_default BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE renewal_stage (             -- КОНФИГУРИРУЕМЫЕ стадии
    id BIGSERIAL PRIMARY KEY,
    pipeline_id BIGINT NOT NULL REFERENCES renewal_pipeline(id) ON DELETE CASCADE,
    key TEXT NOT NULL,                   -- стабильный машинный ключ (для авто-правил)
    label TEXT NOT NULL,
    color TEXT,
    sort_order INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('progress','decision','won','lost')),
    is_auto BOOLEAN NOT NULL DEFAULT false, -- двигается движком (урок 1–4) vs руками
    UNIQUE (pipeline_id, key)
);

CREATE TABLE renewal_deal (
    id BIGSERIAL PRIMARY KEY,
    student_id   INTEGER NOT NULL REFERENCES students(id)   ON DELETE RESTRICT,
    direction_id INTEGER NOT NULL REFERENCES directions(id) ON DELETE RESTRICT,
    cycle_no     INTEGER NOT NULL,
    pipeline_id  BIGINT  NOT NULL REFERENCES renewal_pipeline(id),
    stage_id     BIGINT  NOT NULL REFERENCES renewal_stage(id),
    assignee_id  INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    expected_amount NUMERIC(10,2),       -- ожидаемая сумма продления (для «в работе»)
    next_touch_at DATE,
    reason_code  TEXT,
    stage_entered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    outcome_at   TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (student_id, direction_id, cycle_no)
);

CREATE TABLE renewal_activity (          -- таймлайн: переходы, комментарии, звонки
    id BIGSERIAL PRIMARY KEY,
    deal_id  BIGINT NOT NULL REFERENCES renewal_deal(id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('stage_change','comment','payment_linked','system')),
    from_stage_id BIGINT REFERENCES renewal_stage(id),
    to_stage_id   BIGINT REFERENCES renewal_stage(id),
    payment_id    INTEGER REFERENCES payments(id) ON DELETE SET NULL,
    body TEXT,
    author_id INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX renewal_deal_stage_idx ON renewal_deal(stage_id) WHERE outcome_at IS NULL;
CREATE INDEX renewal_activity_deal_idx ON renewal_activity(deal_id, created_at DESC);
```

### 5.2 Бэкенд

Приложение `apps/renewals/` с теми же слоями + `engine.py` (движок пересчёта стадий), `analytics.py`.

- **Движок** (`engine.py`): пересчёт `stage_id` для авто-стадий по событиям посещаемости/оплат;
  порождение сделки следующего цикла; запись `renewal_activity`.
- **Настраиваемые стадии**: CRUD, права `ReadStaffWriteSuperAdmin` (читают staff, меняют super).
- **Напоминания**: `next_touch_at` + ночная команда, шлющая менеджеру дайджест «касания на сегодня»
  (email через существующий SMTP; опц. — уведомление в SPA).
- **Аналитика** (`analytics.py`): renewal rate, конверсия по стадиям (воронка), сумма «в работе»,
  среднее время до продления — срезы по менеджеру/направлению/месяцу.

### 5.3 API (доп. к Варианту 1)

| Метод | URL | Права | labels |
|---|---|---|---|
| GET/POST/PATCH/DELETE | `/api/admin/renewals/stages` | read staff / write super | `renewal.stage_*` |
| POST | `/api/admin/renewals/:id/comment` | staff | `renewal.comment` |
| GET | `/api/admin/renewals/:id/activity` | staff | — |
| GET | `/api/admin/renewals/analytics?group_by=…` | staff | — |

### 5.4 Журнал изменений

Под pghistory и в `registry.py`: `renewal_deal` (revertable), `renewal_stage`, `renewal_pipeline`;
`renewal_activity` — как правило **не** revertable (это уже сам лог). labels — на все мутирующие URL.

### 5.5 Фронт (доп. к Варианту 1)

- Канбан читает стадии из конфигурации (`useRenewalStages()`), а не из хардкода.
- Экран **настройки воронки** (superadmin): переименование/цвет/порядок/добавление стадий (DnD-сортировка).
- **Drawer сделки** с таймлайном активности, комментариями, связанными оплатами, кнопкой напоминания.
- Экран **аналитики продлений**: воронка конверсии (Recharts 3 — стек графиков проекта), KPI-плитки.

### 5.6 Фазы (Вариант 2)

1. Схема (4 таблицы) + сид дефолтной воронки + pghistory/registry/labels.
2. Движок + сигналы + activity-лог + тесты идемпотентности/конкурентности.
3. API сделок + стадий + комментариев + RBAC-матрица.
4. Фронт: список → канбан (стадии из конфига) → drawer с таймлайном.
5. Настройка воронки (super) + напоминания (SMTP-дайджест).
6. Аналитика конверсии (Recharts) + полировка/SLA.

**Оценка:** ~3.5–5 недель. **Риск:** средний (движок стадий + конкурентные переходы + больше поверхности под баги).

---

## 6. Сравнение вариантов

| Критерий | Вариант 1 (Lean) | Вариант 2 (Full) |
|---|---|---|
| Совпадение с ментальной моделью Bitrix | Хорошее | Полное |
| Настраиваемые стадии | ✗ (миграция) | ✓ (UI) |
| История переходов / таймлайн | Базовая (pghistory) | Богатая (`renewal_activity`) |
| Напоминания / касания | Поле `next_touch_at` | + дайджесты/уведомления |
| Аналитика конверсии | Счётчики колонок | Полноценная воронка + KPI |
| Новых таблиц | 1 | 3–4 |
| Новых зависимостей фронта | `@dnd-kit` | `@dnd-kit` |
| Объём | 1.5–2 нед | 3.5–5 нед |
| Риск | Низкий | Средний |
| Нагрузка на VPS 2CPU/2GB | Минимальная | Умеренная (следить за аналитикой/движком) |
| Апгрейд-путь | → превращается в Вариант 2 | финальная точка |

---

## 7. Единые инварианты и требования (для любого варианта)

- **RBAC:** каждая вьюха задаёт `permission_classes`. Раздел — `IsManagerOrAdmin`; конфиг стадий (В2) — `ReadStaffWriteSuperAdmin`.
- **pghistory + changelog:** новые доменные модели → `@pghistory.track(...)` + `registry.py` + `labels.py` + миграция.
- **Immutable-финансы не трогаем:** карточка только *отражает* платежи, сам `Payment` — POST/DELETE как есть.
- **Half-lesson:** прогресс считаем из `lessons_done` (Decimal), 45 мин = 0.5 — не изобретаем свой счётчик.
- **Балансы выводятся, не хранятся:** прогресс/баланс на карточке — вычисляемые.
- **Пагинация** встроенная DRF; список — server-side, `placeholderData: keepPreviousData`.
- **Sort-dir** — правило `(val==='asc'||val==='desc') ? val : default` в обоих местах.
- **Фронт:** design tokens (без хардкода цветов), form-компоненты, enum-подписи из `lib/labels.ts`,
  `ErrorBoundary key={location.pathname}`, `.data-table--loading` гасит `pointer-events` только на `tbody`.
- **Идемпотентность** автогенерации карточек: `UNIQUE(student, direction, cycle_no)`.
- **Производительность:** точечные UPDATE по индексам, батчи в ночных командах, лимит карточек на колонку + «Показать ещё».

---

## 8. UX/UI (общий раздел)

### 8.1 Канбан (основной вид)

```
Продления                                   [ Канбан | Список ]   Фильтры: [Менеджер ▾][Направление ▾][☐ Только просроченные]  🔍 Ученик…
┌──────────────┬──────────────┬──────────────┬──────────────┬──────────────┬──────────────┐
│ Урок 1–4     │ Ждём оплату  │ Думает       │ Заморожен    │ Продлён ✅   │ Ушёл ❌      │
│ 42 · 168 000₽│ 11 · 44 000₽ │ 6            │ 4            │ 27           │ 3            │
├──────────────┼──────────────┼──────────────┼──────────────┼──────────────┼──────────────┤
│ ┌──────────┐ │ ┌──────────┐ │              │              │              │              │
│ │(АИ) Аня И.│ │ │(СП) Саша │ │              │              │              │              │
│ │ Python·М3│ │ │ Скретч·М2│ │              │              │              │              │
│ │ ▓▓▓░ 3/4 │ │ │ ⚠ 5 дн   │ │              │              │              │              │
│ │ Баланс 0 │ │ │ Долг 4000│ │              │              │              │              │
│ │ 👤 Ирина │ │ │ 👤 Ирина │ │              │              │              │              │
│ │ [₽][❄][…]│ │ │ [₽][❄][…]│ │              │              │              │              │
│ └──────────┘ │ └──────────┘ │              │              │              │              │
│  … Показать  │              │              │              │              │              │
└──────────────┴──────────────┴──────────────┴──────────────┴──────────────┴──────────────┘
```

Карточка: аватар+имя ученика (EntityLink), чип направления (цвет из `direction.color`),
прогресс-бар `n/4` урока, финсостояние (баланс/долг), бейдж SLA (⚠ дней в стадии),
ответственный, быстрые действия: ₽ «Внести оплату» (модалка), ❄ «Заморозить», … меню.
Заголовок колонки: кол-во + сумма потенциала. DnD между колонками с оптимистикой и rollback.

### 8.2 Список (переключатель)

Стандартная `data-table` проекта: колонки Ученик · Направление · Цикл · Стадия (`StatusBadge`) ·
Прогресс · Баланс/долг · Ответственный · Дней в стадии · След. касание · действия.
Сортировка/фильтры/пагинация — как в остальных списках (server-side, `keepPreviousData`).
Полезно для массовой обработки и экспорта.

### 8.3 Drawer карточки

Правая панель: шапка (ученик/направление/цикл/стадия), финблок (баланс, история оплат
направления, кнопка «Внести оплату»), посещаемость месяца, поля (ответственный, дата касания,
причина), заметка. В Варианте 2 — таймлайн активности + комментарии.

### 8.4 Свёртка «по ученику»

Т.к. строка данных — по направлению, в UI добавить тумблер «группировать по ученику»: карточки
одного ученика по разным направлениям складываются в мини-стек — менеджер видит человека целиком,
но действия остаются пер-направление.

---

## 9. Рекомендация

**Идти по Варианту 1 (Lean) как MVP, с двумя «крючками» под Вариант 2:**

1. Вместо enum `status` сразу завести `stage_key TEXT` со стабильными ключами (equivalent enum-значениям).
   Тогда переход к настраиваемой `renewal_stage` — это добавление таблицы и FK, без переписывания карточек.
2. С первого дня писать переходы стадий в лёгкий журнал (даже если это просто pghistory-события) —
   апгрейд до полноценного `renewal_activity` не потеряет историю.

Причины: Вариант 1 закрывает 90% ценности (канбан + список + автогенерация + оплата из карточки +
владение + SLA) за ~в 2–2.5 раза меньший объём и низкий риск на VPS 2CPU/2GB. Настраиваемые стадии,
аналитика конверсии и напоминания-дайджесты (Вариант 2) — это ценно, но это **вторая итерация**,
которую бизнес закажет уже осознанно, поработав на MVP и увидев, каких стадий реально не хватает.

**Что даст быстрый максимум ценности в MVP:** авто-переход в «Продлён» по факту оплаты и
авто-респавн следующего цикла — именно это убирает ручную рутину, ради которой всё затевается.

---

## 10. Решения по умолчанию (принятые самостоятельно, можно оспорить)

- Грань карточки — **по направлению**, не по ученику (свёртка в UI). Причина: балансы/уроки — по направлению.
- Аудитория — `IsManagerOrAdmin` (manager/admin/superadmin), настройка воронки (В2) — только superadmin.
- DnD-библиотека — `@dnd-kit` (React 19-совместима, в отличие от части экосистемы visx/DnD).
- Терминальные стадии закрывают карточку (`outcome_at`) и исключаются из активной доски (архив/фильтр).
- Заморозка карточки синхронизируется со `student.enrollment_status='frozen'` + `frozen_until_month` мягкой связью.
- Автогенерация карточек — идемпотентна по `UNIQUE(student, direction, cycle_no)` + ночная команда самозаживления.
```
