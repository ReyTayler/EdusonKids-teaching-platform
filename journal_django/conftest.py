"""
Root conftest.py — центральные JWT-фикстуры для API-тестов.

Фаза 4 (architecture_v2.md): заменяет sentinel-мок (account_id=42, HMAC-cookie)
и точечный mock get_auth_state на реальную выдачу JWT через RefreshToken.for_user.

Глобальные фикстуры (доступны всем пакетам без импорта):
  - make_auth_client(account) → APIClient c JWT access-cookie
  - anon_client → APIClient без аутентификации
  - admin_client → APIClient с JWT для свежесозданного admin-аккаунта
  - manager_client → APIClient с JWT для свежесозданного manager-аккаунта
  - teacher_client → APIClient с JWT для свежесозданного teacher-аккаунта
  - superadmin_client → APIClient с JWT для superadmin
  - payment_balance_guard_on / _off → включить/выключить гард оплат
  - payroll_rates → версия ставок зарплаты на любую дату (групповой 500,
    индивидуальный 700, отмена 50 %, штраф выключен) — без неё движок
    начисляет 0 «нет ставки»
  - present_classmate → помощник (attendance, group_id) → attendance +
    пришедший одноклассник: урок без присутствующих не записывается

CSRF в тестах: CookieJWTAuthentication._enforce_csrf уважает
request._dont_enforce_csrf_checks. DRF APIClient c enforce_csrf_checks=False
ставит этот флаг → CSRF на мутациях НЕ мешает.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken


# ---------------------------------------------------------------------------
# Центральная JWT-фабрика (вызывается напрямую в тестах)
# ---------------------------------------------------------------------------

def make_auth_client(account) -> APIClient:
    """
    Создать APIClient с JWT access-cookie для django-модели Account.

    Токен несёт token_version из реального аккаунта (account.token_version).
    CookieJWTAuthentication.get_user сверяет это значение с БД — mismatch → 401.

    Пример:
        from conftest import make_auth_client
        client = make_auth_client(user_obj)
    """
    refresh = RefreshToken.for_user(account)
    refresh['token_version'] = account.token_version
    client = APIClient()  # enforce_csrf_checks=False по умолчанию
    cookie_name = settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')
    client.cookies[cookie_name] = str(refresh.access_token)
    return client


# ---------------------------------------------------------------------------
# Фикстура api_client_for — принимает Account-объект
# ---------------------------------------------------------------------------

@pytest.fixture
def api_client_for():
    """
    Фикстура-фабрика: api_client_for(account) → APIClient c JWT access-cookie.

    Пример:
        def test_something(account_obj, api_client_for):
            client = api_client_for(account_obj)
    """
    return make_auth_client


# ---------------------------------------------------------------------------
# Вспомогательные: создание реального Account в eduson_test
# ---------------------------------------------------------------------------

def _create_account(email: str, role: str, teacher_id=None) -> tuple[int, int | None]:
    """
    Создать аккаунт в eduson_test напрямую через SQL.
    Возвращает (account_id, teacher_id_if_created).
    """
    pw = make_password('testpass_sentinel')
    owned_teacher_id = None
    with connection.cursor() as cur:
        if role == 'teacher' and teacher_id is None:
            cur.execute(
                "INSERT INTO teachers (name) VALUES ('__sentinel_teacher__') RETURNING id"
            )
            teacher_id = cur.fetchone()[0]
            owned_teacher_id = teacher_id

        cur.execute(
            "INSERT INTO accounts "
            "(email, password, role, teacher_id, is_active, is_staff, is_superuser, "
            "first_name, last_name, token_version, date_joined) "
            "VALUES (%s, %s, %s, %s, true, false, false, '', '', 0, NOW()) RETURNING id",
            [email, pw, role, teacher_id],
        )
        acc_id = cur.fetchone()[0]
    return acc_id, owned_teacher_id


def _delete_account(acc_id: int, teacher_id=None) -> None:
    """Удалить аккаунт и опционально teacher из eduson_test."""
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM security_audit_log WHERE account_id = %s OR target_id = %s',
            [acc_id, acc_id],
        )
        cur.execute('DELETE FROM account_invites WHERE account_id = %s', [acc_id])
        cur.execute('DELETE FROM account_recovery_codes WHERE account_id = %s', [acc_id])
        cur.execute('DELETE FROM accounts WHERE id = %s', [acc_id])
        if teacher_id:
            cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


def _jwt_client_for_id(account_id: int) -> APIClient:
    """Загрузить Account из БД и создать JWT-клиент."""
    from apps.accounts.models import Account
    user = Account.objects.get(pk=account_id)
    return make_auth_client(user)


# ---------------------------------------------------------------------------
# Глобальные фикстуры ролей
# ---------------------------------------------------------------------------

@pytest.fixture
def anon_client() -> APIClient:
    """APIClient без аутентификации."""
    return APIClient()


@pytest.fixture
def admin_client(db):
    """
    APIClient с JWT для admin. Создаёт реальный аккаунт в eduson_test.
    Использует маркер db (из pytest-django) для разрешения доступа к БД.
    """
    acc_id, _ = _create_account('__root_admin__@test.local', 'admin')
    client = _jwt_client_for_id(acc_id)
    yield client
    _delete_account(acc_id)


@pytest.fixture
def manager_client(db):
    """APIClient с JWT для manager."""
    acc_id, _ = _create_account('__root_manager__@test.local', 'manager')
    client = _jwt_client_for_id(acc_id)
    yield client
    _delete_account(acc_id)


@pytest.fixture
def teacher_client(db):
    """APIClient с JWT для teacher."""
    acc_id, teacher_id = _create_account('__root_teacher__@test.local', 'teacher')
    client = _jwt_client_for_id(acc_id)
    yield client
    _delete_account(acc_id, teacher_id)


@pytest.fixture
def superadmin_client(db):
    """APIClient с JWT для superadmin (полный доступ к admin-платформе)."""
    acc_id, _ = _create_account('__root_superadmin__@test.local', 'superadmin')
    client = _jwt_client_for_id(acc_id)
    yield client
    _delete_account(acc_id)


# ---------------------------------------------------------------------------
# Гард оплат (settings.PAYMENT_BALANCE_GUARD)
# ---------------------------------------------------------------------------

@pytest.fixture
def payment_balance_guard_on(settings):
    """Включить гард «нельзя отметить ученика без оплаченных уроков».

    По умолчанию гард выключен: у Eduson оплаты в платформе не ведутся. Тесты,
    которые проверяют поведение KOTOKOD (блок по балансу, «записать в долг»,
    «Продлён» только при балансе > 0), подключают эту фикстуру — модулем через
    `pytest.mark.usefixtures('payment_balance_guard_on')`. Фикстура `settings`
    pytest-django откатывает значение после теста.
    """
    settings.PAYMENT_BALANCE_GUARD = True


@pytest.fixture
def payment_balance_guard_off(settings):
    """Явно выключить гард оплат (режим Eduson) — для тестов выключенного режима,
    чтобы они не зависели от значения в окружении разработчика."""
    settings.PAYMENT_BALANCE_GUARD = False


# ---------------------------------------------------------------------------
# Ставки зарплаты (apps/payroll: движок «Мотивации»)
# ---------------------------------------------------------------------------

@pytest.fixture
def payroll_rates(db):
    """Версия ставок школы, действующая на любую дату тестов: групповой урок 500,
    индивидуальный 700, отмена — 50 % от ставки, штраф выключен.

    Без версии движок начисляет 0 с пометкой «нет ставки» (спека §1.5) — тесты,
    проверяющие сумму за урок, подключают фикстуру явно (как
    payment_balance_guard_on). Чужие версии из общей тестовой БД убираем внутри
    транзакции теста: иначе более поздняя версия перехватила бы выбор
    «действующей на дату». Тест, которому важен штраф, правит возвращённую
    версию (penalty_enabled/…) и сохраняет её до записи урока.
    """
    import datetime
    from decimal import Decimal

    from apps.payroll.models import PayRateVersion

    PayRateVersion.objects.all().delete()
    return PayRateVersion.objects.create(
        effective_from=datetime.date(2000, 1, 1), group_rate=Decimal('500'),
        individual_rate=Decimal('700'), cancel_mode='percent', cancel_value=Decimal('50'),
        penalty_enabled=False,
    )


# ---------------------------------------------------------------------------
# Пришедший одноклассник для записи урока
# ---------------------------------------------------------------------------

@pytest.fixture
def present_classmate(db):
    """
    Помощник present_classmate(attendance, group_id) → attendance + пришедший
    одноклассник (новый ученик группы с оплатой — проходит и включённый гард
    оплат).

    Для тестов, которые пишут урок ради ОТСУТСТВИЯ основного ученика (пропуск,
    pending-резолюция, неоплачиваемый пропуск): урок без единого присутствующего
    ядро не записывает (спека 2026-10-20-eduson-motivation-payroll-design, §1.2).

    Заведённых одноклассников фикстура убирает в своём teardown — до закрытия
    тестовой транзакции: фикстуры приложений удаляют группы/направления сырым
    DELETE, и при проверке отложенных внешних ключей в конце теста оставшиеся
    членство и оплата одноклассника на них бы упали.
    """
    created: list[int] = []

    def add(attendance: list[dict], group_id: int) -> list[dict]:
        with connection.cursor() as cur:
            cur.execute("INSERT INTO students (full_name) VALUES ('__test_classmate__') "
                        "RETURNING id")
            student_id = cur.fetchone()[0]
            created.append(student_id)
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, 0, true)',
                [group_id, student_id],
            )
            cur.execute(
                """
                INSERT INTO payments (student_id, direction_id, subscriptions_count,
                                      lessons_count, unit_price, total_amount, paid_at,
                                      created_by)
                SELECT %s, direction_id, 2, 8, 1000, 8000, '2026-06-01', 'test'
                  FROM groups WHERE id = %s
                """,
                [student_id, group_id],
            )
        return [*attendance, {'student_id': student_id, 'present': True}]

    yield add

    if created:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE student_id = ANY(%s)', [created])
            cur.execute('DELETE FROM group_memberships WHERE student_id = ANY(%s)', [created])
            cur.execute('DELETE FROM payments WHERE student_id = ANY(%s)', [created])
            cur.execute('DELETE FROM students WHERE id = ANY(%s)', [created])
