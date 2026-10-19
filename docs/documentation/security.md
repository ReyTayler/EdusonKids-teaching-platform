# Безопасность: Провижининг аккаунтов и 2FA

**Статус:** Реализовано. Планы на следующую итерацию помечены как отложено.

## Реализованные контроли (текущий релиз)

### 1. Stateful инвалидация сессий через token_version

**Проблема, которую решает:** HMAC-сессия подписана на сервере, но не хранится. Смена пароля не завершает уже выданные cookie.

**Решение:** Поле `Account.token_version` (целое число, DEFAULT 0).

#### Как работает

1. **Payload сессии расширен:**
   ```json
   {
     "account_id": 1,
     "role": "admin",
     "iat": 1718188800000,
     "exp": 1718275200000,
     "token_version": 0
   }
   ```

2. **На каждом запросе в `HmacSessionAuthentication.authenticate`:**
   ```python
   # 1. Декодировать и проверить подпись (как было)
   payload = verify_session_cookie(token)
   if payload is None:
       return None
   
   # 2. НОВОЕ: проверить версию + активность аккаунта
   state = accounts_repo.get_auth_state(payload['account_id'])
   if state is None or not state['active']:
       return None
   if int(payload.get('token_version', 0)) != int(state['token_version']):
       return None
   
   return ShimUser(payload), None
   ```

3. **Когда версия инкрементируется:**
   - Смена пароля → `repository.bump_token_version(account_id)`
   - Включение/отключение 2FA → `repository.bump_token_version(account_id)`
   - Смена email → `repository.bump_token_version(account_id)`
   - Деактивация аккаунта → `repository.bump_token_version(account_id)`
   - Сброс пароля (reset-2fa) → `repository.bump_token_version(account_id)`
   - Успешное accept invite → `Account.update(token_version=F('token_version')+1)`

#### Пример: смена пароля инвалидирует сессии

```
1. Пользователь вошёл → сессия с token_version=0
   cookie = "eJwtMQ...{...token_version:0...}.abc123def"

2. Админ делает POST /api/admin/accounts/42/reset-password
   → services.reset_password() → issue_invite() → log_event()
   → repository.bump_token_version(42)
   → Account.objects.filter(id=42).update(token_version=F('token_version')+1)
   → БД: Account[42].token_version = 1

3. Пользователь делает следующий запрос со старой сессией
   → HmacSessionAuthentication.authenticate() проверяет
   → payload['token_version'] = 0, БД state['token_version'] = 1
   → НЕ совпадает → return None → 401 Unauthorized

4. Пользователь должен переложить и получить новую сессию
```

#### Производительность

Каждый запрос → один точечный SELECT:
```sql
SELECT token_version, active FROM accounts WHERE id = ?
```
На текущем масштабе (50–100 учителей, 10–15 админов) это пренебрежимо. Кэширование на уровне Python-процесса не требуется.

---

### 2. Kind-guard: защита от Session Fixation через challenge-токены

**Проблема:** Challenge-токены (для 2FA-verify, email-OTP) имеют собственный формат payload (`{kind, stage, account_id, …}`), но проходят через HMAC-верификацию. Злоумышленник может выдать себя за сессию, подложив challenge-токен как session-cookie.

**Решение:** Проверка поля `kind` в `HmacSessionAuthentication`:

```python
def authenticate(self, request: Request):
    ...
    payload = verify_session_cookie(token)
    if payload is None:
        return None
    
    # Session-cookie не имеет поля 'kind'.
    # Challenge-токены отклоняем (они содержат 'kind': 'login_challenge').
    if payload.get('kind'):
        return None
    
    ...
```

#### Почему важно

- **Без guard:** challenge с payload `{kind: 'login_challenge', account_id: 1, role: 'admin', exp: future}` проходит HMAC-проверку и `ShimUser(payload)` создастся → пользователь прошёл аутентификацию без 2FA.
- **С guard:** поле `kind` есть → возвращаем None → 401.

---

### 3. Ротация сессии после аутентификации

**Принцип:** Pre-auth challenge-токены НЕ переиспользуются как сессия. После 2FA выпускается **новая** аутентифицированная сессия.

#### Поток

```
1. POST /api/auth/login {email, password, role}
   ← Проверка пароля: OK
   → if requires_2fa: issue_challenge(acc, 'verify')  ← pre-auth challenge
   ← {twofa_required: true, challenge_token: '...', method: 'totp'}
   
   ВАЖНО: session-cookie НЕ выдаётся!

2. POST /api/auth/login/2fa {challenge_token, code}
   ← Проверка кода 2FA: OK
   → НОВАЯ сессия через issue_session(acc)  ← authenticated session
   ← Set-Cookie: session=...{token_version: 0}; HttpOnly; Secure; SameSite=Strict
   ← {role: '…', redirect: '/admin'}

3. Пользователь получает session-cookie и входит в админку
```

#### Для invite-flow

```
1. POST /api/auth/invite/accept {token, password}
   ← Установка пароля: OK
   → issue_challenge(acc, 'enroll')  ← enroll challenge
   ← {challenge_token: '...'}
   
   ВАЖНО: session-cookie НЕ выдаётся!

2. POST /api/auth/2fa/setup {challenge_token, method: 'totp'}
   ← Инициировать setup: OK
   → {qr: '...', secret: '...'}  ← инструкции для setup

3. POST /api/auth/2fa/enable {challenge_token, code}
   ← Проверка кода: OK
   → НОВАЯ сессия через issue_session(acc)  ← authenticated session
   ← Set-Cookie: session=...
   ← {recovery_codes: [...], redirect: '/teacher'}
```

#### Почему это важно

- Защита от Session Fixation: pre-auth токен НЕ может быть использован для доступа.
- Challenge имеет короткий TTL (5 минут), не переиспользуется.
- Аутентифицированная сессия выпускается только после успешного 2FA.

---

### 4. Дифференцированный Rate-Limit

**Реализация:** Best-effort in-memory лимитер по IP (bucket'ы). На prod-cutover уходит в nginx `limit_req`.

#### Таблица пороговых значений

| Эндпоинт | Лимит | Окно | Bucket | Ключ |
|----------|-------|------|--------|-----|
| POST /api/auth/login | 5 попыток | 15 мин | login | IP |
| POST /api/auth/login/2fa | 10 попыток | 15 мин | 2fa | IP |
| POST /api/auth/2fa/email/send | 3 попытки | 60 мин | email_send | IP |
| GET/POST /api/auth/invite* | 10 попыток | 15 мин | invite | IP |

#### Реализация

**Слой View** (`apps/auth_app/views.py`):
```python
_RATE_LIMITS = {
    'auth':       (10, 15 * 60),
    'login':      (5,  15 * 60),
    '2fa':        (10, 15 * 60),
    'email_send': (3,  60 * 60),
    'invite':     (10, 15 * 60),
}
_rate_store: dict[str, list[float]] = {}  # "<bucket>:<ip>" → [timestamps]
_rate_lock = threading.Lock()

def _check_rate_limit(request: Request, bucket: str = 'auth') -> bool:
    """Вернуть True если OK, False если превышен."""
    max_hits, window_s = _RATE_LIMITS.get(bucket, _RATE_LIMITS['auth'])
    ip = _get_client_ip(request)
    key = f'{bucket}:{ip}'
    now = time.time()
    cutoff = now - window_s
    
    with _rate_lock:
        hits = [t for t in _rate_store.get(key, []) if t > cutoff]
        if len(hits) >= max_hits:
            return False
        hits.append(now)
        _rate_store[key] = hits
        return True
```

**Использование в Views:**
```python
class LoginView(APIView):
    def post(self, request: Request) -> Response:
        if not _check_rate_limit(request, bucket='login'):
            return Response(_RATE_EXCEEDED, status=status.HTTP_429_TOO_MANY_REQUESTS)
        # … логика входа
```

#### Известные ограничения

- **Best-effort:** per-process, не делится между gunicorn-воркерами. Спуфится X-Forwarded-For.
- **Вторичный слой:** основная защита login/2FA от перебора — БД-локаут (`register_login_failure`, 5→15 мин), который XFF не обходит.
- **Prod-защита:** nginx `limit_req` на cutover (жёсткий лимит, прямо на уровне TCP).

---

### 5. Cookie-флаги безопасности

**Session-cookie** (`session`):
```
Set-Cookie: session=...; HttpOnly; Secure (prod); SameSite=Strict
```

| Флаг | Значение | Зачем |
|------|----------|-------|
| HttpOnly | true | Не доступна JavaScript → защита от XSS |
| Secure | true (prod) | Передаётся только по HTTPS |
| SameSite | Strict | Не отправляется в cross-site requests (CSRF protection) |

**Реализация:** `apps/auth_app/sessions.py` → `issue_session()`:
```python
def issue_session(account: dict, request=None) -> str:
    token = sign(payload, _secret())
    # Response-слой (views.py) ставит cookie:
    response.set_cookie(
        'session',
        token,
        max_age=COOKIE_LIFETIME_MS // 1000,
        httponly=True,
        secure=settings.SECURE_COOKIES,  # True в prod
        samesite='Strict',
    )
```

---

### 6. Хеширование invite-токена (без соли)

**Токен:** 32 байта CSPRNG в URL-safe base64 → 256 бит энтропии.

**В БД:** SHA-256 hex (20 символов HEX = 32 байта = 256 бит).

**Без соли?** Да, потому что:
1. High-entropy random: 256 бит достаточно для защиты от перебора (2^128 попыток → невозможно)
2. Соль не добавляет энтропии для random-data (only для паролей)
3. Стандартная практика для API-токенов (GitHub, Stripe, etc.)

```python
def generate_invite_token() -> Tuple[str, str]:
    plaintext = secrets.token_urlsafe(32)  # 256 бит
    token_hash = hashlib.sha256(plaintext.encode('utf-8')).hexdigest()
    return plaintext, token_hash

# plaintext выпускается один раз в JSON-ответе API → админ копирует
# token_hash хранится в БД → поиск по hash при проверке
```

**Защита от утечки plaintext:**
- Хранится только hash в БД (даже админ БД не видит)
- Plaintext НЕ логируется (вырезается из audit-log)
- Plaintext может попасть в nginx access-log → рекомендация: не логировать query-string для `/login/set-password`

---

### 7. Bcrypt: совместимость Node ↔ Python

**Cost:** 12 (фиксированный, как в Node).

**Формат:** `$2b$12$...` (Python bcrypt использует `$2b$`, Node bcryptjs тоже).

**Проверка совместимости:** существующие хэши Node проходят через `bcrypt.checkpw()` без проблем.

```python
import bcrypt

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode('utf-8')

def verify_password(plain: str, hashed: Optional[str]) -> bool:
    if not hashed:
        return False
    return bcrypt.checkpw(plain.encode('utf-8'), hashed.encode('utf-8'))
```

---

### 8. Аудит событий безопасности

**Таблица:** `security_audit_log` (существующая, `apps/audit/models.py`).

**События, логируемые:**

| Событие | account_id | target_id | meta | Когда |
|---------|-----------|-----------|------|-------|
| invite_created | актор | цель | {email, role} | POST /api/admin/accounts |
| invite_used | цель | - | - | POST /api/auth/invite/accept (успешно) |
| invite_revoked | актор | цель | {email} | POST /api/admin/accounts/:id/invite/revoke |
| password_reset | актор | цель | - | POST /api/admin/accounts/:id/reset-password |
| account_deactivated | актор | цель | - | DELETE /api/admin/accounts/:id |
| 2fa_reset | актор | цель | - | POST /api/admin/accounts/:id/reset-2fa |
| 2fa_enable | кто активировал | - | - | POST /api/auth/2fa/enable |
| 2fa_disable | актор | цель | - | POST /api/auth/2fa/disable |
| login_success | пользователь | - | - | POST /api/auth/login (успешно) |
| login_fail | - | - | {reason: 'password'|'role'|'no_account'} | POST /api/auth/login (ошибка) |
| locked | пользователь | - | - | POST /api/auth/login (блокировка по attempts) |

**Реализация:**
```python
from apps.audit.services import log_event

log_event(
    event='invite_created',
    account_id=actor_account_id,  # кто создал
    target_id=account_id,          # кого
    meta={'email': '…', 'role': '…'},
    request=request,               # для IP, User-Agent
)
```

---

### 9. Partial-unique constraint: один активный invite на аккаунт

**Таблица:** `account_invites`

```sql
UNIQUE(account_id) 
WHERE used_at IS NULL AND revoked_at IS NULL
```

**Чем это защищает:** от одновременного accept'а нескольких invite одного аккаунта.

**Реализация:**
```python
class AccountInvite(models.Model):
    ...
    constraints = [
        models.UniqueConstraint(
            fields=['account'],
            name='ai_one_active_per_account',
            condition=models.Q(used_at__isnull=True, revoked_at__isnull=True),
        ),
    ]
```

---

### 10. Ленивая очистка invite

**Текущее решение:** при создании нового invite отзываются все старые:
```python
def issue_invite(account_id, actor_id, request):
    ...
    with transaction.atomic():
        repository.revoke_active_for_account(account_id)  # revoked_at=now()
        repository.create_invite(...)
```

**Зачем:** гарантия одного активного invite.

**История сохраняется:** физически запись в БД не удаляется, revoked_at отметит старые для аудита.

**Будущее (отложено):** management-команда `purge_invites` для архивирования старых записей (> 30 дней).

---

### 11. Анти-энумерация: opaque error response

**GET/POST /api/auth/invite*** всегда возвращают `{valid: false}` без подробностей:

```python
def invite_lookup(token: str) -> Tuple[dict, int]:
    inv = accounts_repo.find_active_by_hash(hash_invite_token(token))
    if inv is None:
        return {'valid': False}, 200  # единый ответ
    ...
```

**Защита от:** перебора токенов, узнавания существования аккаунта по ошибке.

---

## Отложено: следующая итерация

### 1. Trusted Device (запомнить устройство)

**Назначение:** пропустить 2FA на доверенных устройствах (30 дней), без снижения требований для новых.

#### Архитектура (спроектировано, не реализовано)

**Таблица:**
```
trusted_devices (
    id            serial PK,
    account_id    int FK CASCADE,
    selector      text NOT NULL,      # случайный ID (в cookie)
    validator_hash text NOT NULL,    # SHA-256(validator)
    user_agent    text,               # отпечаток браузера
    created_at    timestamptz,
    expires_at    timestamptz,        # 30 дней
    revoked_at    timestamptz NULL,   # ручной отзыв
)
```

**Поток:**
1. POST /api/auth/login/2fa (успешно) + чекбокс «Запомнить на 30 дней»
2. Сервер генерирует `selector` (случайный) и `validator` (32+ байта)
3. Кладёт cookie `trusted_device=selector:validator`
4. Сохраняет `(account_id, selector, hash(validator), expires_at=now+30d)` в БД

**При входе:**
1. POST /api/auth/login (email + пароль: OK)
2. Ищет cookie `trusted_device`, парсит selector
3. Запрашивает в БД: `SELECT validator_hash FROM trusted_devices WHERE selector=… AND revoked_at IS NULL AND expires_at > now()`
4. Сравнивает: hash(validator) = validator_hash из БД
5. Если совпадает → пропускает 2FA, выдаёт сессию

#### Step-Up для чувствительных операций

Доверенное устройство НЕ отменяет повторного 2FA для:
- Смены пароля
- Смены email
- Включения/отключения/смены метода 2FA
- Просмотра recovery-кодов
- Создания/удаления/изменения других аккаунтов
- Просмотра audit-log
- Массовых операций

#### Инвалидация доверенных устройств

Все trusted-device пользователя инвалидируются (revoked_at=now()) при:
- Смене пароля
- Сбросе пароля
- Включении/отключении/смене 2FA
- Восстановлении через recovery-код
- Деактивации аккаунта

```sql
UPDATE trusted_devices SET revoked_at = now() WHERE account_id = ?;
```

#### Управление устройствами из профиля

Фронт (admin SPA):
- GET /api/auth/trusted-devices → список устройств (только selector, user_agent, created_at, expires_at)
- POST /api/auth/trusted-devices/:id/revoke → инвалидировать одно устройство

---

### 2. 2FA-онбординг по ролям

**Реализовано:** все роли требуют 2FA.

**Дифференциация (спроектирована, не реализована на фронте):**
- **Admin/Manager** → TOTP по умолчанию, быстрая настройка, блокирует доступ
- **Teacher** → TOTP рекомендуется, с расширенными подсказками; альтернатива — email-OTP

Это чисто фронтовое различие (UI/подсказки), требует правок `set-password.js` и `login.js`.

---

## Чек-лист угроз и контролей

| Угроза | Контроль | Статус |
|--------|---------|--------|
| Временный пароль в логах | Invite-ссылка (одноразовая); не логируется | ✓ Реализовано |
| Перебор invite-токена | 256-бит CSPRNG, 48-часовой TTL | ✓ Реализовано |
| Replay invite-токена | SELECT FOR UPDATE, used_at отметка | ✓ Реализовано |
| Session Fixation (challenge-токен как session) | kind-guard + ротация сессии | ✓ Реализовано |
| Смена пароля не завершает сессии | token_version в payload + stateful check | ✓ Реализовано |
| Перебор входа (brute-force) | 5 неудач → 15 мин блокировка (БД-локаут) | ✓ Существовало |
| Перебор 2FA-кода | 10 неудач → 15 мин блокировка (rate-limit) | ✓ Реализовано |
| Спуфинг X-Forwarded-For | Основной лимит — БД-локаут (per-account) | ✓ Частично (nginx дополнит) |
| CSRF на invite-accept | SameSite=Strict на session-cookie | ✓ Реализовано |
| Plaintext-токен в nginx-логе | Рекомендация: не логировать query для /login/* | ⚠️ Документировано, требует nginx-правки |
| XSS на set-password.html | HttpOnly cookie, нет внешних ресурсов | ✓ Реализовано |
| Man-in-the-middle (http) | Secure flag на cookie (prod) | ✓ Реализовано |
| Ошибка БД при accept (FK нарушение) | Savepoint в audit.log_event | ✓ Существовало |
| Повторное использование TOTP-кода | 90-секундное окно (valid_window=1 в pyotp) | ⚠️ Известное ограничение порта |

---

## Итог: угрозы уровня Risk

| Уровень | Угроза | Контроль | Примечание |
|---------|--------|---------|-----------|
| **HIGH** | Временный пароль в plaintext | Invite-ссылка → hash в БД | ✓ Закрыто |
| **HIGH** | Session Fixation | kind-guard + ротация | ✓ Закрыто |
| **HIGH** | Смена пароля не инвалидирует сессии | token_version | ✓ Закрыто |
| **MEDIUM** | Перебор 2FA | Rate-limit (best-effort) + nginx | ⚠️ Укрепляется на cutover |
| **MEDIUM** | Plaintext-токен в логах | Документированная nginx-правка | ⚠️ Требует ручной настройки |
| **MEDIUM** | TOTP-reuse в окне | valid_window=1 (~90 сек) | ⚠️ Низкий риск (TLS+сессия) |
| **LOW** | XFF-спуфинг rate-limit | БД-локаут как основной слой | ✓ Закрыто |

---

**Дальше:** см. **[testing.md](testing.md)** для проверки реализации в тестах.
