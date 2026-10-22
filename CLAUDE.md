# Eduson Kids — платформа школы (форк KOTOKOD)

Платформа онлайн-школы **Eduson Kids** (прежняя система — «OKK · Реестр»). Единый вход `/login` с выбором роли + 2FA; teacher SPA `/teacher` и admin SPA `/admin` (React 19 + TanStack Query v5 + React Router v7). Всё на PostgreSQL.

> **Бэкенд — Python Django+DRF (`journal_django/`).** Код — форк платформы школы KOTOKOD (решение владельца 2026-10-15, «вариант B»: весь функционал KOTOKOD постепенно переезжает в Eduson). Упоминания KOTOKOD, `server.js`, `services/*.js`, `computeFifo` в комментариях кода — историческая справка о происхождении. Создание учётки — `manage.py bootstrap_admin`. В корне: документация, `db/` (историческая SQL-схема), `deploy/` (только локальный nginx).

## Eduson ↔ KOTOKOD — ОБЯЗАТЕЛЬНО

- **Это два разных проекта.** Работа над Eduson **никак** не меняет KOTOKOD (прямое требование владельца). Все правки — только внутри `EdusonPlatform/`. Родительский репозиторий (`TestKOTOKOD`, публичный GitHub KOTOKOD) — ни строчки: ни код, ни `.gitignore`, ни docs, ни корневой `CLAUDE.md`.
- **Раскладка `EdusonPlatform/`** (вся папка закрыта внешним `.gitignore` со строкой `*` — данные Eduson содержат ПДн и не должны попасть в публичный репо KOTOKOD):
  - `platform/` — этот форк, **свой git, только локальный, без remote**;
  - `legacy/` — исходный код старого Eduson (Express + node:sqlite + React 18), только для справки;
  - `data/` — боевая SQLite-копия, бэкапы, seed (ПДн!);
  - `docs/` — спеки и планы этапов (`docs/specs/`, `docs/plans/`), визуальный стиль `docs/design-system.md`.
- **Общие с KOTOKOD dev-ресурсы не трогать:** у Eduson свои имена и порты — БД `eduson`/`eduson_test` (не `journal`/`journal_test`), префикс кэша `eduson`, Redis db 2, runserver `127.0.0.1:8100`, Vite admin 5183 / teacher 5184, локальный nginx `127.0.0.1:8180` (CSP-сток 19976).
- **Прод старой системы Eduson** — **только чтение**, ничего не удалять и не менять. Адрес и доступы — у владельца, в репозиторий не записываются.
- ⚠️ **Перед первым push на любой remote — пересобрать историю git** (orphan/squash): первый коммит — снимок KOTOKOD с его инфраструктурой (IP, пути, бакет). Почты суперадминов в код и коммиты не класть.
- Субагенты git не запускают (только чтение); коммитит координатор.

## Этапы Eduson

1. **Этап 1 — стек и данные** (спека `../docs/specs/2026-10-15-eduson-fork-design.md`, план `../docs/plans/2026-10-16-eduson-stage1-plan.md`): данные Eduson легли **на модели KOTOKOD**. Импорт — `apps/eduson_import/` (`sqlite_source` открывает SQLite только на чтение, `normalize`, `identity` — склейка учеников по связке имя + телефон родителя + возраст ±1 + имя родителя, `steps/{people,groups,lessons,renewals}`, `reconcile` — сверка). Команда: `PYTHONUTF8=1 python manage.py import_eduson <путь к sqlite> [--as-of] [--dry-run] [--superadmin EMAIL] [--sheets-dir DIR]`. Отчёт — `docs/eduson/import-report-2026-10.md`.
   - Решения владельца: оплаты не импортируются; отзывы — новая модель `apps/feedback` (`LessonFeedback`); уникальность `Student.full_name` снята; админ, который ведёт уроки → роль teacher; продления — логика KOTOKOD + 12 стадий Eduson.
   - «Пройдено до учёта» — честный пересчёт: `Group.lesson_number_offset = N`, `GroupMembership.lessons_done = N + вес проведённых held`. Правила — спека §5.4 и «Поправки владельца 2026-10-19».
   - Held с датой следующего года (544 шт. «2027-06-DD» — вкладка «Июнь 2027» таблиц Eduson вместо «Июнь 2026») — **настоящие уроки**: импорт заводит их проведёнными на дату годом раньше (`analysis.corrected_year`, `ImportContext.year_fixed`, `submitted_by_token='eduson-import:year-fixed'`), с посещаемостью и без зарплаты. Фиктивными (→ в N) остаются только held, чей год не исправить (дата −1 год всё ещё в будущем).
   - Уроки до запуска старой системы — из таблиц Eduson: `import_eduson … --sheets-dir DIR` (CSV вкладок «Июнь 2026»/«Июнь 2027» четырёх таблиц, `{course}-2026-06.csv`/`{course}-2027-06.csv`; разбор — `sheets.py`, правило — `analysis.sheet_backfill`). Только группы с N > 0: отметки TRUE раньше первого проведённого урока группы и ≤ as_of («Июнь 2027» → 2026-06-DD); кандидатов больше N — N самых поздних + ручная проверка. Заводятся проведёнными, как year-fix, с `submitted_by_token='eduson-import:sheets'`; N уменьшается на их число. Без параметра — как раньше. ID таблиц — только в `.env` (`EDUSON_SHEET_*`), в репозиторий не кладутся (в таблицах ПДн).
   - Пароли Eduson (bcrypt) — обёрнутый хешер `apps/accounts/hashers.py` (`pbkdf2_wrapped_bcrypt`).
   - Суперадмин создаётся владельцем вручную (`bootstrap_admin --email <почта>`); почты учёток в репозиторий не записываются.
   - Решения владельца 2026-10-19, закреплённые в импорте: `Group.lessons_total` не заполняется из `package_size` (длина курса — из направления); направления «Группа» нет — курс Eduson `groups` разносится по первой букве кода группы (П → Python, С → Scratch, В → Вайб-кодинг), неизвестная буква → `ImportDataError`.
   - **Обновление из прода** (на время переработки платформы прод старой системы — источник правды): `scripts/refresh_from_prod.sh` снимает копию SQLite только чтением (`node:sqlite` readOnly + `VACUUM INTO`), скачивает CSV таблиц Eduson в `sheets-<метка>/` рядом со снимком (по gid вкладки из списка `htmlview`; не задано или не скачалось — импорт без `--sheets-dir`, предупреждение в лог), собирает `<рабочая>_next` (`migrate` + `import_eduson`) и подменяет ею рабочую, прежняя → `_prev`. **Всё введённое локально при этом затирается.** После подмены перезапустить runserver/Celery. Конфиг — `EDUSON_PROD_*`/`EDUSON_SNAPSHOT_*`/`EDUSON_SHEET_*` в `.env`; подробно — `docs/eduson/refresh-from-prod.md`.
   - **Мягкое обновление** (решение владельца 2026-10-20): `refresh_from_prod.sh --soft` или кнопка «Обновить данные с прода» в «Синхро» (superadmin, флаг `EDUSON_PROD_REFRESH_BUTTON`; `apps/sync/prod_refresh.py` запускает скрипт отдельным процессом). Без подмены базы — `manage.py sync_eduson` в одной транзакции: ученики/группы/преподаватели обновляются на месте по таблице соответствий `EdusonLink` (`eduson_links`, пишут оба импорта), членства/уроки/план/отзывы групп старой системы заменяются целиком (`apps/eduson_import/sync.py`, без журнала изменений), новые учётки добавляются, существующие не трогаются. **Продления не трогаются.** Ежедневной задачи нет (удалена владельцем 2026-10-20): обновление — кнопкой, полная пересборка — только вручную, на крайний случай (стирает учётки, настройки, ручные правки).
   - «Синхро» (2026-10-20): осталась только «Планы групп — проверка» и мягкое обновление; пересборки KOTOKOD (зарплата, счётчики, план, доп.уроки-пропуски, продления) удалены — стирали офсеты, расписание и стадии Eduson.
2. **Этап 2 — фронт в визуальном языке Eduson** (спека `../docs/specs/2026-10-19-eduson-frontend-redesign.md`, ✅): дизайн **кардинально отличается от KOTOKOD** — монохром Vercel Geist, шрифты Geist/Geist Mono (self-hosted), акцент чёрный (в тёмной теме белый), линии вместо теней, цвета только у статусов и курсов (`--t-*`). Admin — левая колонка навигации со SVG-значком у каждой группы, рельса, мобильное меню на `ui/Dialog`; teacher — верхняя шапка. Ключ темы — `eduson-theme`. При любой правке фронта сохранять вид Eduson, а не возвращать KOTOKOD.
3. **Этап 3 — функции Eduson** (впереди): форма отзыва после урока в teacher SPA, сигналы куратору, напоминания, синхронизация с Sheets, заморозка. Бэклог — `docs/eduson/backlog.md`.
4. **Этап 4 — выкладка** (впереди, только с согласия владельца): `docs/eduson/stage4-deploy-backlog.md`.

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

## Критичные соглашения

**Git**: свой репозиторий форка (`EdusonPlatform/platform/`), ветка `main`, **remote нет**. Секреты, ПДн и дампы в него не попадают ни при каких условиях. Пушить некуда; перед появлением remote — переписать историю (см. выше). Разработка локально (БД `eduson`, runserver :8100), CI нет.

**Docker не использовать** в новой платформе (стек KOTOKOD, решение владельца KOTOKOD). Старый Eduson на своём сервере работает в Docker — это не наш стек.

**Эталонная структура проекта**: так как в основе проекта лежит Python Django+DRF, необходимо строго соблюдать структуру для проекта, которая считается эталонной для такого фреймворка из документации и best practice.

**Не придумывать "велосипед"**: использовать максимум возможностей фреймворков, которые указаны в проекте, не писать всё с нуля, если это уже было придумано в самом фреймворке (пример - не использовать свою систему аутентификации и авторизации, когда есть встроенные механизмы от Django).

**Всегда опираться на документации и существующие паттерны**: не выдумывать что-то своё, использовать всегда знания, в первую очередь, из официальных документаций к используемым в проекте инструментам.

**Быть строгим senior-разработчиком**.

**Порядок маршрутов** (`config/urls.py`): `/api/auth` → `/api/admin/*` → `/api/integrations/*` → общий `/api` (teacher). Общий `/api` — последним, иначе перехватит более узкие префиксы.

**Часовой пояс**: `TIME_ZONE='Europe/Moscow'`, `USE_TZ=True`; сервисы (gunicorn, Celery) запускаются с `TZ=Europe/Moscow` (юниты `deploy/systemd`). Teacher-отчёт считает статус урока по локальному времени — другой TZ уводит статус на день.

**`payments` immutable**: только POST/DELETE, никакого PATCH. `total_amount = unit_price × subscriptions_count` — CHECK в БД + пересчёт на сервере. `unit_price` округляется до копеек ДО умножения.

**Half-lesson**: `duration=45min → 0.5 урока`, иначе 1. Канон веса — `apps/lessons/weights.py`.

**Гард оплат** — `settings.PAYMENT_BALANCE_GUARD` (по умолчанию `False`: Eduson оплаты в платформе не ведёт). Единственная точка проверки при записи — `apps/lessons/repository.assert_students_paid` (урок, ячейка, доп.урок); у продлений — `apps/renewals/repository.move_deal` (балансовый гейт «Продлён») и маршрутизация в авто-стадию «Ждём оплату» (`engine._target_auto_stage`, `rebuild.plan_for_student`: без гарда сделка идёт по «Урок N», рубеж цикла не меняется). Teacher SPA получает флаг в `GroupData.paymentGuard`, карточка канбана продлений — в `payment_guard`. Тесты поведения KOTOKOD подключают фикстуру `payment_balance_guard_on` (корневой `conftest.py`).

**Зарплата преподавателей — движок «Мотивации»** (спека `../docs/specs/2026-10-20-eduson-motivation-payroll-design.md`, 2026-10-21). Ставки KOTOKOD (`calculator.py`) удалены. Правила — только `apps/payroll/engine.py` (чистые функции), пересчёт по БД — `apps/payroll/recalc.py` (`recalculate`, `recalculate_cancellations`), API ставок — `apps/payroll/motivation.py`, экран — «Система → Настройки → Мотивация» (superadmin).
- Ставка за урок — фикс по формату (`Group.is_individual`) × направлению; версии ставок датированы (`PayRateVersion` + `PayRateDirection`), урок считается по версии на свою дату; нет версии → 0 «Нет ставки на …». Персональные ставки (`PersonalRate` — сущность с `is_active`/`deleted_at`, параметры — в периодах `PersonalRateTerm` с `valid_from`/`valid_until`): преподаватель × группа × диапазон номеров курса, «вместо / +₽ / +%», точнее побеждает среди периодов, действующих на дату; непересечение периодов по урокам и датам — EXCLUDE в БД (`btree_gist`). **Изменения ставок не меняют прошлое** (поправка §8 спеки): правка/выключение/удаление персональной ставки закрывают период сегодняшним днём, новая версия школы — не раньше сегодня, начавшуюся не правят и не удаляют; пересчёт после мутаций ставок — с сегодня. Отмена (`CancellationPay`, маркер `PlannedLesson` status=cancelled) — % от ставки или фикс; переживает удаление маркера (SET NULL), привязывается к новому по ключу группа+дата+время (запасной — по дню); прошлые сироты остаются, будущие удаляются. Штраф за отчёт не в день урока — только записи из кабинета (`acct:`), настраивается. Сгорание — 0. Урок без присутствующих не записывается, последнего пришедшего не снять.
- Начисление хранится (`Payroll`/`CancellationPay`) и пересчитывается движком во всех поводах (таблица §3 спеки): запись/правка урока и посещаемости, отмена/замена/перенос маркера, пересборка плана, мутации ставок (с сегодняшнего дня: период версии / группа), мягкое обновление. Ручная правка суммы → `is_manual` (пересчёт обновляет только `calc_*`), сброс — `…/reset`; ручные правки переживают `sync_eduson`.
- **Порядок блокировок — урок → план → деньги** (шапка `recalc.py`). Хуки расписания пересчитывают только отмены (уроки не лочат); удаление маркеров — после `scheduling.repository.lock_plan_rows`; мутации версий — под общим advisory-замком.
- Мутации ставок пересчитывают начисления в HTTP-запросе только с сегодняшнего дня (прошлое по §8 не меняется), поэтому объём ограничен будущими уроками и отменами затронутого периода или группы. Ставки в «Журнале изменений» видны, но не откатываются (`revertable=False`): откат обошёл бы §8 и права superadmin. Полный пересчёт истории — только командой `manage.py recalculate_payroll [--from --to --group --dry-run]`; прошлое он не меняет, потому что история ставок хранится в данных (версии, периоды персональных ставок).

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

## Конфигурация (.env)

Файл — `platform/.env` (не коммитится); образец с пояснениями — `.env.example`. Прод-режим — `DJANGO_SETTINGS_MODULE=config.settings.production` (прода у форка пока нет). Telegram/Sheets-переменные ниже унаследованы от KOTOKOD; у Eduson пусты, пока не настроены свои.

```
DATABASE_URL=postgresql://<user>:<pass>@localhost:5432/eduson
SECRET_KEY=                   # 128-hex, в проде обязателен
ADMIN_COOKIE_SECRET=          # 128-hex; запасной источник ключа для dev
ALLOWED_HOSTS=                # домен; в проде пусто = не пустит никого
CSRF_TRUSTED_ORIGINS_LIST=    # https://<домен> — SPA и API на одном origin
CORS_ORIGINS=                 # пусто = кросс-ориджин запрещён (так и нужно)
REDIS_URL=                    # без него — синхронно, LocMemCache, без Celery-задач
SMTP_HOST/PORT/USER/PASS/FROM # SMTP для email-OTP и email-напоминаний (у Eduson свой, пока не настроен)
PUBLIC_URL=                   # https://<домен> — ссылка на кабинет в письмах; пусто → без ссылки
PAYMENT_BALANCE_GUARD=false   # гард оплат KOTOKOD (нельзя отметить ученика с балансом <= 0); у Eduson выключен — оплат в платформе нет
# Telegram-уведомления:
TELEGRAM_BOT_TOKEN=           # токен Telegram-бота школы (у Eduson пока не настроен)
TELEGRAM_GENERAL_CHAT_ID=     # общий чат сотрудников (-100...); пусто → дубли в чат не идут
BOT_SERVICE_TOKEN=            # общий секрет журнал ↔ бот для /api/integrations/telegram
NOTIFICATIONS_HISTORY_LIMIT=200  # сколько ЗАВЕРШЁННЫХ записей очереди хранить
KNOWLEDGE_MEDIA_ROOT=          # корень хранилища картинок базы знаний; прод — /var/www/journal-media
KNOWLEDGE_X_ACCEL_PREFIX=      # префикс internal-локации nginx; пусто → Django отдаёт файлы сам (локально)
# Legacy (Phase 5):
STUDENTS_SPREADSHEET_ID=
JOURNAL_SPREADSHEET_ID=       # нужен для backfill-payments
```

**Telegram-уведомления** — `apps/notifications/`, единственное место в журнале, знающее про Telegram. Очередь исходящих (`notification_messages`) лежит в PostgreSQL: доменные сервисы (`scheduling`, `extra_lessons`) вызывают `notifications.services.notify_teacher()` **внутри своей транзакции** (transactional outbox — либо операция и сообщение, либо ничего), а Celery-beat раз в минуту разгребает очередь и шлёт через Telegram Bot API. Бот в доставке не участвует. Идемпотентность — уникальный `dedup_key` на уровне БД. Дайджесты: расписание 8:00, незаполненные отчёты 21:00 (оба только в личку); точечные уведомления об изменениях — личка + дубль в общий чат. Импорт `apps.notifications` из доменных приложений — **только локальный, внутри функции**.

**Email-напоминания Eduson** (спека этапа 3, раздел D) — та же очередь, `channel='email'` (`recipient_email` + `subject` вместо `chat_id`; CHECK по каналу), транспорт `email_transport.py` (`send_mail`). Задачи — `apps/notifications/reminders.py`: расписание преподавателю 08:00, «урок не записан» (окно 3 ч после конца, повторы раз в сутки, не больше 3), родителям «урок завтра» 10:00 и «через час» (55–65 мин). Родительские — под флагом `parent_reminders_enabled` (по умолчанию выключен) поверх общего `is_enabled`. Адрес преподавателя — email учётки, запас — карточки. Без Redis — `manage.py send_reminders <schedule|unrecorded|parent-day|parent-hour> [--dispatch]`. SMS — бэклог (нет провайдера).

## Тесты

`pytest` из `journal_django/` (~3070 тестов; на 2026-10-19 — 0 failed, 3 skipped). Тесты идут только в `eduson_test` (guard в `config/settings/test.py` пускает лишь имена `eduson*_test` — защита и `eduson`, и БД KOTOKOD). Тестовые данные — выдуманные (`apps/core/tests/minimal_school.py`), не ПДн Eduson.

⚠️ Гонять **полный** `pytest -q`, а не по приложениям: часть приложений no-op'ит `django_db_setup` (общая `eduson_test`), часть пересоздаёт `test_eduson_test`. Прогон по частям даёт ложный результат.

Фронт-тестов нет: проверка — `npx tsc --noEmit` и `npm run build` в обоих SPA + скриншоты через локальный nginx `127.0.0.1:8180` (хост именно `127.0.0.1`, не `localhost`).

## Производительность (учитывать при любой правке)

Ориентир — те же требования, что у KOTOKOD (VPS 4 CPU / 6 ГБ). Не читать «всё» там, где нужна часть. Индексы под реальные предикаты (PG не индексирует FK). Пагинация везде.

Каждую новую добавляемую или изменяемую фичу проверяй на производительность и способы оптимизации. В проекте уже используется Celery и Redis если что.


## Инфраструктура

**Локально** (Windows): runserver `127.0.0.1:8100` (`PYTHONUTF8=1 ../.venv/Scripts/python manage.py runserver 127.0.0.1:8100` из `journal_django/`), nginx Eduson `127.0.0.1:8180` → runserver, собранные SPA и CSP как на проде (`deploy/nginx/local/`, запуск — `start-local-nginx.ps1`; `.ps1` с кириллицей — UTF-8 BOM). Без nginx страницы SPA отдают 404.

**Прод новой платформы — нет.** Выкладка — этап 4, только с согласия владельца (`docs/eduson/stage4-deploy-backlog.md`). Ansible, бэкапы и юниты KOTOKOD в форк не перенесены и к Eduson не относятся.

**Старая система Eduson** (Express + node:sqlite в Docker) работает на сервере владельца. **Только чтение**; адрес и доступы в репозиторий не записываются.

## Backlog

Soft-delete payments и прочее — `docs/BACKLOG.md`.
