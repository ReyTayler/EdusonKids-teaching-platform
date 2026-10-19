# Провижининг аккаунтов через Invite-ссылку + Обязательная 2FA

**Статус:** Реализовано в журнале KOTOKOD на Django 5.1 + DRF, PostgreSQL.  
**Дата документации:** 2026-06-12

## Обзор

Феча заменяет выдачу временного пароля на механизм, где **сотрудник сам задаёт пароль** через одноразовую invite-ссылку, и **2FA обязательна для всех ролей** (выбор метода: TOTP или email-OTP).

### Бизнес-процесс (happy path)

```
1. Админ создаёт учётку в /api/admin/accounts POST
   ↓ система выписывает invite-ссылку
   ← админ копирует URL, передаёт сотруднику вручную

2. Сотрудник переходит по /login/set-password?token=…
   ↓ GET /api/auth/invite?token=… проверяет валидность
   ← показывает экран установки пароля

3. Сотрудник вводит пароль (≥8 символов)
   ↓ POST /api/auth/invite/accept {token, password}
   ← выдаётся challenge_token стадии 'enroll'

4. Обязательная настройка 2FA (TOTP)
   ↓ POST /api/auth/2fa/setup {challenge_token, method: 'totp'}
   ← QR-код для сканирования

5. Сотрудник вводит код из приложения-аутентификатора
   ↓ POST /api/auth/2fa/enable {challenge_token, code}
   ← сессия-cookie + recovery-коды (один раз)
   ← редирект в кабинет (/teacher или /admin)
```

---

## Архитектура

### Слои и ответственность

**Views** (`apps/auth_app/views.py`, `apps/accounts/views.py`)
- HTTP-слой: валидация входа, rate-limit, cookie-management
- Эндпоинты: `InviteLookupView`, `InviteAcceptView`, `AccountInviteView`/`AccountInviteRevokeView`
- Не содержит бизнес-логику

**Services** (`apps/accounts/services.py`, `apps/auth_app/services.py`)
- Бизнес-логика: создание учётки без пароля, выписка/отзыв invite, логирование
- Функции: `create_account`, `issue_invite`, `invite_lookup`, `invite_accept`
- Вырезают секреты (password_hash, twofa_secret) перед выдачей

**Repository** (`apps/accounts/repository.py`)
- Единственный слой работы с SQL
- CRUD-операции на account_invites: `create_invite`, `find_active_by_hash`, `revoke_active_for_account`, `accept_invite`
- Функции управления сессиями: `bump_token_version`, `get_auth_state`

### Модели данных

**Account.token_version** — `IntegerField(default=0)`
- Версия токена сессии
- Инкрементируется при смене пароля, 2FA, email, деактивации
- Хранится в session-cookie payload
- Сверяется в `HmacSessionAuthentication.authenticate` → смена версии инвалидирует все старые сессии

**Account.password_hash** — `TextField(null=True, blank=True)`
- Теперь nullable: состояние «приглашён» = есть активный invite и password_hash IS NULL
- До установки пароля попытка входа без 2FA → `twofa_enrollment_required`

**AccountInvite** (новая модель)
```
id                  AutoField PK
account_id          FK CASCADE → accounts.id
token_hash          TEXT — SHA-256 hex (plaintext НЕ хранится)
created_by          INT nullable — account_id админа-автора (NULL для bootstrap)
created_at          TIMESTAMPTZ DEFAULT now()
expires_at          TIMESTAMPTZ — created_at + 48 часов
used_at             TIMESTAMPTZ nullable — момент успешной установки пароля
revoked_at          TIMESTAMPTZ nullable — ручной отзыв из админки

Индекс: account_invites(token_hash)
Partial UNIQUE: (account_id) WHERE used_at IS NULL AND revoked_at IS NULL
  → только один активный invite на аккаунт
```

### Миграции

- **0002_account_token_version.py** — добавляет поле token_version с DEFAULT 0
- **0003_account_invites.py** — создаёт таблицу account_invites, делает password_hash nullable
- **0004_invite_created_by_nullable.py** — created_by nullable (для bootstrap)

---

## API-эндпоинты

### Auth-путь (в `/api/auth`, доступны всем)

#### `GET /api/auth/invite?token=…`
**Проверка активности invite-ссылки**

| Параметр | Тип | Описание |
|----------|-----|---------|
| token | query string | Plaintext invite-токен из URL |

**Ответ 200:**
```json
{
  "valid": true,
  "email": "teacher@example.com",
  "role": "teacher"
}
```

Невалидный токен (анти-энумерация — единый ответ):
```json
{"valid": false}
```

**Rate-limit:** 10 попыток / 15 минут (по IP, bucket='invite')

---

#### `POST /api/auth/invite/accept`
**Установка пароля + выдача enroll-challenge**

**Тело запроса:**
```json
{
  "token": "plaintext-токен-из-URL",
  "password": "новый-пароль (≥8 символов)"
}
```

**Ответ 200:**
```json
{
  "challenge_token": "base64url_encoded_challenge_payload"
}
```

**Важно:** НЕ выдаёт session-cookie! Возвращает challenge для 2FA-enrollment. Пользователь переходит к `POST /api/auth/2fa/setup`.

**Ошибки:**
- 400 — невалидный/просроченный/использованный/отозванный токен → `{"error": "Ссылка недействительна"}`
- 400 — слабый пароль → `{"error": "..."}`

**Rate-limit:** 10 попыток / 15 минут (по IP, bucket='invite')

**Атомарность:** SELECT FOR UPDATE на accept_invite → два одновременных accept одного токена → второй получает 400.

---

### Admin-путь (в `/api/admin/accounts`, требует IsAdmin)

#### `POST /api/admin/accounts`
**Создание новой учётки с выпиской invite**

**Тело:**
```json
{
  "email": "newteacher@example.com",
  "role": "teacher",
  "teacher_id": 42
}
```

**Ответ 201:**
```json
{
  "id": 123,
  "email": "newteacher@example.com",
  "role": "teacher",
  "teacher_id": 42,
  "invite_url": "/login/set-password?token=aB12cD34eF56gH78iJ90kL12mN34oP56qR78sT90uV12wX34yZ56",
  "expires_at": "2026-06-14T12:34:56Z"
}
```

**Ошибки:**
- 409 — email занят → `{"error": "Email уже используется"}`

**Заметка:** `invite_url` — относительный URL, сформированный как `/login/set-password?token={plaintext}`. Plaintext-токен выдаётся **один раз** в этом ответе. Админ копирует URL и передаёт сотруднику.

---

#### `POST /api/admin/accounts/{id}/reset-password`
**Выписать новый invite для смены пароля**

**Ответ 200:**
```json
{
  "invite_url": "/login/set-password?token=…",
  "expires_at": "2026-06-14T12:34:56Z"
}
```

**Эффект:** отзывает старые active invite аккаунта, создаёт новый.

---

#### `POST /api/admin/accounts/{id}/invite`
**Перевыпустить invite-ссылку (отзов старых, новая)**

**Ответ 200:**
```json
{
  "invite_url": "/login/set-password?token=…",
  "expires_at": "2026-06-14T12:34:56Z"
}
```

---

#### `POST /api/admin/accounts/{id}/invite/revoke`
**Отозвать активные invite аккаунта**

**Ответ 200:**
```json
{"ok": true}
```

**Эффект:** revoked_at = now() для всех активных инвайтов аккаунта.

---

#### `GET /api/admin/accounts`
**Пагинированный список учёток (с вычисляемым статусом)**

**Параметры:**
- page, page_size, sort_by, sort_dir
- Фильтры: filter[email], filter[role], filter[active], filter[teacher_name]

**Ответ:**
```json
{
  "rows": [
    {
      "id": 1,
      "email": "admin@x.com",
      "role": "admin",
      "teacher_id": null,
      "active": true,
      "twofa_enabled": true,
      "twofa_method": "totp",
      "last_login_at": "2026-06-12T10:00:00Z",
      "has_active_invite": false,
      "teacher_name": null,
      "status": "active"
    },
    {
      "id": 2,
      "email": "newteacher@x.com",
      "role": "teacher",
      "teacher_id": 42,
      "active": true,
      "twofa_enabled": false,
      "twofa_method": null,
      "last_login_at": null,
      "has_active_invite": true,
      "teacher_name": "Иван Петров",
      "status": "invited"
    }
  ],
  "total": 2,
  "page": 1,
  "page_size": 50
}
```

**Поле `status`** — вычисляемое, не хранится в БД:
- `disabled` — active=false
- `active` — есть last_login_at
- `invited` — есть активный инвайт, пароль не установлен
- `expired` — ни входа, ни активного инвайта

---

## Фронтенд: страница установки пароля

### `/login/set-password.html` + `/login/set-password.js`

Vanilla JavaScript, стили переиспользуют `/login/styles.css`.

**Три экрана:**

1. **Ссылка недействительна** (id=screen-invalid)
   - Если token отсутствует или GET /invite вернул `{valid: false}`
   - Текст: «Ссылка недействительна, обратитесь к администратору»
   - Нет кнопок, нет редиректов (пользователь ждёт новую ссылку)

2. **Задайте пароль** (id=screen-set)
   - Показывается email из GET /invite ответа
   - Ввод пароля, валидация ≥8 символов
   - На submit: POST /api/auth/invite/accept
   - При успехе: переход на экран 2FA

3. **Настройте 2FA** (id=screen-2fa)
   - Показ QR-кода (TOTP по умолчанию)
   - Ввод кода из приложения-аутентификатора
   - На submit: POST /api/auth/2fa/enable
   - При успехе: показ recovery-кодов (6 сек на прочтение), редирект в кабинет

**Важные детали:**
- Подключить внешние ресурсы запрещено (защита от утечки query-string в Referer)
- `autocomplete="one-time-code"` + `inputmode="numeric"` на поле кода для мобильного удобства
- Первый вход → обязательно 2FA, пропуска нет

---

## Утилиты и константы

### `apps/core/utils/passwords.py`

```python
INVITE_TTL_HOURS = 48  # Срок жизни invite-ссылки

def generate_invite_token() -> Tuple[str, str]:
    """Вернёт (plaintext, sha256hex). Plaintext выдаётся один раз."""

def hash_invite_token(token: str) -> str:
    """SHA-256 hex для поиска в БД."""
```

### `apps/auth_app/services.py`

```python
def requires_2fa(role: str) -> bool:
    """Возвращает True для ВСЕХ ролей (включая teacher)."""

def invite_lookup(token: str) -> Tuple[dict, int]:
    """GET /invite. Возвращает (data, http_status)."""

def invite_accept(token: str, password: str, request=None) -> Tuple[dict, int, Optional[dict]]:
    """POST /invite/accept. account=None (не выдаёт сессию)."""
```

### `apps/accounts/repository.py`

```python
def create_invite(account_id, token_hash, created_by, expires_at) -> dict:
    """INSERT в account_invites. Возвращает полную строку."""

def find_active_by_hash(token_hash) -> Optional[dict]:
    """Активный инвайт или None."""

def revoke_active_for_account(account_id: int) -> int:
    """Отозвать все активные инвайты аккаунта. Возвращает кол-во."""

def accept_invite(invite_id: int, password_hash: str) -> Optional[dict]:
    """SELECT FOR UPDATE + погашение + установка пароля + bump token_version."""

def bump_token_version(account_id: int) -> None:
    """Инкремент token_version (инвалидация сессий)."""

def get_auth_state(account_id: int) -> Optional[dict]:
    """{token_version, active} для проверки в HmacSessionAuthentication."""
```

---

## Bootstrap первого админа

### Django-команда

```bash
python manage.py bootstrap_admin --email=admin@example.com [--if-empty]
```

**Опции:**
- `--email` (обязательный) — email администратора
- `--if-empty` (флаг) — создавать только если нет ни одного админа (idempotent для деплея)

**Вывод:**
```
Создан admin admin@example.com.
Invite-ссылка (48 ч): /login/set-password?token=aB12cD34eF56gH78iJ90kL12mN34oP56qR78sT90uV12wX34yZ56
```

**Реализация:** `apps/accounts/management/commands/bootstrap_admin.py`
- Проверяет email нормализацию (lowercase, trim, базовая валидация)
- Вызывает `repository.create_account(email=…, role='admin')`
- Выписывает invite через `services.issue_invite`
- Печатает invite-URL в stdout (НЕ пароль)
- Логирует в audit (actor_account_id=None для bootstrap)

**Использование в деплее:**
```bash
# In systemd service или Docker entrypoint (при каждом перезапуске):
python manage.py migrate
python manage.py bootstrap_admin --email=admin@example.com --if-empty
gunicorn ...
```

### Гейт пустой системы

В dev-режиме (`urls_dev.py`), если admin-аккаунтов нет, путь `/admin` ведёт на страницу:
> Перед входом в админ-панель нужно создать администратора:
> ```
> python manage.py bootstrap_admin --email=YOUR_EMAIL --if-empty
> ```

Это предотвращает chicken-and-egg при первом запуске.

---

## Обработка ошибок и граничные случаи

### Невалидный/просроченный/использованный инвайт
Все возвращают **одинаковый** ответ (анти-энумерация):
```json
{"valid": false}
```
Фронт показывает экран «ссылка недействительна», без подробностей причины.

### Гонка двойного accept одного токена
`accept_invite` использует `SELECT FOR UPDATE` в транзакции:
```python
with transaction.atomic():
    invite = AccountInvite.objects.select_for_update().filter(...).first()
    if invite is None or invite.expires_at < timezone.now():
        return None
    # mark used, set password, bump version
```
Второй запрос получит None → 400 с `{valid: false}`.

### Слабый пароль (< 8 символов)
`InviteAcceptSerializer` валидирует `min_length=8`. Ошибка:
```json
{
  "password": ["Ensure this field has at least 8 characters."]
}
```

### Смена пароля инвалидирует старые сессии
`accept_invite` вызывает `bump_token_version` → все существующие cookie со старой версией перестают приниматься. Это защита от Session Fixation — пользователь после входа получает **новую** сессию (с обновлённой версией).

---

## Аудит и логирование

События логируются через `apps/audit.services.log_event`:

```python
# При создании invite:
log_event('invite_created', account_id=actor_id, target_id=account_id, 
          meta={'email': acc['email'], 'role': acc['role']}, request=request)

# При успешном accept:
log_event('invite_used', account_id=account_id, actor_email=acc['email'], request=request)

# При отзыве invite:
log_event('invite_revoked', account_id=actor_id, target_id=account_id, 
          meta={'email': acc['email']}, request=request)

# При сбросе пароля (через invite):
log_event('password_reset', account_id=actor_id, target_id=account_id, request=request)

# При смене 2FA:
log_event('2fa_enable', account_id=account_id, request=request)
log_event('2fa_disable', account_id=account_id, request=request)
```

Таблица: `security_audit_log` (см. `docs/db-schema.md`).

---

## Изменения в существующих эндпоинтах

### POST /api/auth/login — смена логики при отсутствии 2FA

**Было:** teacher без 2FA → прямая выдача session-cookie.

**Теперь:** любой без установленной 2FA → `twofa_enrollment_required`:
```json
{
  "twofa_enrollment_required": true,
  "challenge_token": "…"
}
```
Сотрудник переходит в `POST /api/auth/2fa/setup`.

Это касается старых аккаунтов (teacher, которые входили без 2FA). Новые аккаунты 2FA настраивают на онбординге.

### Ответ POST /api/admin/accounts (был)
```json
{
  "id": 123,
  "email": "…",
  "role": "…",
  "teacher_id": …,
  "password": "ABCD-EFGH-IJKL"  ← УДАЛЕНО
}
```

### Ответ POST /api/admin/accounts (стало)
```json
{
  "id": 123,
  "email": "…",
  "role": "…",
  "teacher_id": …,
  "invite_url": "/login/set-password?token=…",  ← НОВОЕ
  "expires_at": "2026-06-14T12:34:56Z"          ← НОВОЕ
}
```

### Ответ POST /api/admin/accounts/{id}/reset-password (был)
```json
{
  "password": "…"  ← УДАЛЕНО (temp-пароль больше не генерится)
}
```

### Ответ POST /api/admin/accounts/{id}/reset-password (стало)
```json
{
  "invite_url": "/login/set-password?token=…",
  "expires_at": "…"
}
```

---

## Совместимость и миграции

### Как это работает с существующими аккаунтами?

**Существующие admin/manager:**
- Могут продолжать входить как раньше (пароль установлен, 2FA может быть включена)
- Поле `token_version` инициализируется DEFAULT 0, сверяется в session-cookie
- При смене пароля/2FA версия инкрементируется → старые cookie инвалидируются

**Существующие teacher без 2FA:**
- При входе встречают `twofa_enrollment_required` (новая логика `requires_2fa=True`)
- Должны настроить 2FA до доступа (новое UX)

**Новые учётки (через invite):**
- 2FA настраивается на онбординге (неизбежно)

### Миграция базы

```bash
cd journal_django
.venv/Scripts/python.exe manage.py migrate accounts
```

Миграции автоматически:
- Добавляют token_version (DEFAULT 0)
- Создают таблицу account_invites
- Делают password_hash nullable

---

## Развёртывание и конфигурация

### Окружение (`.env`)

Никаких новых переменных. Используются существующие:
- `ADMIN_COOKIE_SECRET` — HMAC-ключ для сессии (должен быть установлен)
- `DATABASE_URL` — PostgreSQL connection string
- `SMTP_*` — для email-OTP (если используется)

### Dev-запуск

```bash
cd journal_django
.venv/Scripts/python.exe manage.py runserver
```

Автоматически включает dev-раздачу статики (`/login/set-password.*`).

### Prod-деплей

1. Выполнить миграции:
   ```bash
   python manage.py migrate
   ```

2. Создать первого админа:
   ```bash
   python manage.py bootstrap_admin --email=admin@example.com --if-empty
   ```

3. Запустить gunicorn (как обычно):
   ```bash
   gunicorn journal_django.wsgi:application
   ```

4. **nginx configuration** (ключевые части):
   ```nginx
   # Раздача статики login (без query-string в логах!):
   location ~ ^/login/ {
       # Не логировать query-string (защита от plaintext-токена в логах)
       access_log /var/log/nginx/access.log combined_no_qs;
       alias /path/to/journal_django/frontend/login/;
   }
   
   # Rate-limit на основные auth-пути:
   limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=5r/m;
   limit_req_zone $binary_remote_addr zone=invite_limit:10m rate=10r/m;
   
   location /api/auth/login {
       limit_req zone=auth_limit burst=10;
       proxy_pass http://app;
   }
   location /api/auth/invite {
       limit_req zone=invite_limit burst=20;
       proxy_pass http://app;
   }
   ```

---

## Резюме ключевых функций

| Функция | Файл | Возвращает | Примечание |
|---------|------|-----------|-----------|
| `issue_invite(account_id, actor_id, request)` | accounts/services.py | {invite_url, expires_at} | Отзывает старые, создаёт новый |
| `invite_lookup(token)` | auth_app/services.py | ({valid, email?, role?}, 200) | Анти-энумерация |
| `invite_accept(token, password, request)` | auth_app/services.py | ({challenge_token}, 200) / ({error}, 400) | SELECT FOR UPDATE |
| `create_account(data, actor_id, request)` | accounts/services.py | {id, email, role, …, invite_url, expires_at} | БЕЗ пароля |
| `bump_token_version(account_id)` | accounts/repository.py | None | Инвалидирует сессии |
| `accept_invite(invite_id, password_hash)` | accounts/repository.py | {…} / None | В транзакции |

---

Дальше: см. **[security.md](security.md)** для контролей и ограничений, **[testing.md](testing.md)** для тестового покрытия.
