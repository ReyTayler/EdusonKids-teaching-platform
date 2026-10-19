"""
GET /api/my-payroll — зарплата преподавателя за месяц с расшифровкой.

Главное, что проверяем: скоуп. Эндпоинт обязан отдавать ТОЛЬКО строки того
преподавателя, чей JWT предъявлен, и не иметь способа попросить чужие.
"""
from __future__ import annotations

import pytest
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

URL = '/api/my-payroll'

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Фикстуры: преподаватель + его аккаунт + группа + уроки со строками payroll
# ---------------------------------------------------------------------------

def _jwt_client(account_id: int) -> APIClient:
    from apps.accounts.models import Account
    user = Account.objects.get(pk=account_id)
    refresh = RefreshToken.for_user(user)
    refresh['token_version'] = user.token_version
    client = APIClient()
    client.cookies[settings.SIMPLE_JWT.get('AUTH_COOKIE', 'access')] = str(refresh.access_token)
    return client


@pytest.fixture
def own_account(teacher_id_fixture):
    """Аккаунт role=teacher, привязанный к teacher_id_fixture."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO accounts (email, password, role, teacher_id, is_active, is_staff,
                                  is_superuser, first_name, last_name, token_version, date_joined)
            VALUES ('__mypayroll__@test.local', %s, 'teacher', %s, true, false, false,
                    '', '', 0, NOW())
            RETURNING id
            """,
            [make_password('x'), teacher_id_fixture],
        )
        account_id = cur.fetchone()[0]
    yield account_id
    with connection.cursor() as cur:
        cur.execute('DELETE FROM accounts WHERE id = %s', [account_id])


@pytest.fixture
def client(own_account):
    return _jwt_client(own_account)


@pytest.fixture
def make_lesson(group_fixture, teacher_id_fixture):
    """Фабрика «урок + строка payroll». Убирает за собой всё созданное."""
    created: list[int] = []

    def _make(
        date='2026-07-03', payment='800.00', penalty='0', total=5, present=4,
        duration=60, lesson_type='regular', teacher_id=None, number=1,
    ):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                "lesson_duration_minutes, lesson_type, submitted_by_token) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'test') RETURNING id",
                [group_fixture, teacher_id or teacher_id_fixture, date, number,
                 duration, lesson_type],
            )
            lesson_id = cur.fetchone()[0]
            cur.execute(
                "INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, "
                "payment, penalty) VALUES (%s, %s, %s, %s, %s, %s)",
                [lesson_id, teacher_id or teacher_id_fixture, total, present, payment, penalty],
            )
        created.append(lesson_id)
        return lesson_id

    yield _make

    with connection.cursor() as cur:
        for lesson_id in created:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.fixture
def other_teacher():
    """Второй преподаватель — чужие строки, которые не должны попасть в выдачу."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name, active) VALUES ('__pr_other__', true) RETURNING id"
        )
        tid = cur.fetchone()[0]
    yield tid
    with connection.cursor() as cur:
        # Свои уроки убираем сами: порядок разрушения фикстур не гарантирует,
        # что make_lesson отработал раньше, а FK этого не простит.
        cur.execute(
            'DELETE FROM payroll WHERE lesson_id IN '
            '(SELECT id FROM lessons WHERE teacher_id = %s)', [tid],
        )
        cur.execute('DELETE FROM lessons WHERE teacher_id = %s', [tid])
        cur.execute('DELETE FROM teachers WHERE id = %s', [tid])


# ---------------------------------------------------------------------------
# Доступ
# ---------------------------------------------------------------------------

def test_anonymous_is_rejected(anon_client):
    assert anon_client.get(f'{URL}?month=2026-07').status_code in (401, 403)


def test_admin_role_is_rejected(admin_client):
    """Эндпоинт кабинета преподавателя, не админский раздел."""
    assert admin_client.get(f'{URL}?month=2026-07').status_code == 403


def test_only_own_payroll_is_returned(client, make_lesson, other_teacher):
    """Строки чужого преподавателя не попадают в выдачу."""
    make_lesson(date='2026-07-03', payment='800.00')
    make_lesson(date='2026-07-04', payment='999.00', teacher_id=other_teacher, number=2)

    body = client.get(f'{URL}?month=2026-07').json()

    assert body['totals']['lessons'] == 1
    assert body['totals']['payment'] == '800.00'
    assert all(r['payment'] != '999.00' for r in body['rows'])


def test_teacher_id_in_query_is_ignored(client, make_lesson, other_teacher):
    """Подсунуть чужой teacher_id параметром нельзя — скоуп берётся из JWT."""
    make_lesson(date='2026-07-03', payment='800.00')
    make_lesson(date='2026-07-04', payment='999.00', teacher_id=other_teacher, number=2)

    body = client.get(f'{URL}?month=2026-07&teacher_id={other_teacher}').json()

    assert body['totals']['payment'] == '800.00'


# ---------------------------------------------------------------------------
# Период
# ---------------------------------------------------------------------------

def test_month_boundaries_are_inclusive(client, make_lesson):
    make_lesson(date='2026-07-01', payment='100.00', number=1)
    make_lesson(date='2026-07-31', payment='200.00', number=2)
    make_lesson(date='2026-06-30', payment='300.00', number=3)
    make_lesson(date='2026-08-01', payment='400.00', number=4)

    body = client.get(f'{URL}?month=2026-07').json()

    assert body['totals']['lessons'] == 2
    assert body['totals']['payment'] == '300.00'


def test_rows_are_newest_first(client, make_lesson):
    make_lesson(date='2026-07-03', number=1)
    make_lesson(date='2026-07-20', number=2)

    rows = client.get(f'{URL}?month=2026-07').json()['rows']

    assert [r['date'] for r in rows] == ['2026-07-20', '2026-07-03']


def test_empty_month_returns_zero_totals(client):
    body = client.get(f'{URL}?month=2026-01').json()

    assert body['rows'] == []
    assert body['totals'] == {
        'lessons': 0, 'presences': 0,
        'payment': '0.00', 'penalty': '0.00', 'net': '0.00',
    }


def test_month_defaults_to_current(client):
    """Без параметра — текущий месяц по МСК, а не ошибка."""
    from apps.core.utils.dates import msk_now

    body = client.get(URL).json()

    assert body['month'] == msk_now().strftime('%Y-%m')


def test_invalid_month_is_rejected(client):
    assert client.get(f'{URL}?month=2026-13').status_code == 400
    assert client.get(f'{URL}?month=июль').status_code == 400


def test_month_label_is_russian(client):
    assert client.get(f'{URL}?month=2026-07').json()['monthLabel'] == 'Июль 2026'


# ---------------------------------------------------------------------------
# Содержимое строки
# ---------------------------------------------------------------------------

def test_totals_equal_sum_of_rows(client, make_lesson):
    make_lesson(date='2026-07-03', payment='800.00', penalty='160.00', present=4, number=1)
    make_lesson(date='2026-07-10', payment='500.00', penalty='0', total=2, present=2, number=2)

    body = client.get(f'{URL}?month=2026-07').json()

    assert body['totals']['payment'] == '1300.00'
    assert body['totals']['penalty'] == '160.00'
    assert body['totals']['net'] == '1140.00'
    assert body['totals']['presences'] == 6


def test_row_carries_rule_breakdown(client, make_lesson):
    make_lesson(date='2026-07-03', payment='800.00', total=5, present=4)

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['rule']['code'] == 'per_student'
    assert row['rule']['text'] == '4 × 200 ₽'
    assert row['adjusted'] is False
    assert row['net'] == '800.00'


def test_row_carries_group_and_direction(client, make_lesson):
    make_lesson(date='2026-07-03')

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['group'] == '__pr_group__'
    assert row['direction'] == '__pr_dir__'


def test_penalty_is_explained(client, make_lesson):
    make_lesson(date='2026-07-03', payment='800.00', penalty='160.00', present=4)

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['net'] == '640.00'
    assert '40 ₽ × 4' in row['penaltyNote']


def test_no_penalty_leaves_note_empty(client, make_lesson):
    make_lesson(date='2026-07-03', penalty='0')

    assert client.get(f'{URL}?month=2026-07').json()['rows'][0]['penaltyNote'] is None


def test_manual_correction_is_flagged(client, make_lesson):
    """Админ переписал сумму руками — расшифровка не выдаёт её за формулу."""
    make_lesson(date='2026-07-03', payment='777.00', total=5, present=4)

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['adjusted'] is True
    assert row['rule']['code'] == 'adjusted'


def test_excluded_students_are_explained(client, make_lesson, student_fixture):
    """
    Исторический урок (до 2026-09-28): ученик занимался бесплатно, в оплате его
    нет (present_count=0 при одном присутствовавшем). Преподаватель должен видеть,
    почему.
    """
    lesson_id = make_lesson(date='2026-07-03', payment='0', total=1, present=0)
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s, %s, true, true)',
            [lesson_id, student_fixture],
        )

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['excludedNote'] == '1 ученик не учтён в оплате: бесплатное занятие'


def test_paid_free_student_is_not_explained_as_excluded(client, make_lesson, student_fixture):
    """
    Урок по правилу 2026-09-28: бесплатный ученик оплачен преподавателю
    (present_count=1) — подписи «не учтён в оплате» быть не должно.
    """
    lesson_id = make_lesson(date='2026-07-03', payment='500.00', total=1, present=1)
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s, %s, true, true)',
            [lesson_id, student_fixture],
        )

    row = client.get(f'{URL}?month=2026-07').json()['rows'][0]

    assert row['excludedNote'] is None


def test_lesson_without_payroll_row_is_absent(client, group_fixture, teacher_id_fixture):
    """Исторические уроки без строки payroll в зарплату не попадают."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, '2026-07-15', 9, 60, 'regular', 'test') RETURNING id",
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]
    try:
        assert client.get(f'{URL}?month=2026-07').json()['rows'] == []
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def test_query_count_does_not_grow_with_lessons(client, make_lesson):
    """
    N+1 не должно быть: контекст урока тянется join'ом, исключения из headcount —
    одним сгруппированным запросом на все уроки месяца.
    """
    make_lesson(date='2026-07-01', number=1)
    with CaptureQueriesContext(connection) as one_lesson:
        client.get(f'{URL}?month=2026-07')

    for i in range(2, 7):
        make_lesson(date=f'2026-07-0{i}', number=i)
    with CaptureQueriesContext(connection) as six_lessons:
        client.get(f'{URL}?month=2026-07')

    assert len(six_lessons) == len(one_lesson)
