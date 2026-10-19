# Architecture v2 — план реализации: auth на JWT (simplejwt, HttpOnly-cookie) + замена самописа на встроенное Django/DRF

> Ревизия `architecture_v1.md` после независимого аудита (каждое утверждение v1 проверено по коду).
> Документ — задание на разработку. Заходы 2+ — раздел «Отложено».

## Главный архитектурный принцип

**Django → DRF → официальные пакеты экосистемы → свой код.**

Перед добавлением любого нового кода задавать вопрос: «есть ли готовое надёжное решение в Django/DRF,
которое уже это делает?». Если есть — использовать его. Самопис писать только когда:
встроенного решения нет; встроенное не закрывает бизнес-требование; есть доказанная необходимость в кастомизации.

## Context

Бэкенд `journal_django/` (Django 5.1 + DRF 3.15) содержит самописные механизмы, дублирующие штатные
средства, и полу-мигрированный нерабочий слой аутентификации:

- Auth несогласован: `apps/auth_app/views.py` зовёт `django.contrib.auth.login()` (стр. 56, 106, 170),
  но `django.contrib.sessions` исключён из `INSTALLED_APPS`. `apps/auth_app/twofa.py:127` импортирует
  **несуществующий** `apps.auth_app.sessions.sign` → email-OTP падает с `ImportError`. Отсутствуют
  `apps/auth_app/sessions.py` и `apps/core/authentication.py`, на которые завязан тестовый харнесс.
- Самопис вместо встроенного: ручной HMAC `_verify_with_secret` (twofa.py:28-66) вместо `django.core.signing`
  (последний уже используется в `auth_app/services.py`); самописный SMTP `mailer.py` на `smtplib` + флаг
  `EMAIL_OTP_CONSOLE` вместо `django.core.mail`; прямой `bcrypt` для OTP/recovery-кодов вместо
  `make_password`/`check_password`.
- settings отклоняются от стандарта: `SECRET_KEY = env('ADMIN_COOKIE_SECRET')` (base.py:42); нет
  `AUTH_PASSWORD_VALIDATORS`; в `REST_FRAMEWORK` указан `TokenAuthentication` без установленного `authtoken`;
  в `MIDDLEWARE` остались `SessionMiddleware`+`AuthenticationMiddleware` при отсутствии сессий.

**Предпосылка, делающая весь переезд валидным:** `Account` — это `AbstractUser`, а
`AUTH_USER_MODEL = 'accounts.Account'` (`apps/accounts/models.py:44`, `config/settings/base.py:147`).
Поэтому `RefreshToken.for_user()`, штатный `get_user()` и проверка `is_active` работают из коробки.

## Зафиксированные решения

1. **JWT на `djangorestframework-simplejwt`** (стандарт из доков DRF), транспорт — **HttpOnly-cookie**.
   Тонкий `CookieJWTAuthentication(JWTAuthentication)` читает токен из cookie; CSRF на мутациях.
2. **`token_version` сохраняем** как ОСНОВНОЙ и единственный механизм отзыва доступа. Через него:
   отзыв всех токенов, принудительный logout, смена пароля, деактивация, смена email, сброс 2FA.
3. **JWT blacklist НЕ используем.** Убрать `rest_framework_simplejwt.token_blacklist`,
   `ROTATE_REFRESH_TOKENS`, `BLACKLIST_AFTER_ROTATION`, `refresh.blacklist()`. Причина: `token_version`
   уже даёт принудительный отзыв всех токенов; blacklist решает задачу отдельных устройств, не нужную проекту;
   лишняя сложность без пользы.
4. **Роли (Groups+Permissions)** — отдельный Заход 2 (использовать встроенное Django, не изобретать своё).
5. Всё строго по официальной документации Django/DRF/simplejwt, без самописа и оверинжиниринга.

**Как работает отзыв без blacklist:** access несёт claim `token_version`. `CookieJWTAuthentication.get_user`
на каждом запросе сверяет claim с `get_auth_state(user.id)`. При `bump_token_version` (смена пароля и т.п.)
версия в БД растёт → все ранее выданные access (и свежевыпущенные из старого refresh — они несут старую
версию) отвергаются. Деактивация рубится мгновенно штатной проверкой `is_active` в `get_user`.

**Что НЕ трогаем** (корректное использование / нет встроенной замены): `token_version` и
`get_auth_state`/`bump_token_version`; слой `repository.py` (`.values()`/dictrow); raw SQL
`register_login_failure` (`apps/accounts/repository.py:222-240` — атомарный `UPDATE … CASE … RETURNING`,
race-free); `result.get('error')` в `apps/payments/views.py` (легитимные бизнес-результаты:
`direction_not_found`/`no_capacity`/`cap_exceeded`); `apps/core/{permissions,exceptions,renderers,pagination}.py`;
`pyotp`+`qrcode`+`Pillow`; raw `student_stats`.

---

## Фаза 1 — JWT-аутентификация core (без blacklist)

**`requirements.txt`:** добавить `djangorestframework-simplejwt`. НЕ добавлять `token_blacklist`.
`bcrypt` — оставить (хеши recovery-кодов могут потребоваться при проверке байт-совместимости; новые хеши — Django).

**`config/settings/base.py`:**
- `INSTALLED_APPS`: оставить как есть (НЕ добавлять `sessions`, НЕ добавлять `token_blacklist`).
  Обновить устаревший комментарий (стр. 55-56).
- `MIDDLEWARE`: убрать `SessionMiddleware` и `AuthenticationMiddleware`; оставить `SecurityMiddleware`,
  `CorsMiddleware`, `CommonMiddleware`, `CsrfViewMiddleware` (CSRF не зависит от сессий — работает на cookie).
- `SECRET_KEY`: читать из `SECRET_KEY`/`DJANGO_SECRET_KEY`, не из `ADMIN_COOKIE_SECRET`.
- Добавить стандартный блок `AUTH_PASSWORD_VALIDATORS` (установка пароля через invite).
- `REST_FRAMEWORK['DEFAULT_AUTHENTICATION_CLASSES'] = ['apps.core.authentication.CookieJWTAuthentication']`
  (убрать `SessionAuthentication` и `TokenAuthentication`).
- Блок `SIMPLE_JWT` по докам: `ACCESS_TOKEN_LIFETIME ≈ timedelta(minutes=15)`,
  `REFRESH_TOKEN_LIFETIME ≈ timedelta(days=7)`, имена cookie (`AUTH_COOKIE='access'`, refresh-cookie),
  HttpOnly, `SameSite='Lax'`. **НЕ** включать `ROTATE_REFRESH_TOKENS`/`BLACKLIST_AFTER_ROTATION`.

**`config/settings/development.py` / `production.py`:**
- prod: добавить `AUTH_COOKIE_SECURE=True`; `CSRF_COOKIE_HTTPONLY=False` (SPA читает CSRF-токен);
  `CSRF_TRUSTED_ORIGINS`. `SESSION_COOKIE_SECURE`/`CSRF_COOKIE_SECURE` уже есть.

**`apps/core/authentication.py` (новый, тонкий, по документированному паттерну simplejwt cookie):**
- `CookieJWTAuthentication(JWTAuthentication)` — override `authenticate()`: брать raw-токен из
  `request.COOKIES[settings.SIMPLE_JWT['AUTH_COOKIE']]`, валидировать штатными
  `get_validated_token`/`get_user`; для небезопасных методов вызывать CSRF-проверку (зеркало
  `rest_framework.authentication.SessionAuthentication.enforce_csrf`).
- Override `get_user()`: после штатной загрузки сверить `validated_token['token_version']` с
  `apps.accounts.repository.get_auth_state(user.id)['token_version']`; mismatch → `AuthenticationFailed`.
  Это вся «бизнес-логика» класса — больше внутри ничего.
- Хелперы `set_auth_cookies(response, refresh)` / `delete_auth_cookies(response)` (HttpOnly, Secure в prod,
  SameSite=Lax).
- (Точную сигнатуру методов сверить с актуальной докой simplejwt при реализации — поиск разрешён.)

**Выдача токена с claim `token_version`:**
- `refresh = RefreshToken.for_user(user)`; затем `refresh['token_version'] = user.token_version`
  (claim попадёт и в access). Кастомные claims — штатный механизм simplejwt.

**`apps/auth_app/views.py`:**
- Везде, где раньше `auth_login(request, user)` (LoginView, Login2faView, TwofaEnableView), вместо этого:
  `refresh = RefreshToken.for_user(user)` (+ claim) → `set_auth_cookies(response, refresh)`.
  Тело ответа без изменений (`{role, redirect}`).
- `LogoutView`: **только** `delete_auth_cookies(response)` (без blacklist).
- Новый `/api/auth/refresh` — подкласс `TokenRefreshView`, читает refresh-cookie, ставит новый access-cookie.
  Новый access несёт `token_version` из refresh → при бампе версии `get_user` его отвергнет.
- `MeView` — работает через `CookieJWTAuthentication`, изменений минимум.

**`token_version`:** поле (`apps/accounts/models.py:92`), `bump_token_version`/`get_auth_state`
(`apps/accounts/repository.py:206-213`) и все вызовы бампа (`auth_app/services.py:335,363`;
`accounts/services.py:102,125,137,223`; `accounts/repository.py:313`) — **сохраняются**. Drop-миграции НЕТ.

---

## Фаза 2 — Встроенные механизмы Django (замена самописа)

- **Email:** `apps/auth_app/mailer.py` — заменить `smtplib` на `django.core.mail.send_mail` (тонкий
  `send_otp_email`, тема/тело сохранить). Транспорт и креды — из `EMAIL_BACKEND`/`EMAIL_*` settings
  (маппинг из существующих `SMTP_*`). Удалить флаг `EMAIL_OTP_CONSOLE` и его ветку; dev →
  `django.core.mail.backends.console.EmailBackend`, prod → SMTP backend.
- **Подпись:** `apps/auth_app/twofa.py` — удалить `_verify_with_secret` и мёртвый
  `from apps.auth_app.sessions import sign` (стр. 127). email-OTP challenge перевести на `django.core.signing`
  (тот же `Signer`/`TimestampSigner`, что уже в `auth_app/services.py` для login-challenge).
- **Хеширование:** OTP-кодов и recovery-кодов — `make_password`/`check_password` вместо прямого `bcrypt`
  (`generate_email_code`/`issue_email_challenge`/`verify_email_challenge`,
  `generate_recovery_codes`/`verify_recovery`).

---

## Фаза 3 — Инфраструктура (test-БД + same-origin)

- **Отдельная test-БД:** текущий харнесс гоняет тесты на **реальной БД** (`django_db_setup: pass` +
  raw DELETE в teardown) — известная ловушка «pytest стирает dev-БД». Создать throwaway test-БД (клон схемы)
  и запускать pytest с `DATABASE_URL` на неё. Паттерн shared-DB/`managed=False` оставляем; полная изоляция —
  Заход 2.
- **Same-origin:** `SameSite='Lax'`-кука **не уходит на кросс-ориджин XHR/fetch** → SPA получит 401.
  Зафиксировать единый origin SPA↔API: nginx (:8080 → runserver) или dev-proxy Vite (`/api` → бэкенд).
  `CSRF_TRUSTED_ORIGINS` — этот origin. (v1 ставил `SameSite=Lax` + прямой кросс-ориджин Vite:5173 — это
  взаимоисключающе.)

---

## Фаза 4 — Тесты (минимально, до зелёного)

Сейчас аутентификация в тестах — дублированный самописный `_make_cookie` (HMAC) в 15 `apps/*/tests/conftest.py`.
- Единая JWT-фикстура в корневом `conftest.py`: реальный JWT (`RefreshToken.for_user(account)` с claim
  `token_version`) в auth-cookie `APIClient`; для мутаций — проставлять CSRF (или `enforce_csrf_checks=False`
  в тест-клиенте, по доке). Точечный mock `get_auth_state` для sentinel-аккаунта убрать — токен несёт версию
  реального аккаунта.
- Удалить локальные `_make_cookie`/`TEST_SECRET`/`_future_ms`/`_past_ms` из 15 conftest и заменить
  использование в `test_*_api.py`.
- **Переписать (не удалять):**
  - `apps/auth_app/tests/test_token_version.py` → под JWT (инварианты валидны: stale→401, inactive→401,
    soft_delete бампит, challenge-токен ≠ session). Поправить `active` → `is_active` (AbstractUser).
  - `apps/core/tests/test_authentication.py` → под `CookieJWTAuthentication`.
- **Удалить:** `apps/auth_app/tests/test_sessions.py` (round-trip Node↔Python HMAC `sign` — устарел вместе
  с HMAC-cookie).
- `apps/teacher_spa/tests/test_teacher_spa_api.py:140-150` — сохранить смысл (401 для несуществующего id).

---

## Отложено (Заход 2+)
- **Роли → встроенные Django Groups + Permissions** (seed-миграция групп teacher/manager/admin;
  per-model + кастомные `Meta.permissions`; DRF `DjangoModelPermissions`/`has_perm`; пересмотр
  `apps/core/permissions.py`). Не изобретать своё.
- **Сервисы → DRF-исключения:** `apps/teacher_spa/services.py` (45, 60, 87) `{'_error','_status'}` →
  `raise PermissionDenied`; убрать `if '_error' in result` в `teacher_spa/views.py` (154, 169, 187).
  (`apps/students/services.py` этого паттерна НЕ содержит — не трогать.)
- Массовая чистка conftest и глубокая изоляция тест-БД; `repository.py` → менеджеры/QuerySet;
  DRY пагинации/фильтрации (`django-filter`); выходная сериализация (`.values()`/dictrow) → DRF-сериализаторы.

---

## Критичные файлы
- `requirements.txt`; `config/settings/{base,development,production}.py`.
- `apps/core/authentication.py` (новый: тонкий `CookieJWTAuthentication` + проверка `token_version` +
  cookie-хелперы).
- `apps/auth_app/views.py` (выдача JWT-cookie, refresh-эндпоинт, `delete_auth_cookies`);
  `apps/auth_app/twofa.py` (signing + Django hashers, убрать битый импорт); `apps/auth_app/mailer.py`
  (`django.core.mail`).
- `apps/accounts/{models.py,repository.py}` — `token_version`/`get_auth_state`/`bump_token_version` **сохранить**.
- Корневой `conftest.py` + 15 `apps/*/tests/conftest.py` и `test_*_api.py` (JWT-фикстура вместо `_make_cookie`).
- Переписать: `apps/auth_app/tests/test_token_version.py`, `apps/core/tests/test_authentication.py`.
- Удалить: `apps/auth_app/tests/test_sessions.py`.

---

## Верификация (пошагово после каждой фазы; git нет)
1. `python manage.py check` и `migrate` — применяется чисто (token_blacklist НЕ ставится; drop
   `token_version` НЕ выполняется).
2. **pytest только на выделенной test-БД, НЕ на dev-данных.** Цель — зелёная база после переписанного
   харнесса; явно проверить token_version-инварианты (stale→401, inactive→401) на `CookieJWTAuthentication`.
3. `runserver` + same-origin (nginx :8080), ручной auth-флоу:
   - `POST /api/auth/login` (admin/teacher) → challenge; `POST /api/auth/login/2fa` (**TOTP и email-OTP** —
     убедиться, что email-OTP больше не падает `ImportError` и письмо уходит через `django.core.mail`);
   - после успеха выставляются HttpOnly access/refresh cookie; `GET /api/auth/me` → 200; защищённый
     `GET /api/admin/teachers` с cookie → 200, без cookie → 401;
   - `POST` без CSRF-токена → 403, с токеном → проходит;
   - `/api/auth/refresh` обновляет access-cookie;
   - смена пароля админом (`bump_token_version`) → старый и свежеобновлённый access умирают → 401;
   - `/api/auth/logout` чистит cookie → повторный запрос 401.

## Риски
- **token_version (+1 SELECT/запрос)** — осознанная stateful-проверка в `get_user`; единственный механизм
  отзыва (blacklist убран намеренно). Нагрузка проекта (50–100 учителей, 10–15 admin) делает стоимость
  незначимой; по сути это то же, что делает session-auth.
- **CSRF + SameSite:** мутации требуют `X-CSRFToken`; фронт (admin SPA, teacher SPA) обязан слать заголовок,
  доступ — same-origin.
- **Объём Фазы 4:** 15 conftest + переписать 2 теста; делать поштучно с прогоном pytest на test-БД.
- **Смена источника `SECRET_KEY`:** на хеши паролей не влияет (они от него не зависят); signing-challenge
  переживает (тот же ключ-движок).
