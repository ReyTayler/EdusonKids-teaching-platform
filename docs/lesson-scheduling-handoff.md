# Хендофф: материализованные плановые уроки + операции переноса

> Документ передачи между сессиями. Описывает **всё**, что сделано по фиче
> «Планирование занятий» (материализованные `planned_lessons`), как оно устроено,
> текущее состояние, и **утверждённый, но ещё не начатый** план продолжения
> (рефакторинг операций переноса). Спека модели — `docs/lesson-scheduling.md`.

**Ветка:** `feature/lesson-scheduling-materialized` (18 коммитов поверх `main`, дерево чистое, НЕ смёржено, НЕ запушено).
**Стек:** Django 5.2 + DRF (`journal_django/`), admin SPA (React 19 + TanStack Query v5 + React Router v7), teacher SPA (React, общий код через `@shared`). PostgreSQL. Node — только компилятор фронта.

---

## 1. Что это за фича

Раньше расписание было **вычисляемым** (compute-on-read): occurrences считались на лету из `group_schedule_slots` + `lesson_schedule_exceptions`. Перешли на **материализованную** модель: таблица `planned_lessons` — источник правды по датам/статусам/преподавателю каждого планового занятия. Слоты остались recurrence-шаблоном для генерации; исключения удалены.

Три операции над расписанием: **разовый перенос**, **перенос навсегда** (сдвиг хвоста на новый день недели + версионирование слота), **отмена со сдвигом** (+1 неделя, курс продлевается, урок не списывается). Плюс **доп. занятие** и **генерация плана**.

Фронт: страница группы переведена во вкладки; teacher-календарь вынесен в общий `@shared` и переиспользуется в admin; операции — кнопки + модалки.

---

## 2. Архитектура (слои и файлы)

### Backend — `journal_django/apps/scheduling/`
- **`models.py`** — `PlannedLesson` (`managed=True`, таблица `planned_lessons`). Поля: `id`, `group` (FK CASCADE), `seq` (null для extra/маркеров), `lesson_number` Decimal(5,1), `scheduled_date` (**плановая** дата), `scheduled_time`, `teacher` (FK, препод КОНКРЕТНОГО занятия), `status` (`pending/overdue/done/cancelled/moved`), `fact_lesson` (FK→`lessons.Lesson`, unique, SET_NULL), `moved_from_date`, `moved_to_date`, `note`, `created_at/updated_at`. Constraints: `UniqueConstraint(group, seq)` где seq NOT NULL; CheckConstraint статуса; CheckConstraint «seq и lesson_number вместе NULL или вместе заданы». Index `(group, scheduled_date)`.
- **`migrations/0001_initial.py`** — создание таблицы + RunSQL DB-default `now()` на created_at/updated_at.
- **`occurrences.py`** — ЧИСТЫЙ генератор недель: `_walk`, `_step_for` (half-lesson: 45мин→0.5), `_offset_from_monday` (конвенция **Вс=0**), `Slot`, `Occurrence`, статус-константы. Compute-on-read из него **удалён** (шаг 9); остался только генератор для planner.
- **`planner.py`** — ЧИСТЫЕ функции без ORM (быстрый юнит-тест без БД): `generate`, `reschedule`, `permanent_change`, `cancel`, `extra`, `_shift_to_weekday`, `_far_future`. Работают над dataclass `PlannedRow`.
- **`repository.py`** — ЕДИНСТВЕННОЕ место ORM. Чтение: `active_groups`, `slots_by_group`, `planned_lessons_in_window` (для календаря, скоуп по teacher_id), `groups_without_plan`, `student_names_by_group`, `teacher_names`, `get_plan`, `get_plan_lesson`. Запись: `persist_plan` (идемпотентно), `link_facts`, `reset_plan`, `reschedule_lesson`, `permanent_change`, `cancel_lesson`, `add_extra`. Сериализация строки — `_plan_row_dict` / `_plan_row_dict_obj`.
- **`services.py`** — оркестрация + аудит: `build_calendar` (читает planned_lessons), `get_plan`, `generate_plan`, `reschedule`, `permanent_change`, `cancel`, `add_extra`. Каждая мутация пишет `log_event` (`plan_reschedule/permanent_change/cancel/extra/generate`) без PII.
- **`serializers.py`** — `StrictSerializer` (отклоняет неизвестные поля) + `PlanRescheduleSerializer`, `PlanPermanentChangeSerializer`, `PlanExtraSerializer`.
- **`views.py`** — `CalendarView` (`IsTeacher`) + 6 admin-вьюх плана (`IsManagerOrAdmin`): `GroupPlanView/Generate/Reschedule/PermanentChange/Cancel/Extra`. Ошибки: перенос done → 409; мульти-слот/бизнес → 400; нет группы/строки → 404.
- **`management/commands/backfill_planned_lessons.py`** — бэкфилл + линковка + флаг `--dry-run`, `--reset`.
- **URL:** admin-план смонтирован в `apps/groups/urls.py` под `/api/admin/groups/<pk>/plan*` (ДО teacher-guard); календарь — `apps/scheduling/urls.py` `/api/calendar`.

### Frontend — `journal_django/frontend/`
- **`admin-src/src/shared/calendar/`** — общий презентационный календарь (перенесён из teacher-src): `CalendarView.tsx` (view week/month/list, KPI, легенда, навигация), `WeekGrid/MonthGrid/DayList/LessonPopup.tsx`, `StatusPill.tsx`, `Modal.tsx`, `lib.ts`, `types.ts`, `calendar.css`, `modal.css`. teacher импортирует через `@shared/shared/calendar/*`, admin — через `@/shared/calendar/*`. `@shared` alias → `../admin-src/src` (см. `teacher-src/vite.config.ts`, dedupe react/query).
- **`admin-src/src/components/ui/`** — `Tabs.tsx`, `Button.tsx` (новые), `Dialog.tsx`.
- **`admin-src/src/components/form/`** — `TimeInput.tsx` (новый, без native `<input type=time>`), `Combobox`, `DateInput`, `SelectInput`.
- **`admin-src/src/hooks/useGroupPlanCalendar.ts`** — `useGroupPlan(groupId)` (GET /plan → `PlanRow[]`) + `useGroupPlanCalendar` (маппинг в `Occurrence` для CalendarView). Ключ query — `groupPlanKey = ['group-plan', id]`.
- **`admin-src/src/hooks/useGroupPlan.ts`** — мутации: `useGeneratePlan/useReschedule/usePermanentChange/useCancelLesson/useAddExtra`. `api()` сам ставит `X-CSRFToken`. onSuccess инвалидирует `['group-plan',id]` + `['groups']`.
- **`admin-src/src/pages/groups/`** — `GroupDetailPage.tsx` (вкладки Обзор/Ученики/Уроки/Расписание, активная в URL `?tab=`), `GroupPlanActions.tsx` (toolbar + модалки операций + `quickAction` для LessonPopup), `GroupPlanTable.tsx` (таблица всех уроков во вкладке «Обзор»), `GroupScheduleBlock.tsx` (read-only слоты).

---

## 3. Как работают ключевые механизмы

### Календарь (teacher + admin)
- **teacher** `GET /api/calendar?from&to` → `services.build_calendar` читает `planned_lessons` в окне, **скоуп по `planned_lesson.teacher_id`** (препод занятия, не группы). Статус вычисляется на чтении: `done` если status=done или есть fact; иначе `overdue`/`pending` по МСК-времени. Ответ `{occurrences, unscheduled, window}`.
- **admin** (вкладка «Расписание») — `useGroupPlanCalendar` грузит **весь** план группы (GET /plan) и маппит в Occurrence. `CalendarView` **фильтрует по видимому окну** `[windowFrom, windowTo]` на клиенте (иначе занятия всех недель наваливались бы на текущую — сетки раскладывают по дню недели через `columnIndexOfIsoDate`). Для teacher фильтр — no-op.
- **«Перекидывание» урока:** смена `teacher_id` строки → занятие исчезает из календаря старого препода и появляется у нового (скоуп по teacher_id). Покрыто тестами (`test_teacher_reassignment.py`).

### Генерация и бэкфилл
- `planner.generate(start, slots, total_lessons, duration, default_teacher)` → N курсовых строк seq 1..N. `_far_future` теперь учитывает `step` (иначе полуурочные 45-мин курсы обрезались вдвое).
- `backfill_planned_lessons` — для активных групп со стартом и `direction.total_lessons`: `persist_plan` (идемпотентно) + `link_facts`. `--reset` удаляет план группы и генерирует начисто (нужен для боевого cutover; **разрушителен** — сбрасывает ручные операции).

### Линковка план ↔ факт (ВАЖНОЕ решение по датам)
`link_facts` связывает плановую строку с проведённым уроком (`lessons.Lesson`) и ставит `status='done'`:
1. приоритетно **по `lesson_number`** (позиция урока в курсе — надёжнее даты; уроки прошлого часто на сдвинутых датах),
2. fallback по точной дате.

**`scheduled_date` НЕ перезаписывается** фактической датой — плановая дата хранится отдельно, фактическая берётся из `fact_lesson.lesson_date`. `get_plan` отдаёт оба: `scheduled_date` (плановая) и `fact_date` (фактическая) + `record_url`. Так во вкладке «Обзор» видны обе даты (на dev у ~2571/3426 проведённых они различаются — праздники/переносы).
> Следствие: в календарных сетках проведённые уроки показываются на **плановой** дате, не фактической. Если нужно иначе — правка маппинга в `useGroupPlanCalendar`/`build_calendar`.

### Операции (текущее поведение)
- **reschedule** (`/plan/<lid>/reschedule`): обновляет ОДНУ строку in-place — `scheduled_date`, опц. `scheduled_time`, опц. `teacher_id`; ставит `moved_from_date = старая дата`. Блокирует только `done`.
- **permanent_change** (`/plan/permanent-change`): гард мульти-слотовых групп (>1 открытый слот → 400); пересчитывает хвост seq>=from_seq (pending/overdue) на новый день недели (`_shift_to_weekday`); версионирует слот через `groups.repository.apply_schedule_change` (закрыть открытый / открыть новый); `effective_from` выводится на сервере из новой даты первой сдвинутой строки.
- **cancel** (`/plan/<lid>/cancel`): сдвигает все строки `scheduled_date>=from_date, status!=done` на +7 дней; вставляет НЕ-курсовой маркер `status='cancelled'` (seq=NULL) на исходную дату. Абонемент не трогается.
- **extra** (`/plan/extra`): вставляет строку seq=NULL, is_extra.

### Метки статусов в календаре
- `cancelled` — маркер зачёркивается целиком (`.cancelled` в `calendar.css`).
- `moved` — компактный значок `↪` на занятии по наличию `moved_from_date` (статус строки не меняется — сохраняется overdue-сигнал). В LessonPopup строка «Перенесён с …».
- Токены: `--overlay`, `--shadow-xs` в `tokens.css` (убран hardcoded rgba).

---

## 4. Ключевые инварианты и решения (НЕ сломать)
- **RBAC:** DRF default AllowAny — каждая вьюха задаёт `permission_classes`. Admin-план → `IsManagerOrAdmin`; календарь → `IsTeacher` со скоупом по `request.user.teacher_id` (нельзя чужой календарь).
- **CSRF:** мутации через DRF SessionAuth/CookieJWT, `api()` шлёт `X-CSRFToken`; `@csrf_exempt` не ставить.
- **Аудит:** `log_event` на каждой мутации, без PII/секретов в meta.
- **`done` неприкосновенен:** ни одна операция не трогает проведённые строки.
- **day_of_week Вс=0** (Пн=1..Сб=6, Вс=0). В UI Monday-first, но `value` уже в Вс=0 (конвертации индекса нет).
- **half-lesson:** `lesson_number = seq * step`, 45мин → step 0.5.
- **Даты чистые** (без TZ), «сейчас» по МСК (`apps.core.utils.dates.msk_now`).
- **Батч без N+1** (VPS 2CPU/2ГБ); `keepPreviousData` в плановых хуках; `ErrorBoundary key=pathname` (не терять фокус в модалках).
- **Дизайн-токены** только из `admin-src/src/styles/tokens.css`; native form-элементы запрещены (только `SelectInput/DateInput/Combobox/Checkbox/TimeInput`).

---

## 5. Тесты и верификация
- **pytest** (settings `config.settings.test`, БД `journal_test` с fail-fast guard): полный прогон — **665 passed, 79 skipped**. Раздел scheduling — 72 теста: `test_planner`, `test_calendar_api`, `test_plan_api`, `test_backfill_planned_lessons`, `test_teacher_reassignment` (файл `test_occurrences` удалён на шаге 9 вместе с compute-on-read).
  - Запуск: `cd journal_django && .venv/Scripts/python.exe -m pytest apps/scheduling -q`
  - Гонять ДЕФОЛТНЫМ pytest (guard против боевой БД). Схему test-БД пересоздаёт `scripts/recreate_test_db.sh`.
- **Фронт:** `cd frontend/admin-src && npm run build` и `cd frontend/teacher-src && npm run build` — обе зелёные; `npx tsc --noEmit` — чисто. dist пересобран и закоммичен (`admin-dist/`, `teacher-dist/`).
- **Дев-БД `journal`** репарирована `backfill_planned_lessons --reset`: 6780 строк, 3426 слинковано с фактами (незалинкованных фактов 62), полуурочные планы полной длины.
- **Запуск приложения:** `cd journal_django && .venv/Scripts/python.exe manage.py runserver` → http://localhost:8000 (см. `docs/how-to-run.md`). venv в `journal_django/.venv`.

---

## 6. Баги, найденные и ИСПРАВЛЕННЫЕ в этой сессии
1. **Плохая синхронизация прошлых уроков** — `link_facts` матчил по дате (77% фактов), 794 факта не линковались, 1835 строк «overdue навечно». → матчинг по `lesson_number` (98%).
2. **Обрезка полуурочных курсов** — `_far_future` считал 1 урок/неделю. → учёт `step`.
3. **Наваливание занятий в календаре** — admin грузит весь план, сетки раскладывают по дню недели → все недели в одну. → фильтр по видимому окну в `CalendarView`.
4. **Метки cancelled/moved** не проставлялись (мёртвый код) → cancel вставляет маркер, moved-значок по `moved_from_date`.
5. **Мульти-слот permanent-change** схлопывал расписание → гард (400).
6. **effective_from от клиента** мог рассинхронить слот и хвост → выводится на сервере.
7. Токенизация hardcoded rgba; номер урока и обе даты (план/факт) в UI.

---

## 7. ⚠️ ОТКРЫТЫЕ баги/костыли механизма переноса (глубокий аудит, ещё НЕ исправлены)

Пользователь запросил аудит; найдено (по убыванию важности):

**🔴 Баги (видны пользователю):**
1. **Смена преподавателя помечает урок «перенесён».** `planner.reschedule` (`planner.py:100-106`) ВСЕГДА ставит `moved_from_date = старая дата`, без сравнения с новой. «Сменить преподавателя» (`GroupPlanActions.tsx:228-230`) = reschedule с той же датой → урок получает значок «↪ Перенесён», хотя дата не менялась.
2. **«Изменить расписание» по умолчанию — Понедельник.** `openPermanent` (`GroupPlanActions.tsx:148-154`) всегда ставит `pDow='1'` независимо от дня группы; поле обязательное → смена только препода/времени навсегда молча переносит все занятия на понедельник.
3. **Повторная отмена сдвигает прежний маркер «Отменён».** `cancel_lesson` (`repository.py:641-651`) двигает +7 всё `status != 'done'` — включая маркеры `cancelled`/`moved`. Маркеры должны быть неподвижными пинами.

**🟡 Костыли:**
4. Отмена сдвигает и доп. занятия (extra) — тот же фильтр.
5. «Сменить преподавателя» — не отдельная операция, а reschedule (корень бага 1).
6. В списке «Перенести занятие» видны маркеры отмены (`reschedulableRows = status !== 'done'`).

**🟢 Мелочи:**
7. reschedule/permanent не проверяют коллизии дат (два занятия на одну дату/время).
8. permanent_change над вручную перенесёнными строками берёт их текущую дату для `_shift_to_weekday` → даты вне порядка.
9. При `new_time=None` слот получает время `open_slots[0]`, строки — свои (рассинхрон при индивидуальном времени).

---

## 8. УТВЕРЖДЁННЫЙ план продолжения (архитектура операций — начать отсюда)

Пользователь выбрал: **«Сначала архитектура»** + **«смена преподавателя — разовая И навсегда»**.

**Задача:** развести дату-двигающие операции и смену преподавателя/времени в чистую модель, устранив побочные `moved_from` и дефолт-понедельник.

Ориентир на целевую модель операций (спроектировать точно в начале сессии, согласовать с пользователем):
- **Перенос (дата) разовый** — двигает дату одной строки, ставит `moved_from_date` ТОЛЬКО если дата реально изменилась.
- **Перенос навсегда (день/время слота)** — как сейчас, но день недели предзаполняется текущим днём группы, а не понедельником; смена только времени/препода не должна двигать день.
- **Смена преподавателя разовая** — отдельная операция: меняет только `teacher_id` одной строки, НЕ трогает дату, НЕ ставит `moved_from`.
- **Смена преподавателя навсегда** — меняет `teacher_id` хвоста (seq>=k) pending/overdue + дефолт будущей генерации; НЕ трогает даты/слот-день.
- Заодно починить: неподвижные маркеры (cancel исключает cancelled/moved из сдвига; решить судьбу extra), фильтры списков в модалках (не показывать маркеры), опц. guard коллизий.

**Как вести (правила проекта, память):**
- Инкрементально, TDD для чистых функций planner, verify + review после каждого шага, коммитить по шагам (пуш/merge только по явной просьбе).
- Backend через ORM/DRF-паттерны, не «велосипед». Соблюдать инварианты раздела 4.
- Обновить `docs/lesson-scheduling.md` при изменении семантики операций.
- Новые эндпоинты/операции — обязательно RBAC `IsManagerOrAdmin` + аудит `log_event` + строгие сериализаторы; фронт-модалки на существующих form-компонентах.

---

## 9. Данные dev-БД: тестовые/заброшенные группы (решение пользователя — не трогать)
Диагностика нашла 9 активных групп со стартом, но БЕЗ фактов (среди них «Тестовая группа», «Python №353 (не факт)») — их план проецируется в будущее. Это не баг кода, а гигиена данных. Пользователь решил **оставить как есть** (архивировать вручную при желании: `active=false`).

---

## 10. Состояние git
Ветка `feature/lesson-scheduling-materialized`, 18 коммитов (от `82765c4` docs до `1f38976` таблица «Обзор»), дерево чистое. **Не смёржено и не запушено** — по правилу проекта делать это только по явной просьбе.
