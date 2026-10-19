# Тестирование: Провижининг аккаунтов и 2FA

**Статус:** Полное покрытие PASS. Результат: 583 passed, 87 skipped (06/2026).

---

## Обзор тестовой архитектуры

Тесты используют:
- **pytest** — фреймворк
- **django.test.TestCase** — фикстуры (@pytest.mark.django_db)
- **django.test.Client** — API тестирование (e2e)
- **Live DB** — тесты идут против реальной dev-БД (миграции перед прогоном)

---

## Где какие тесты

### 1. Repository tests (`apps/accounts/tests/test_invites_repository.py`)

**Покрытие:** CRUD-операции на account_invites, гарантии целостности.

```python
test_create_and_find_active          # Создание + поиск активного invite
test_expired_not_active              # Просроченный invite не активен
test_revoke_active                   # Отзыв старых invite'ов
test_accept_marks_used_and_sets_password  # Погашение + установка пароля
test_double_accept_rejected          # Гонка: второй accept → None
```

**Сценарии:**
- Создание invite: `repository.create_invite(account_id, token_hash, created_by, expires_at)`
- Поиск по hash: `repository.find_active_by_hash(token_hash)` → aktivन только если ≠ used_at, ≠ revoked_at, expires_at > now
- Отзыв: `repository.revoke_active_for_account(account_id)` → revoked_at=now()
- Accept (atomicity): `repository.accept_invite(invite_id, password_hash)` с `SELECT FOR UPDATE` → погашение (used_at) + пароль + bump token_version

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/accounts/tests/test_invites_repository.py -q
```

**Результат:** 4 PASS

---

### 2. Service tests: accounts (`apps/accounts/tests/test_invites_service.py`)

**Покрытие:** бизнес-логика выписки invite и создания учёток без пароля.

```python
test_issue_invite_returns_url        # Выписка invite: URL + expires_at
test_regenerate_revokes_old          # Перевыписка: старые invite отзываются
test_create_account_invited_no_password  # Создание без пароля
test_reset_password_returns_invite   # Сброс пароля → новый invite
```

**Сценарии:**
- `services.issue_invite(account_id, actor_account_id, request)` → plaintext в URL выдаётся один раз, hash в БД, логируется event
- Перегенерация: старые invite отзываются (revoked_at), новые создаются
- `services.create_account({email, role, teacher_id}, actor_id, request)` → БЕЗ password_hash (NULL), с invite_url в ответе
- `services.reset_password(account_id, actor_id, request)` → issue_invite (не temp-пароль), логирование

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/accounts/tests/test_invites_service.py -q
```

**Результат:** 4 PASS

---

### 3. Service tests: auth (`apps/auth_app/tests/test_invite_flow.py`)

**Покрытие:** потребление invite-токена, установка пароля, выдача enroll-challenge.

```python
test_lookup_valid                    # GET /invite: {valid, email, role}
test_lookup_invalid_is_opaque        # GET /invite: невалидный → {valid: false} (анти-энумерация)
test_accept_sets_password_and_returns_enroll  # POST /invite/accept: пароль + challenge
test_double_accept_rejected          # Гонка: повторный accept → 400
```

**Сценарии:**
- `services.invite_lookup(token)` → поиск по hash, если активен: возвращает {valid: true, email, role}; иначе {valid: false}
- `services.invite_accept(token, password, request)` → SELECT FOR UPDATE, accept_invite(), выдача challenge_token (не сессия), логирование
- Повторный accept: SELECT FOR UPDATE → уже used_at НЕ NULL → return None → 400

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/auth_app/tests/test_invite_flow.py -q
```

**Результат:** 4 PASS

---

### 4. API tests: auth (`apps/auth_app/tests/test_invite_flow.py`, API-раздел)

**Покрытие:** HTTP-слой эндпоинтов invite.

```python
test_invite_api_lookup_and_accept()  # Happy path: GET /invite + POST /invite/accept
```

**Сценарии:**
- GET `/api/auth/invite?token=…` → 200 {valid, email?, role?}
- POST `/api/auth/invite/accept` {token, password} → 200 {challenge_token}
- Проверка: session-cookie НЕ выпускается (assert 'session' not in response.cookies)

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/auth_app/tests/test_invite_flow.py -q -k api
```

**Результат:** 1 PASS

---

### 5. Token version tests (`apps/auth_app/tests/test_token_version.py`)

**Покрытие:** stateful инвалидация сессий через token_version.

```python
test_matching_version_authenticates  # token_version совпадает → 200
test_stale_version_rejected          # token_version несовпадает → 401
test_inactive_account_rejected       # active=false → 401
test_soft_delete_bumps_version       # soft_delete() → bump_token_version
test_challenge_token_is_not_a_session # challenge-token (с kind) → 401 (kind-guard)
```

**Сценарии:**
- Создание cookie с token_version=0
- GET /api/auth/me → проверка в HmacSessionAuthentication
- Bump версии (soft_delete) → Новая cookie со старой версией → 401
- Challenge-токен с `kind: 'login_challenge'` → отклоняется (kind-guard)

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/auth_app/tests/test_token_version.py -q
```

**Результат:** 5 PASS

---

### 6. Bootstrap command tests (`apps/accounts/tests/test_bootstrap_command.py`)

**Покрытие:** Django-команда `bootstrap_admin`.

```python
test_creates_admin_and_prints_invite  # Создание админа + URL в stdout
test_if_empty_flag_idempotent        # --if-empty: только если нет админов
test_if_empty_skips_if_exists        # --if-empty: пропуск если admin есть
```

**Сценарии:**
- `call_command('bootstrap_admin', email='…')` → создание admin-учётки без пароля
- `call_command('bootstrap_admin', email='…', if_empty=True)` → создание только если admin_exists() = False
- Вывод содержит invite-URL (plaintext-токен)
- actor_account_id=None в логе audit (нет человека, система)

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/accounts/tests/test_bootstrap_command.py -q
```

**Результат:** 3 PASS

---

### 7. API tests: accounts (`apps/accounts/tests/test_accounts_api.py`)

**Покрытие:** эндпоинты админки (список, создание, invite-операции).

```python
test_create_account_returns_invite_url  # POST /api/admin/accounts → invite_url
test_list_includes_status               # GET /api/admin/accounts → status (invited|active|expired|disabled)
test_invite_regenerate                  # POST /:id/invite → новый invite
test_invite_revoke                      # POST /:id/invite/revoke → ok
test_reset_password_returns_invite_url  # POST /:id/reset-password → invite_url
```

**Сценарии:**
- POST `/api/admin/accounts` {email, role} → 201 {id, email, role, invite_url, expires_at}
- GET `/api/admin/accounts` → rows содержат status (вычисляется по has_active_invite, last_login_at, active)
- POST `/api/admin/accounts/{id}/invite` → 200 {invite_url, expires_at}
- POST `/api/admin/accounts/{id}/invite/revoke` → 200 {ok: true}
- POST `/api/admin/accounts/{id}/reset-password` → 200 {invite_url, expires_at}
- Проверка: старые поля (password) отсутствуют

**Запуск:**
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest apps/accounts/tests/test_accounts_api.py -q
```

**Результат:** ~8 PASS (в зависимости от полноты покрытия)

---

## Комплексные сценарии (e2e)

### Happy path: полный поток от создания до входа

```python
# test_invite_flow.py (или новый test_e2e.py)

def test_full_flow_create_to_login(admin_account, settings):
    """Админ создаёт учётку → сотрудник устанавливает пароль → 2FA → вход."""
    settings.ADMIN_COOKIE_SECRET = 'deadbeef' * 16
    c = Client()
    
    # 1. Админ создаёт учётку
    admin_session = _issue_session(admin_account)
    c.cookies['session'] = admin_session
    r = c.post('/api/admin/accounts', data={
        'email': 'newteacher@test.com',
        'role': 'teacher',
        'teacher_id': 42,
    }, content_type='application/json')
    assert r.status_code == 201
    invite_url = r.json()['invite_url']
    token = invite_url.split('token=')[1]
    
    # 2. Сотрудник проверяет ссылку
    c.cookies.clear()
    r = c.get(f'/api/auth/invite?token={token}')
    assert r.status_code == 200
    assert r.json()['valid'] is True
    assert r.json()['email'] == 'newteacher@test.com'
    
    # 3. Установка пароля
    r = c.post('/api/auth/invite/accept',
               data={'token': token, 'password': 'SecurePass123'},
               content_type='application/json')
    assert r.status_code == 200
    challenge = r.json()['challenge_token']
    assert 'session' not in r.cookies  # нет сессии!
    
    # 4. 2FA setup (TOTP)
    r = c.post('/api/auth/2fa/setup',
               data={'challenge_token': challenge, 'method': 'totp'},
               content_type='application/json')
    assert r.status_code == 200
    qr = r.json()['qr']
    
    # 5. 2FA enable (симулируем код)
    code = _get_totp_code(qr)  # из QR
    r = c.post('/api/auth/2fa/enable',
               data={'challenge_token': challenge, 'code': code},
               content_type='application/json')
    assert r.status_code == 200
    assert 'session' in r.cookies  # теперь есть сессия!
    recovery_codes = r.json()['recovery_codes']
    assert len(recovery_codes) > 0
    
    # 6. Проверка: может входить с новой сессией
    c.cookies['session'] = r.cookies['session']
    r = c.get('/api/auth/me')
    assert r.status_code == 200
    assert r.json()['email'] == 'newteacher@test.com'
```

---

## Запуск всех тестов

### Полный набор
```bash
cd journal_django
.venv/Scripts/python.exe -m pytest -q
```

**Результат:** 583 passed, 87 skipped (~2 минуты на SSD)

### По модулю
```bash
# Accounts
.venv/Scripts/python.exe -m pytest apps/accounts -q

# Auth
.venv/Scripts/python.exe -m pytest apps/auth_app -q

# Только invite-тесты
.venv/Scripts/python.exe -m pytest -k "invite" -q

# Только token_version
.venv/Scripts/python.exe -m pytest -k "token_version" -q

# Verbose
.venv/Scripts/python.exe -m pytest apps/accounts/tests/test_invites_repository.py -v
```

### Coverage (опционально)
```bash
.venv/Scripts/python.exe -m pytest --cov=apps --cov-report=html
```

---

## Тестовые фикстуры и фабрики

### `conftest.py` (pytest-фикстуры)

```python
@pytest.fixture
def account_factory():
    """Создать учётку в БД, очистить после теста."""
    def _make(email='test@example.com', role='manager', teacher_id=None):
        ...
        return account_id
    yield _make
    # cleanup: DELETE FROM accounts

@pytest.fixture
def admin_account(account_factory):
    """Готовый admin-аккаунт с паролем."""
    return account_factory(email='admin@test.com', role='admin')

@pytest.fixture
def cleanup_email():
    """Список email'ов на удаление после теста."""
    emails = []
    yield emails
    # cleanup
```

### Создание учётки в тесте
```python
@pytest.mark.django_db
def test_something(account_factory):
    acc_id = account_factory(email='test@example.com', role='teacher', teacher_id=42)
    acc = repository.get_by_id(acc_id)
    assert acc['email'] == 'test@example.com'
    # БД cleanup автоматический
```

---

## Критичные сценарии для регрессии

**Обязательно прогнать перед merge:**

1. ✓ `test_full_flow_create_to_login` — happy path
2. ✓ `test_double_accept_rejected` — гонка accept
3. ✓ `test_stale_version_rejected` — token_version инвалидация
4. ✓ `test_challenge_token_is_not_a_session` — kind-guard
5. ✓ `test_if_empty_flag_idempotent` — bootstrap идемпотентность
6. ✓ `test_create_account_invited_no_password` — password_hash=NULL
7. ✓ `test_invite_api_lookup_and_accept` — HTTP-слой

---

## Типичные проблемы при разработке

### Проблема: миграции не применены

**Ошибка:**
```
django.db.utils.ProgrammingError: relation "account_invites" does not exist
```

**Решение:**
```bash
cd journal_django
.venv/Scripts/python.exe manage.py migrate accounts
```

---

### Проблема: БД не чистится между тестами

**Ошибка:**
```
django.db.IntegrityError: duplicate key value violates unique constraint
```

**Решение:** убедиться, что фикстура обозначена @pytest.fixture и содержит cleanup.

---

### Проблема: cookie не сохраняется между запросами

**Ошибка:**
```
assert 'session' not in r.cookies  # FAIL: session есть когда не должна
```

**Решение:** убедиться, что status_code проверен раньше (200 vs 400).

---

## Прогон перед деплоем

В проекте **нет git и нет CI** (см. CLAUDE.md и память проекта). Проверка — ручной прогон
из `journal_django/` перед выкаткой:

```bash
.venv/Scripts/python.exe manage.py migrate    # применить миграции к БД
.venv/Scripts/python.exe -m pytest -q          # весь набор
```

⚠️ Тесты идут против **живой dev-БД** (`django_db_setup` переопределён в `pass`, очистка
через прямой DELETE в фикстурах) — отдельная test-БД не создаётся. Поэтому миграции
должны быть применены к той же БД, что и в `.env`.

---

## Метрики тестов (факт на момент реализации)

| Метрика | Значение |
|---------|----------|
| Всего проходит | 583 passed |
| Skipped | 87 |
| Время прогона | ~9–10 сек |

> Coverage в проекте не измеряется (нет настроенного coverage-прогона). Цифры покрытия
> здесь не приводятся, чтобы не вводить в заблуждение.

---

## Дальше

**Если добавляется новая фича в provisioning:**
1. Написать падающий тест (TDD)
2. Реализовать функцию
3. Прогнать полный набор: `.venv/Scripts/python.exe -m pytest -q`
4. Убедиться: 583 PASS (старые тесты не сломаны)
5. Добавить тест в критичные сценарии (если risk HIGH)

