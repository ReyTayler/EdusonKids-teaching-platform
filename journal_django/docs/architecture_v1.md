# Architecture v1 — план рефакторинга: auth на JWT (simplejwt, HttpOnly-cookie) + замена самописа на встроенное Django/DRF

> Документ для аудита/review. Описывает целевую архитектуру и пошаговый план Захода 1.
> Заходы 2+ — раздел «Отложено».

## Context

Бэкенд `journal_django/` (Django 5.1 + DRF) содержит ряд **самописных механизмов, дублирующих штатные средства
Django/DRF**, и полу-мигрированный, нерабочий слой аутентификации:

- Auth несогласован: `apps/auth_app/views.py` зовёт `django.contrib.auth.login()`, но `django.contrib.sessions`
  исключён из `INSTALLED_APPS`; `apps/auth_app/twofa.py:127` импортирует **несуществующий** `apps.auth_app.sessions.sign`
  (email-OTP падает с `ImportError`); отсутствуют `apps/auth_app/sessions.py` и `apps/core/authentication.py`, на которые
  завязан **весь тестовый харнесс** (`_make_cookie()` — самописный HMAC-cookie — продублирован в ~17 `tests/conftest.py`).
  Защищённые `/api/admin/*` сейчас аутентифицировать некому → прод-auth нефункционален, тесты красные.
- Самопис вместо встроенного: ручной HMAC `_verify_with_secret` (вместо `django.core.signing`); самописный SMTP-клиент
  `apps/auth_app/mailer.py` на `smtplib` + флаг `EMAIL_OTP_CONSOLE` (вместо `django.core.mail` + `EMAIL_BACKEND`);
  прямой `bcrypt` для OTP/recovery-кодов (вместо `make_password`/`check_password`); поле `token_version` —
  ручная инвалидация сессий.
- settings отклоняются от стандарта: `SECRET_KEY` переиспользует `ADMIN_COOKIE_SECRET`; нет `AUTH_PASSWORD_VALIDATORS`;
  устаревшие комментарии; в `REST_FRAMEWORK` указан `TokenAuthentication` без установленного `authtoken`.

**Решения (зафиксированы):**
1. **Сессии убираем, ставим JWT** на `djangorestframework-simplejwt` (стандарт из доков DRF), транспорт — **HttpOnly-cookie**
   (тонкий `CookieJWTAuthentication` — подкласс штатного `JWTAuthentication`, читает токен из cookie; CSRF на мутации).
2. **Роли** (переход на встроенные Django **Groups + Permissions**) — **отдельный Заход 2**. Сейчас не трогаем.
3. Всё строго по официальной документации Django/DRF/simplejwt, без самописа; без оверинжиниринга.

**Принципы для всего захода:** для каждого самописного куска сначала ищем готовое в Django/DRF и берём его; settings
приводим к стандартному виду; код стандартизируем по Django-конвенциям, но без переусложнения.

**Что НЕ меняем** (это корректное использование и/или нет встроенной замены): `apps/core/permissions.py` (на этот заход —
роли в Заходе 2), `apps/core/exceptions.py`, `apps/core/renderers.py` (`DateSafeJSONRenderer` нужен для байт-совместимости
numeric/date), `apps/core/pagination.py`; `pyotp`+`qrcode`+`Pillow` (TOTP/QR — встроенной замены в Django нет); raw SQL
`student_stats` (обоснован CTE+MSK).

---

## Phase 0 — Зависимости и стандартизация settings

**requirements.txt:**
- Добавить `djangorestframework-simplejwt`.
- Удалить `bcrypt` (хеширование уходит на Django-хешеры).
- Оставить `pyotp`, `qrcode`, `Pillow`, `psycopg2-binary`, `django-cors-headers`, `django-environ`, `gunicorn`.

**config/settings/base.py:**
- `INSTALLED_APPS`: добавить `'rest_framework_simplejwt.token_blacklist'`. НЕ добавлять `django.contrib.sessions`.
  Обновить устаревший комментарий (строки 55-56).
- `MIDDLEWARE`: убрать `SessionMiddleware` и `AuthenticationMiddleware` (нет сессий и Django-admin; для чистого DRF-API
  `request.user` ставит authentication class). Оставить `SecurityMiddleware`, `CorsMiddleware`, `CommonMiddleware`,
  `CsrfViewMiddleware` (CSRF нужен для cookie-JWT мутаций).
- `SECRET_KEY`: читать из стандартного env (`SECRET_KEY`/`DJANGO_SECRET_KEY`), а не из `ADMIN_COOKIE_SECRET`.
- Добавить стандартный блок `AUTH_PASSWORD_VALIDATORS` (для установки пароля через invite).
- **Email** — заменить самопис на встроенное: `EMAIL_BACKEND`, `EMAIL_HOST/PORT/HOST_USER/HOST_PASSWORD/USE_SSL/USE_TLS`,
  `DEFAULT_FROM_EMAIL`, маппинг из существующих `SMTP_*` env. Убрать `EMAIL_OTP_CONSOLE`.
- `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = ['apps.core.authentication.CookieJWTAuthentication']`
  (убрать `SessionAuthentication` и `TokenAuthentication`).
- Добавить блок `SIMPLE_JWT` по докам: `ACCESS_TOKEN_LIFETIME`, `REFRESH_TOKEN_LIFETIME`, `ROTATE_REFRESH_TOKENS=True`,
  `BLACKLIST_AFTER_ROTATION=True`, имена cookie (`AUTH_COOKIE='access'`, refresh-cookie), `HttpOnly`, `SameSite='Lax'`.
- `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` в `production.py` уже есть; добавить `AUTH_COOKIE_SECURE` (prod) /
  `CSRF_COOKIE_HTTPONLY=False` (чтобы SPA читал CSRF-токен) + `CSRF_TRUSTED_ORIGINS` (dev: `http://localhost:5173`).

**config/settings/development.py / production.py:**
- dev: `EMAIL_BACKEND='django.core.mail.backends.console.EmailBackend'` (заменяет `EMAIL_OTP_CONSOLE`-print).
- prod: SMTP backend; cookie-флаги Secure.

---

## Phase 1 — JWT-аутентификация (core)

**apps/core/authentication.py (новый, по документированному паттерну simplejwt cookie):**
- `CookieJWTAuthentication(JWTAuthentication)` — override `authenticate()`: брать raw-токен из
  `request.COOKIES[settings.SIMPLE_JWT['AUTH_COOKIE']]`, валидировать штатными `get_validated_token`/`get_user`;
  для небезопасных методов вызывать CSRF-проверку (зеркало `rest_framework.authentication.SessionAuthentication.enforce_csrf`).
- Хелперы `set_auth_cookies(response, refresh)` / `delete_auth_cookies(response)` (HttpOnly, Secure в prod, SameSite=Lax).
- (Точную сигнатуру методов сверить с актуальной докой simplejwt при реализации — поиск в интернете разрешён.)

**apps/auth_app/views.py:**
- Везде, где раньше `auth_login(request, user)` (Login2faView, TwofaEnableView, и login без 2FA), вместо этого:
  `refresh = RefreshToken.for_user(user)` → `set_auth_cookies(response, refresh)`. Тело ответа без изменений (`{role, redirect}`).
- `LogoutView`: blacklist refresh-токена (`token.blacklist()`) + `delete_auth_cookies`.
- Новый `TokenRefreshView`-подкласс на `/api/auth/refresh`, читает refresh-cookie, ставит новый access-cookie.
- `MeView` — работает через `CookieJWTAuthentication`, изменений минимум.

**Инвалидация вместо `token_version`:**
- Удалить поле `token_version` (`apps/accounts/models.py:92`) + миграция drop column; функции
  `bump_token_version`/`get_auth_state` (`apps/accounts/repository.py:206-213`) и все вызовы
  (`auth_app/services.py:335,363`; `accounts/services.py:102,125,137,223`; `accounts/repository.py:313`).
- Logout/смена пароля → blacklist outstanding-токенов пользователя (встроенный `token_blacklist` app, по докам simplejwt).
  Деактивация аккаунта — `is_active=False` (simplejwt `get_user` отвергает неактивных).

---

## Phase 2 — Email через django.core.mail (замена самописа)

- `apps/auth_app/mailer.py`: заменить `smtplib`-реализацию на `django.core.mail.send_mail` (тонкий `send_otp_email`,
  тема/тело сохраняются). Транспорт (SMTP/console) и креды — целиком из `EMAIL_BACKEND`/`EMAIL_*` settings.
- Удалить флаг `EMAIL_OTP_CONSOLE` и связанную ветку.

---

## Phase 3 — Убрать ручной HMAC и прямой bcrypt (замена на встроенное)

- `apps/auth_app/twofa.py`: удалить `_verify_with_secret` и мёртвый `from apps.auth_app.sessions import sign`.
  email-OTP challenge перевести на `django.core.signing` (тот же `Signer`/`TimestampSigner`, что уже в
  `auth_app/services.py` для login-challenge).
- Хеширование OTP-кодов и recovery-кодов: `make_password`/`check_password` вместо прямого `bcrypt`
  (`generate_email_code`/`issue_email_challenge`/`verify_email_challenge`, `generate_recovery_codes`/`verify_recovery`).
- Удалить тесты-сироты на несуществующую архитектуру: `apps/core/tests/test_authentication.py`,
  `apps/auth_app/tests/test_sessions.py`, `apps/auth_app/tests/test_token_version.py`, плюс проверки
  `HmacSessionAuthentication`/`token_version` в `apps/teacher_spa/tests/test_teacher_spa_api.py:140-150`.

---

## Phase 4 — Тестовый харнесс на JWT (самая объёмная часть)

Сейчас аутентификация в тестах — дублированный самописный `_make_cookie()` (HMAC) в ~17 `apps/*/tests/conftest.py`.
- Единая фикстура в корневом `conftest.py`: выдаёт реальный JWT и кладёт его в auth-cookie `APIClient`
  (`RefreshToken.for_user(account)` → `client.cookies[AUTH_COOKIE]=str(access)`); для мутаций — проставлять CSRF
  (или `enforce_csrf_checks=False` в тест-клиенте, по доке).
- Удалить локальные `_make_cookie`/`TEST_SECRET`/`_future_ms`/`_past_ms` из всех app-conftest и заменить использование
  в `test_*_api.py`. Представительные пути: `apps/teachers/tests/conftest.py`, `apps/students/tests/conftest.py`,
  `apps/groups/tests/conftest.py`, `apps/payments/tests/conftest.py`, `apps/lessons/tests/conftest.py`,
  `apps/teacher_spa/tests/conftest.py`, `apps/tokens/tests/conftest.py`, … (паттерн одинаков).

---

## Phase 5 — Быстрые победы + аудит структуры

- **Сервисы → DRF-исключения:** `apps/teacher_spa/services.py`, `apps/students/services.py` — заменить возврат
  `{'_error','_status'}` на `raise` (`PermissionDenied`/`NotFound`/`ValidationError`), которые уже нормализует
  `apps/core/exceptions.py`. Во вью убрать `if '_error' in result` (`teacher_spa/views.py:147-192`, `payments/views.py:53-82`).
- **Опц.:** `apps/accounts/repository.py:227-240` `register_login_failure` raw SQL → ORM `update(Case(When(...)))`.
- **Аудит структуры (отчёт + только безопасные правки):** структура `config/settings/{base,development,production}` и
  пакеты `apps/*` — соответствуют Django-конвенциям, оставляем. Отметить как кандидатов на будущее (НЕ в этом заходе,
  чтобы не оверинжинирить): слой `repository.py` с ручными `.values()`/`dictrow` — идиоматичнее как менеджеры/QuerySet
  модели; убедиться, что чисто-логический пакет `apps/finances` (без моделей) не требует регистрации в INSTALLED_APPS.

---

## Отложено (Заход 2+)
- **Роли → встроенные Django Groups + Permissions** (seed-миграция групп teacher/manager/admin; per-model + кастомные
  `Meta.permissions`; DRF `DjangoModelPermissions`/`has_perm`; пересмотр `apps/core/permissions.py`). Самый крупный блок.
- DRY пагинации/фильтрации (`django-filter` + готовые `StandardPagination`/`WhitelistOrderingFilter`).
- Выходная сериализация (`.values()`/`dictrow`) → DRF-сериализаторы.

---

## Критичные файлы
- `requirements.txt`; `config/settings/{base,development,production}.py`.
- `apps/core/authentication.py` (новый, `CookieJWTAuthentication` + cookie-хелперы).
- `apps/auth_app/views.py` (выдача/blacklist JWT-cookie, refresh-эндпоинт); `apps/auth_app/twofa.py` (signing+хешеры);
  `apps/auth_app/mailer.py` (`django.core.mail`).
- `apps/accounts/models.py` + миграция (drop `token_version`); `apps/accounts/repository.py`, `apps/accounts/services.py`,
  `apps/auth_app/services.py` (убрать `token_version`).
- `apps/teacher_spa/services.py`, `apps/students/services.py` + их `views.py` (исключения).
- Корневой `conftest.py` + все `apps/*/tests/conftest.py` и `test_*_api.py` (JWT-фикстура вместо `_make_cookie`).
- Удалить: `apps/core/tests/test_authentication.py`, `apps/auth_app/tests/test_sessions.py`,
  `apps/auth_app/tests/test_token_version.py`.

---

## Верификация (пошагово после каждой фазы; git нет)
1. `python manage.py check` и `migrate` (включая `token_blacklist` и drop `token_version`) — применяется чисто.
2. **pytest только на выделенной тест-БД, НЕ на dev-данных** (полный прогон делает flush реальной БД — известная ловушка
   проекта). Цель — зелёная база после переписанного харнесса.
3. `runserver` + nginx (:8080), ручной auth-флоу:
   - `POST /api/auth/login` (admin/teacher) → challenge; `POST /api/auth/login/2fa` (TOTP **и** email-OTP — убедиться, что
     email-OTP больше не падает с ImportError и письмо уходит через `django.core.mail`);
   - после успеха выставляются HttpOnly access/refresh cookie; `GET /api/auth/me` → 200; защищённый
     `GET /api/admin/teachers` с cookie → 200, без cookie → 401;
   - `POST` без CSRF-токена → 403, с токеном → проходит;
   - `/api/auth/refresh` обновляет access-cookie; `/api/auth/logout` blacklist'ит refresh и чистит cookie → повторный
     запрос 401.

## Риски
- **Cookie-JWT — расширение поверх simplejwt:** `CookieJWTAuthentication` и refresh — по документированному паттерну
  (подкласс `JWTAuthentication`, без самописной крипты); сверить точный API simplejwt при реализации.
- **CSRF:** при cookie-JWT мутации требуют `X-CSRFToken` — фронт (admin SPA, teacher SPA) обязан слать заголовок.
- **Объём тестов (Phase 4):** ~17 conftest + связанные `test_*_api.py`; делать поштучно с прогоном pytest на тест-БД.
- **Смена `SECRET_KEY`-источника:** на хеши паролей не влияет (они от него не зависят); сессий/signing-cookie больше нет.
- **Drop `token_version`:** необратимая миграция; вне тестов колонка не читается (подтверждено grep).
