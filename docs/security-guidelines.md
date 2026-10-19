# Правила безопасности (обязательны для любой фичи и правки)

Свод механизмов безопасности платформы KOTOKOD и правил, которые **обязательно**
соблюдать при добавлении новых фич и исправлений. Нарушение любого пункта —
блокер для мержа.

Принципы: **используем встроенные механизмы Django/DRF** (не изобретаем свою
крипту/аутентификацию/сессии), **least privilege**, **defense-in-depth**,
**fail-closed** для доступа (по умолчанию закрыто).

Профильные документы: `docs/auth.md`, `docs/csp-explained.md`,
`docs/security-csp-plan.md`, `docs/compliance-152fz-checklist.md`,
`journal_django/docs/architecture_v2.md`.

---

## 1. Аутентификация — JWT в HttpOnly-cookie

- Единственный auth-backend — `apps/core/authentication.CookieJWTAuthentication`
  (глобальный `DEFAULT_AUTHENTICATION_CLASSES`). **Не изобретать свою
  аутентификацию/сессии.**
- Токен живёт **только** в HttpOnly-cookie (`access`/`refresh`). **Никогда** не
  читать/писать токен из JS, не переходить на схему `Authorization: Bearer` из
  фронта, не класть токен в `localStorage`.
- Выдавать токены только через `issue_tokens_for()` + `set_auth_cookies()`,
  удалять через `delete_auth_cookies()`. Cookie руками не ставить.
- **`token_version` — единственный механизм мгновенного отзыва** всех токенов.
  Любое действие, которое должно разлогинить пользователя (смена/сброс пароля,
  сброс 2FA, компрометация, отзыв доступа), **обязано инкрементить**
  `token_version` аккаунта. Каждый access-токен несёт claim `token_version`,
  который сверяется с БД на каждом запросе.
- Cookie-параметры: `HttpOnly=True`, `SameSite=Lax`, `Secure=True` в проде
  (`production.py`). **Не ослаблять.**

## 2. Авторизация / RBAC

- DRF default permission = **`AllowAny`** (только для health-check). Поэтому
  **КАЖДАЯ новая вьюха ОБЯЗАНА явно задать `permission_classes`.** Забыл →
  эндпоинт открыт всему интернету.
- Готовые классы (`apps/core/permissions.py`):
  `IsTeacher`, `IsManager`, `IsAdmin`, `IsManagerOrAdmin`.
  - Данные админ-панели — минимум `IsManagerOrAdmin`.
  - Строго административное (аккаунты, аудит) — `IsAdmin`.
  - Данные преподавателя — `IsTeacher` (+ фильтрация по своему `teacher_id`).
- **Никогда не полагаться только на фронтенд.** Скрытие пунктов меню и
  клиентские guard'ы — это UX и defense-in-depth. Реальная граница доступа —
  **всегда на API**. Данные, к которым нет права, не должны даже попадать в ответ.
- Клиентские guard'ы (`AuthGate` и т.п.) обязаны проверять **роль**, а не только
  факт аутентификации, и уводить чужую роль в её раздел.

## 3. CSRF

- Мутирующие методы (`POST/PUT/PATCH/DELETE`) под cookie-auth проходят
  CSRF-проверку внутри `CookieJWTAuthentication` (зеркало
  `SessionAuthentication.enforce_csrf`). SPA обязана слать заголовок
  `X-CSRFToken` из cookie `csrftoken` (её ставит `GET /api/auth/csrf`).
- **Не** помечать вьюхи `@csrf_exempt`, **не** отключать `CsrfViewMiddleware`.
  `CSRF_COOKIE_HTTPONLY=False` намеренно (JS читает токен), `SameSite=Lax`,
  `Secure` в проде.

## 4. Двухфакторная аутентификация (2FA)

- 2FA **обязательна для ВСЕХ ролей** (`services.requires_2fa → True`). Обходов не
  добавлять. Способ выбирается явно при enrollment: TOTP (`pyotp` + QR) или e-mail-OTP.
- Промежуточные login-challenge токены — только через `django.core.signing`
  (`TimestampSigner`) с ограниченным TTL. Не выдавать долгоживущих «полутокенов».

## 5. Секреты и конфигурация

- Секреты — **только из окружения** через `django-environ`. Обязательные:
  `SECRET_KEY` (в проде отдельный, 128+ энтропии), `ADMIN_COOKIE_SECRET` (128-hex),
  `DATABASE_URL`, `SMTP_*`.
- **Никогда** не хардкодить секреты в коде, конфигах, фронте, тестах.
- **Никогда** не коммитить секреты. Корневой `.gitignore` закрывает `.env`,
  `service-account-key.json`, `backups/` (дампы БД = ПДн), `logs/`. Перед коммитом —
  проверять `git status`, что ничего чувствительного не попало в индекс.

## 6. CSP — никаких inline-скриптов

Политика (nginx, dev+prod): `script-src 'self'` — **без `'unsafe-inline'` и без
nonce**. Сейчас в режиме `Report-Only`, флип на боевой `Content-Security-Policy`
запланирован (`docs/security-csp-plan.md`) — код обязан быть готов уже сейчас.

- **Запрещено:** inline `<script>…</script>`, inline-обработчики (`onclick=`,
  `onload=` и т.п.), `javascript:`-URL, `eval` / `new Function`.
- **Любой JS — внешним файлом** с same-origin. Даже ранние скрипты (например
  применение темы до отрисовки) — внешним файлом, `render-blocking` в `<head>`.
- `style-src` допускает `'unsafe-inline'` (компромисс для inline-стилей
  React/teacher) — но новые стили предпочитать в CSS/токенах.
- `img-src 'self' data:` (QR-код как `data:` — ок). `connect-src 'self'` — фронт
  ходит **только на свой origin** (same-origin через nginx). Добавление любого
  внешнего origin (CDN, шрифты, API, аналитика) требует явного обновления CSP —
  по умолчанию всё внешнее заблокировано. Шрифты — self-hosted (`/fonts`, `/admin/fonts`).

## 7. Заголовки безопасности (nginx)

Отдаёт nginx на server-уровне: `Strict-Transport-Security` (HSTS),
`X-Content-Type-Options: nosniff`, `X-Frame-Options: SAMEORIGIN`,
`Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`/`-Resource-Policy: same-origin`,
`Permissions-Policy` (отключены неиспользуемые API), `server_tokens off`.

- ⚠️ **Правило nginx:** не добавлять `add_header` внутри `location` — иначе все
  server-уровневые заголовки в этом `location` молча исчезают. Новый заголовок —
  только на server-уровне.
- В проде: `SECURE_SSL_REDIRECT`, TLS 1.2/1.3, `client_max_body_size` ограничен.

## 8. Rate-limiting

Два слоя, оба обязательны для auth-чувствительных эндпоинтов:

- **Django** (`django_ratelimit`, `key='ip'`, `block=True`) на вьюхах: логин
  `5/15m`, 2FA/OTP/email `3/h`–`10/15m`.
- **nginx** `limit_req` зоны: `api_login` (20r/m), `api_general` (300r/m),
  `api_csp`.
- Любой новый эндпоинт входа/OTP/сброса/инвайта **обязан** получить rate-limit на
  обоих слоях.

## 9. CORS

- `django-cors-headers`, `CORS_ALLOW_CREDENTIALS=True`, whitelist из окружения
  (`CORS_ORIGINS`). **Никогда** не ставить allow-all (`*`) вместе с credentials.
- Основной режим — same-origin через nginx; CORS нужен точечно. (Backlog: сузить
  whitelist до боевых доменов.)

## 10. SQL и инъекции

- **Только параметризованные запросы**: `cursor.execute(sql, [params])` с
  плейсхолдерами `%s`. **Никогда** не подставлять значения в SQL строкой
  (f-string, `%`, `.format`, конкатенация).
- Динамические идентификаторы (колонка сортировки, направление) — **только через
  whitelist**, не из сырого ввода. Паттерн sort-dir:
  `(val==='asc'||val==='desc') ? val : default` — и чинить в обоих местах.

## 11. Валидация входных данных и инварианты денег

- Весь внешний ввод — через **DRF-сериализаторы** (типы, `choices`, длины,
  обязательность). Не доверять клиенту ни в чём.
- Пагинация — встроенная (`StandardPagination`); лимиты/сортировка —
  whitelisted значения.
- `payments` **immutable**: только `POST`/`DELETE`, никакого `PATCH`. Суммы
  (`total_amount = unit_price × subscriptions_count`) пересчитывать на сервере +
  `CHECK` в БД. Никогда не доверять сумме с клиента.

## 12. Аудит

- Чувствительные действия (вход, сброс пароля/2FA, инвайт/отзыв, изменения
  аккаунтов и ролей) логировать через `apps.audit.services.log_event(...)`.
- **Не класть секреты/пароли/токены в `meta`** — `sanitize_meta` вырезает
  известные ключи, но полагаться на это нельзя. Аудит не должен ронять основной
  запрос (ошибки записи глушатся).

## 13. Служебные эндпоинты Telegram-бота

`/api/integrations/telegram/*` — единственные эндпоинты, которые вызывает не
браузер, а сторонний процесс (Telegram-бот школы на той же машине). Защита слоями,
и снимать ни один из них нельзя:

- **Общий секрет** `X-Bot-Token`, сравнение только через
  `django.utils.crypto.constant_time_compare` — обычное `==` утекает по времени.
- **Пустой `BOT_SERVICE_TOKEN` в настройках означает «запрещено всем»**, а не
  «пускать всех». Это отдельно закреплено тестом.
- `permission_classes = [IsBotService]` задан явно на каждой вьюхе.
- Аутентификация не сессионная ⇒ CSRF не применяется; `@csrf_exempt` не нужен и
  не ставится.
- Rate-limit (`ScopedRateThrottle`, scope `bot_service`) + `limit_req` в nginx.
- В nginx `location /api/integrations/` закрыт `allow 127.0.0.1; deny all` — бот
  и журнал на одной машине, наружу этому торчать незачем.

`TELEGRAM_BOT_TOKEN` живёт в двух `.env` (журнала и бота) на одной машине —
осознанная цена того, что рассылку ведёт журнал напрямую.

## 14. Персональные данные (152-ФЗ)

- Данные учеников и родителей — **ПДн**. Не логировать ПДн в общие логи, не
  выгружать в git (дампы БД — в `backups/`, он в `.gitignore`), соблюдать
  consent-поля. Скриншоты с ПДн не коммитить. См.
  `docs/compliance-152fz-checklist.md`.

---

## Чеклист перед добавлением фичи / правки

- [ ] У каждой новой вьюхи задан `permission_classes` (не остался `AllowAny`).
- [ ] Роль/владение проверяются на сервере, а не только на фронте.
- [ ] Мутирующие методы совместимы с CSRF (SPA шлёт `X-CSRFToken`).
- [ ] Если действие должно разлогинить — инкрементнут `token_version`.
- [ ] Никакого нового inline-`<script>`, `onclick=`, `eval`; JS — внешним файлом.
- [ ] Не добавлен внешний origin без обновления CSP (`connect/img/font/script-src`).
- [ ] Новый auth/OTP/reset-эндпоинт имеет rate-limit (Django + nginx).
- [ ] SQL параметризован; идентификаторы — из whitelist.
- [ ] Ввод валидируется сериализатором; суммы денег пересчитаны на сервере.
- [ ] Чувствительное действие пишется в аудит; секретов в `meta` нет.
- [ ] Секреты только из окружения; `git status` чист от `.env`/ключей/дампов/ПДн.
- [ ] Cookie/заголовки безопасности не ослаблены; `add_header` не внутри `location`.
