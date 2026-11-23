# Eduson Kids — платформа школы (форк KOTOKOD)

Платформа онлайн-школы **Eduson Kids** (прежняя система — «OKK · Реестр»). Единый вход `/login` с выбором роли + 2FA; teacher SPA `/teacher` и admin SPA `/admin` (React 19 + TanStack Query v5 + React Router v7). Всё на PostgreSQL.

> **Бэкенд — Python Django+DRF (`journal_django/`).** Код — форк платформы школы KOTOKOD (решение владельца 2026-10-15, «вариант B»: весь функционал KOTOKOD постепенно переезжает в Eduson). Упоминания KOTOKOD, `server.js`, `services/*.js`, `computeFifo` в комментариях кода — историческая справка о происхождении. Создание учётки — `manage.py bootstrap_admin`. В корне: документация, `journal_django/`, Telegram-бот `bot/`, скрипты `scripts/`, Dockerfile и `deploy/` (nginx, Docker staging, Ansible staging).

Этот файл — действующие инструкции для всей папки `platform/`. Перед работой над Eduson читать его и учитывать вложенные `AGENTS.md`, если они появятся. При изменении архитектуры или правил обновлять этот файл. Исторические спеки и планы могут отставать от кода; статус реализации сверять с текущим кодом и Git.

Актуализировано: 2026-10-29 по локальному состоянию репозитория (последний коммит `a76ca91`, 2026-10-27). Состояние работающего сервера проверяется отдельно.

## Eduson ↔ KOTOKOD — ОБЯЗАТЕЛЬНО

- **Это два разных проекта.** Работа над Eduson **никак** не меняет KOTOKOD (прямое требование владельца). Все правки — только внутри `EdusonPlatform/`. Родительский репозиторий (`TestKOTOKOD`, публичный GitHub KOTOKOD) — ни строчки: ни код, ни `.gitignore`, ни docs, ни корневой `CLAUDE.md`.
- **Раскладка `EdusonPlatform/`** (вся папка закрыта внешним `.gitignore` со строкой `*` — данные Eduson содержат ПДн и не должны попасть в публичный репо KOTOKOD):
  - `platform/` — этот форк, **свой Git и отдельный remote Eduson**;
  - `legacy/` — исходный код старого Eduson (Express + node:sqlite + React 18), только для справки;
  - `data/` — боевая SQLite-копия, бэкапы, seed (ПДн!);
  - `docs/` — спеки и планы этапов (`docs/specs/`, `docs/plans/`), визуальный стиль `docs/design-system.md`.
- **Общие с KOTOKOD dev-ресурсы не трогать:** у Eduson свои имена и порты — БД `eduson`/`eduson_test` (не `journal`/`journal_test`), префикс кэша `eduson`, Redis db 2 при локальной разработке без Docker (в staging отдельный контейнер Redis, db 0), runserver `127.0.0.1:8100`, Vite admin 5183 / teacher 5184, локальный nginx `127.0.0.1:8180` (CSP-сток 19976).
- **Прод старой системы Eduson** — **только чтение**, ничего не удалять и не менять. Адрес и доступы — у владельца, в репозиторий не записываются.
- Remote Eduson — `https://github.com/ReyTayler/EdusonKids-teaching-platform.git`. Архив истории до публикации — `../platform-history-before-publish.bundle`; это локальный архив, не материал для публикации. Не переписывать историю без явного запроса владельца. Почты суперадминов в код и коммиты не класть.
- Субагенты git не запускают (только чтение); коммитит координатор.

## Этапы Eduson

1. **Стек и импорт реализованы.** Django + DRF + PostgreSQL, импорт — `apps/eduson_import/`; SQLite открывается только на чтение. Спека: `../docs/specs/2026-10-15-eduson-fork-design.md`, план: `../docs/plans/2026-10-16-eduson-stage1-plan.md`, отчёт: `docs/eduson/import-report-2026-10.md`.
   - Оплаты не импортируются. Уникальность `Student.full_name` снята; идентификация учитывает имя и контакты семьи. Пароли — `pbkdf2_wrapped_bcrypt`. Суперадмин создаётся вручную через `bootstrap_admin --email <почта>`.
   - Прогресс: `Group.lesson_number_offset = N`, `GroupMembership.lessons_done = N + вес проведённых уроков`. Ошибочный июнь 2027 исправляется на 2026; ранние уроки дополняются из CSV Sheets через `--sheets-dir`, уменьшая N. Правила — спека §5.4 и поправки владельца 2026-10-19, `analysis.corrected_year` / `analysis.sheet_backfill`.
   - `Group.lessons_total` не берётся из `package_size`: длина курса — из направления. Курс legacy `groups` распределяется по первой букве кода: П → Python, С → Scratch, В → Вайб-кодинг; неизвестная буква — ошибка импорта. ID Sheets — только в окружении.
   - Штатное обновление — `scripts/refresh_from_prod.sh --soft` или кнопка в «Синхро» (superadmin, `EDUSON_PROD_REFRESH_BUTTON`). `sync_eduson` использует `EdusonLink`, заменяет членства/уроки/план/отзывы импортированных групп в транзакции, сохраняет существующие учётки, продления и ручные суммы зарплат. Инструкция — `docs/eduson/refresh-from-prod.md`.
   - Полная пересборка без `--soft` заменяет рабочую БД через `_next`/`_prev` и стирает локальные учётки, настройки и ручные правки; только вручную на крайний случай. Ежедневного обновления нет. Пересборки KOTOKOD из «Синхро» удалены: они несовместимы с офсетами и стадиями Eduson. `backfill_planned_lessons` на группах Eduson не запускать: игнорирует офсет.
2. **Этап 2 — фронт в визуальном языке Eduson** (спека `../docs/specs/2026-10-19-eduson-frontend-redesign.md`, ✅): дизайн **кардинально отличается от KOTOKOD** — монохром Vercel Geist, шрифты Geist/Geist Mono (self-hosted), акцент чёрный (в тёмной теме белый), линии вместо теней, цвета только у статусов и курсов (`--t-*`). Admin — левая колонка навигации со SVG-значком у каждой группы, рельса, мобильное меню на `ui/Dialog`; Teacher — левая панель разделов с рельсой и мобильным выезжающим меню на Radix Dialog (как admin). Ключ темы — `eduson-theme`. При любой правке фронта сохранять вид Eduson, а не возвращать KOTOKOD.
3. **Функции Eduson реализованы:** обязательный отзыв при записи урока преподавателем, реестр по остатку курса и сигналы куратору, напоминания семьям, мягкая синхронизация старой системы, движок «Мотивации», причины отмен и компенсации. С 2026-10-27 ссылка на запись урока обязательна и проверяется как URL. Заморозка в расписании отменена владельцем; используется стадия «Заморозка» в продлениях. Telegram-бот и доставка уведомлений описаны ниже. Отложенные задачи — `docs/eduson/backlog.md`; пометки в старых планах не являются текущим статусом.
4. **Staging подготовлен и выкладывался на сервер с 2026-10-26:** Docker Compose + Ansible staging. Последний блок 2026-10-27 добавляет Redis, Celery worker/beat и Telegram-бота. Наличие коммита не подтверждает его запуск на сервере; перед продолжением выкладки проверить фактическую версию и smoke-сценарии. Переход новой платформы в основной прод — отдельный этап: `docs/eduson/stage4-deploy-backlog.md`.

## 🔒 Безопасность — ОБЯЗАТЕЛЬНО

**Полный свод правил: [`docs/security-guidelines.md`](docs/security-guidelines.md)** — читать перед добавлением ЛЮБОЙ фичи/правки; там же чеклист для PR.

Самое критичное (нарушение = блокер):

- **RBAC**: DRF default = `AllowAny`. КАЖДАЯ новая вьюха ОБЯЗАНА задать `permission_classes` (`IsAdmin`/`IsManagerOrAdmin`/`IsTeacher`). Забыл → эндпоинт открыт всем. Доступ проверяется на API, не только на фронте (фронт-guard = UX/defense-in-depth).
- **Auth**: JWT только в HttpOnly-cookie (не в JS/localStorage); токены — через `issue_tokens_for`/`set_auth_cookies`. Отзыв — инкремент `token_version` (при смене/сбросе пароля и 2FA). Свою аутентификацию не изобретать.
- **CSP `script-src 'self'`**: НИКАКИХ inline-`<script>`, `onclick=`, `eval`. Весь JS — внешним файлом same-origin. Внешний origin (CDN/API/шрифты) — только с обновлением CSP.
- **CSRF**: мутирующие методы шлют `X-CSRFToken`; не ставить `@csrf_exempt`.
- **Rate-limit** (Django + nginx) на всех auth/OTP/reset-эндпоинтах.
- **SQL** только параметризованный (`%s`); идентификаторы — из whitelist.
- **Секреты** только из окружения; не коммитить (`.env`, ключи, дампы `backups/`, ПДн). Аудит чувствительных действий — `log_event`, без секретов в `meta`.
- **Мотивация сотрудников конфиденциальна:** ставки, начисления, штрафы, ручные суммы и зарплаты — только superadmin (серверный `IsSuperAdmin`, исключение полей в teachers/lessons). Преподаватель видит только свои начисления в своём кабинете. В ленте журнала для остальных — формальное описание без цифр; детали и откат операций зарплаты — только superadmin. Проверки — `changelog/summary.py`, `labels.is_motivation_operation`, `_PAYROLL_ROW_KEYS` в lessons/views.py.


## Критичные соглашения

**Git**: отдельный репозиторий `EdusonPlatform/platform/`, ветка `main`, remote `origin` — `https://github.com/ReyTayler/EdusonKids-teaching-platform.git`. Коммитить и пушить только по явной просьбе владельца. Секреты, ПДн и дампы не включать в Git. CI не настроен; проверки выполняются локально. Состояние remote проверять отдельно: локальная `origin/main` может быть устаревшей.

**Docker используется для staging Eduson.** `Dockerfile`, `deploy/docker/compose.staging.yml` (локально), `compose.server.yml` (сервер), `deploy-staging.sh` (sync/release), `rebuild.ps1` (локальная пересборка). Образы релиза строятся из `git archive` конкретного коммита. Локальная разработка без Docker также поддерживается. Ограничения Docker исходного KOTOKOD к Eduson не применять.

Соблюдать структуру Django/DRF и существующие паттерны проекта. Использовать возможности фреймворков и официальную документацию вместо самостоятельного дублирования стандартных механизмов.

**Порядок маршрутов** (`config/urls.py`): `/api/auth` → `/api/admin/*` → `/api/integrations/*` → общий `/api` (teacher). Общий `/api` — последним, иначе перехватит более узкие префиксы.

**Часовой пояс**: `TIME_ZONE='Europe/Moscow'`, `USE_TZ=True`; сервисы (gunicorn, Celery) запускаются с `TZ=Europe/Moscow` (в Docker — настройки образа и Compose). Teacher-отчёт считает статус урока по локальному времени — другой TZ уводит статус на день.

**`payments` immutable**: только POST/DELETE, никакого PATCH. `total_amount = unit_price × subscriptions_count` — CHECK в БД + пересчёт на сервере. `unit_price` округляется до копеек ДО умножения.

**Half-lesson**: `duration=45min → 0.5 урока`, иначе 1. Канон веса — `apps/lessons/weights.py`.

**Гард оплат** — `settings.PAYMENT_BALANCE_GUARD` (по умолчанию `False`: Eduson оплаты в платформе не ведёт). Единственная точка проверки при записи — `apps/lessons/repository.assert_students_paid` (урок, ячейка, доп.урок); у продлений — `apps/renewals/repository.move_deal` (балансовый гейт «Продлён») и маршрутизация в авто-стадию «Ждём оплату» (`engine._target_auto_stage`, `rebuild.plan_for_student`: без гарда сделка идёт по «Урок N», рубеж цикла не меняется). Teacher SPA получает флаг в `GroupData.paymentGuard`, карточка канбана продлений — в `payment_guard`. Тесты поведения KOTOKOD подключают фикстуру `payment_balance_guard_on` (корневой `conftest.py`).

**Зарплата преподавателей — движок «Мотивации»** (спека `../docs/specs/2026-10-20-eduson-motivation-payroll-design.md`, 2026-10-21). Ставки KOTOKOD (`calculator.py`) удалены. Правила — только `apps/payroll/engine.py` (чистые функции), пересчёт по БД — `apps/payroll/recalc.py` (`recalculate`, `recalculate_cancellations`), API ставок — `apps/payroll/motivation.py`, экран — «Система → Настройки → Мотивация» (superadmin).
- Ставка: формат группы × направление, версия на дату урока (`PayRateVersion`/`PayRateDirection`); нет версии → 0 с пояснением. Персональные ставки — `PersonalRate` и датированные `PersonalRateTerm`, ограничения пересечений — EXCLUDE (`btree_gist`).
- **Изменения ставок не меняют прошлое** (спека §8): правка/выключение/удаление закрывает период сегодня, версия школы — не раньше сегодня; начавшиеся версии не редактируются и не удаляются. Пересчёт после мутаций — с сегодня; ставки не откатываются через журнал изменений. Полный пересчёт — `manage.py recalculate_payroll [--from --to --group --dry-run]`, с сохранённой историей ставок.
- Отмены — `CancellationPay`, причины — `apps/cancellations`, спека `../docs/specs/2026-10-23-eduson-lesson-cancel-reasons-design.md`. `compensated=False` → 0, `True` → правило «Мотивации» (%/фикс). Legacy-отмена без записи причины оплачивается по прежнему правилу. Свободный комментарий «Другой причины» преподавателю не показывать.
- Снимок `LessonCancellation` неизменяем и не удаляется; SET NULL переживает удаление маркера. Повторная привязка — через `cancellations.repository.bind_for_markers`, вслед за начислением; не добавлять самостоятельную эвристику по дню. Правила ключей, сирот и дублей — спека отмен и реализация repository.
- Начисления хранятся в `Payroll`/`CancellationPay`, обновляются при записи/правке уроков, посещаемости, отменах/заменах/переносах, пересборке плана и мягком sync. `is_manual` сохраняет ручную сумму, обновляются только `calc_*`; сброс — API `…/reset`. Ручная сумма не переносится другому преподавателю.
- Штраф за поздний отчёт — только записи кабинета (`acct:`). Сгорание не оплачивается. Урок без присутствующих не записывается; последнего присутствующего снять нельзя.
- **Порядок блокировок: урок → план → записи отмен → деньги.** Хуки расписания пересчитывают только отмены; удаление маркеров — после `lock_plan_rows`; мутации версий — под общим advisory-замком. Подробности — шапка `apps/payroll/recalc.py` и спека.

**Балансы выводятся, не хранятся**: `get_student_balance()` (`apps/finances/balance.py` — единственный дом расчёта; одноимённые функции в `students`/`payments` лишь делегируют сюда) = куплено − пройдено **общим пулом по всем направлениям** (с 2026-07-08; `payments.direction_id` — информационная метка, разбивки по направлениям только справочные).

**FIFO-финансы**: `compute_fifo` (`apps/finances/fifo.py`, точный `Decimal`, округление до копеек один раз на выходе). Партии строит **только** `build_lots` (`apps/finances/lots.py`): цена урока = `total_amount / lessons_count` оплаты; с доплатами (`kind='surcharge'`) оплата дробится на блоки по 4 урока; возвраты — синтетические списания, не партии. Guard: оплаты с `lessons_count=NULL/0` пропускаются (деление на ноль ломает суммы). Подробнее: `docs/finances.md`.

**Журнал изменений (pghistory)**: новые доменные модели ОБЯЗАНЫ получать `@pghistory.track(InsertEvent(), UpdateEvent(), DeleteEvent())` + запись в `apps/changelog/registry.py` (тест `test_registry_covers_all_tracked_models` упадёт, если забыть) + миграцию. Метки операций — `apps/changelog/labels.py` (новый мутирующий URL → новое правило). Секретные поля — в `exclude` декоратора.

**Paginator**: пагинация используется встроенная от фреймворка (`apps/core/pagination.StandardPagination`, 50 на страницу), никаких постоянных пагинаций от SELECT.

**Направление сортировки** из запроса — только из whitelist: `sort_dir not in ('asc', 'desc')` → значение по умолчанию или 400 (как в `apps/accounts/views.py`, `apps/audit/views.py`). Сырое значение в `ORDER BY` не подставлять.

**ErrorBoundary key**: `key={location.pathname}`, NOT `key={location.key}` (последнее ремоунтит на каждый setSearchParams → потеря фокуса инпутов).

**`.data-table--loading`**: гасить `pointer-events` только на `tbody`, не на всей таблице.

**`placeholderData: keepPreviousData`** обязателен во всех server-paginated хуках.

**Native form-элементы запрещены** в admin SPA: использовать `SelectInput`, `DateInput`, `Checkbox`, `Combobox` из `components/form/`. Enum-labels — только из `lib/labels.ts`.

**Design tokens** — единственный источник: `journal_django/frontend/admin-src/src/styles/tokens.css`. Никаких hardcoded цветов/радиусов/отступов. Значения — визуальный язык Eduson (`../docs/design-system.md`); `docs/design-system.md` внутри форка — наследие KOTOKOD (механика токенов и компонентов). Общие CSS-токены обязаны быть в **обоих** `tokens.css` (admin и teacher), иначе `var()` молча схлопывается.

**`AuthProvider`**: поле `me`, не `user`. `GET /api/auth/me` возвращает **плоский** объект (`{ account_id, email, role, teacher_id, name, twofa_enabled }`) — БЕЗ обёртки `{ me: {...} }`; `api<Me>()` отдаёт его как есть.

**Wiki**: один список документов без плиток, избранного и архивной подборки. Папки — над списком; до 640px — один выпадающий выбор папки со счётчиками. DELETE документа удаляет запись и зависимые связи физически; перед удалением интерфейс предупреждает о невозможности восстановления. Существующие архивные записи автоматически не очищаются. Права чтения и записи, серверный поиск и пагинация сохраняются.

## Конфигурация (.env)

Файл — `platform/.env` (не коммитится); образец с пояснениями — `.env.example`. Серверный режим — `DJANGO_SETTINGS_MODULE=config.settings.production`, используется и на staging. Конфигурация staging — отдельные env-файлы, образцы и скрипт в `deploy/docker/`. Наличие и значения секретов проверять без вывода их содержимого.

Образец переменных — `.env.example`, Docker — `deploy/docker/.env.staging.example` и `bot.env.example`. Не дублировать секреты в документации. Важное:
- `PAYMENT_BALANCE_GUARD=false` — оплаты в Eduson не ведутся.
- `REDIS_URL` включает Redis/Celery; без него — eager-выполнение задач и LocMemCache, расписанию нужен beat.
- `TELEGRAM_BOT_TOKEN` журнала совпадает с `BOT_TOKEN` бота, `BOT_SERVICE_TOKEN` общий для интеграции; `TELEGRAM_GENERAL_CHAT_ID` — дубль уведомлений в общий чат.
- Wiki в Docker — `/srv/media`, общий volume app/web; `KNOWLEDGE_X_ACCEL_PREFIX` согласован с internal location nginx.
- SMTP используется для email-OTP и писем семьям; родительские напоминания по умолчанию выключены.


**Telegram-уведомления** — `apps/notifications/`, единственное место в журнале, знающее про Telegram. Очередь исходящих (`notification_messages`) лежит в PostgreSQL: доменные сервисы (`scheduling`, `extra_lessons`) вызывают `notifications.services.notify_teacher()` **внутри своей транзакции** (transactional outbox — либо операция и сообщение, либо ничего), а Celery-beat раз в минуту разгребает очередь и шлёт через Telegram Bot API. Бот в доставке не участвует. Идемпотентность — уникальный `dedup_key` на уровне БД. **Бот** — `platform/bot/` (README там): отвечает на `/start`, `/help`, `/my` и сообщает журналу привязку аккаунтов. Его `BOT_TOKEN` = `TELEGRAM_BOT_TOKEN` журнала; `BOT_SERVICE_TOKEN` — общий секрет журнал↔бот. На staging — контейнеры `redis`, `worker`, `beat`, `bot` (`deploy/docker/compose.server.yml`); секреты бота — `bot.env`, его кладёт `deploy-staging.sh sync`. Одним токеном опрашивать Telegram может только один процесс. Дайджесты: расписание 8:00, незаполненные отчёты 21:00 (оба только в личку); точечные уведомления об изменениях — личка + дубль в общий чат. Импорт `apps.notifications` из доменных приложений — **только локальный, внутри функции**.

**Email-напоминания Eduson** (спека этапа 3, раздел D) — та же очередь, `channel='email'` (`recipient_email` + `subject` вместо `chat_id`; CHECK по каналу), транспорт `email_transport.py` (`send_mail`). Письма идут **только семьям учеников**: задачи `apps/notifications/reminders.py` — «урок завтра» 10:00 и «через час» (55–65 мин), под флагом `parent_reminders_enabled` (по умолчанию выключен) поверх общего `is_enabled`. **Преподавателям писем нет** — только Telegram-дайджесты 08:00/21:00 (`digests.py`); письма «Расписание на сегодня» и «Урок не записан» убраны по решению владельца 2026-10-27 (kind `lesson_unrecorded` остался в `KIND_CHOICES` только ради старых строк журнала доставки). Без Redis — `manage.py send_reminders <parent-day|parent-hour> [--dispatch]`. SMS — бэклог (нет провайдера).

## Тесты

`pytest` из `journal_django/`. Исторический baseline — `docs/eduson/baseline-tests.md`; актуальный результат устанавливается новым прогоном, а не старым числом тестов. Тесты идут только в `eduson_test` (guard в `config/settings/test.py` пускает лишь имена `eduson*_test` — защита и `eduson`, и БД KOTOKOD). Тестовые данные — выдуманные (`apps/core/tests/minimal_school.py`), не ПДн Eduson.

⚠️ Гонять **полный** `pytest -q`, а не по приложениям: часть приложений no-op'ит `django_db_setup` (общая `eduson_test`), часть пересоздаёт `test_eduson_test`. Прогон по частям даёт ложный результат.

Бот проверяется отдельно: `python -m pytest -q` из `bot/` с его dev-зависимостями.

Фронтенд проверять — `npx tsc --noEmit` и `npm run build` в обоих SPA + скриншоты через локальный nginx `127.0.0.1:8180` (хост именно `127.0.0.1`, не `localhost`).

## Производительность (учитывать при любой правке)

Ориентир — те же требования, что у KOTOKOD (VPS 4 CPU / 6 ГБ). Не читать «всё» там, где нужна часть. Индексы под реальные предикаты (PG не индексирует FK). Пагинация везде.

Каждую новую добавляемую или изменяемую фичу проверяй на производительность и способы оптимизации. В проекте уже используется Celery и Redis если что.


## Инфраструктура

**Локально** (Windows): runserver `127.0.0.1:8100` (`PYTHONUTF8=1 ../.venv/Scripts/python manage.py runserver 127.0.0.1:8100` из `journal_django/`), nginx Eduson `127.0.0.1:8180` → runserver, собранные SPA и CSP как на проде (`deploy/nginx/local/`, запуск — `start-local-nginx.ps1`; `.ps1` с кириллицей — UTF-8 BOM). Без nginx страницы SPA отдают 404.

**Staging Eduson** — отдельный Docker Compose-проект `eduson-staging` на сервере KOTOKOD: db, redis, разовая миграция migrate, app (gunicorn), web (nginx), worker, beat, bot. Конфиги — `deploy/docker/`, подготовка сервера — `deploy/ansible-staging/`. Существующие сервисы KOTOKOD не изменять. Секреты app/db/bot — отдельные env-файлы; `deploy-staging.sh sync` подготавливает файлы на сервере. Перед применением Ansible — `--check --diff`; выкатка на сервер только с согласия владельца.

**Обновление данных** — кнопка «Обновить данные с прода» или `refresh_from_prod.sh --soft`. При заданном `EDUSON_STAGING_SSH` после локального обновления staging получает тот же снимок, с предварительным бэкапом его БД. Сбой staging не откатывает уже выполненный локальный sync. Полная пересборка без `--soft` staging не обновляет. Подробности и откат — `docs/eduson/refresh-from-prod.md`.

**Основной прод новой платформы** — отдельный этап перехода со старой системы; staging не означает завершение миграции. Требования — `docs/eduson/stage4-deploy-backlog.md`.

**Старая система Eduson** (Express + node:sqlite в Docker) работает на сервере владельца. **Только чтение**; адрес и доступы в репозиторий не записываются.

## Backlog

Актуальные отложенные задачи Eduson — `docs/eduson/backlog.md`; требования к основному проду — `docs/eduson/stage4-deploy-backlog.md`. `docs/BACKLOG.md` — наследие KOTOKOD, пункты требуют проверки применимости.

Последний блок разработки — Telegram-бот и инфраструктура staging (2026-10-27). Известные задачи: различить сбой журнала и отсутствие привязки в `/my`, заменить синхронный HTTP в async-хендлерах бота, учитывать прогоны дайджестов без получателей. Перед новой выкладкой проверить `/my`, привязку, доставку уведомлений, worker/beat и вход с email-2FA; старые чекбоксы плана не подтверждают прохождение этих проверок.

## UI: parity с KOTOKOD (2026-11-23)

Дизайн-доработки переносятся в компоненты Eduson с сохранением его монохромных токенов и доменной логики. Справка открывается через `HelpPopover` рядом с заголовком/полем; в продлениях общая справка находится в `PageHeader`. Ошибки, финансовые предупреждения и текущие показатели остаются видимыми. `TableScrollArea` рассчитывает высоту от фактического положения таблицы до пагинации и нижнего отступа; таблицы в диалогах остаются в пределах диалога.

Обе SPA используют data router React Router для `useBlocker` общего редактора графика. Несохранённый график защищён при смене вкладки/недели/режима и переходе по маршруту; визуальный предпросмотр не сохраняет изменения автоматически.

Архив: `GET /api/admin/archive` (только superadmin), единый серверный поиск по преподавателям, группам и направлениям, фильтр типа и пагинация после объединения результатов. Счётчики считаются на сервере; нельзя искать только в загруженной странице. Архив сущностей не связан с удалением документов Wiki. Период зарплаты и остальные фильтры находятся в панели поиска шапки, отдельно поддержаны список и сводка. Бизнес-расчёты зарплаты, обязательные поля отчёта и правила Eduson при переносе UI не заменяются кодом KOTOKOD.
