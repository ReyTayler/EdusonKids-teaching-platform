"""Фикстуры renewals: создаём реальные строки в eduson_test, чистим в teardown."""
from __future__ import annotations

import pytest
from django.db import connection


@pytest.fixture
def make_student(db):
    ids = []

    def _make(full_name='__renew_test_student__'):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO students (full_name, created_at) "
                "VALUES (%s, now()) RETURNING id", [full_name])
            sid = cur.fetchone()[0]
        ids.append(sid)
        return sid

    yield _make
    # renewal_activity → renewal_deal FK создан Django как DEFERRABLE INITIALLY
    # DEFERRED, из-за чего ON DELETE CASCADE откладывается и валит FK на teardown.
    # Поэтому чистим activity явно перед сделками.
    with connection.cursor() as cur:
        for sid in ids:
            cur.execute(
                'DELETE FROM renewal_activity WHERE deal_id IN '
                '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM students WHERE id = %s', [sid])


@pytest.fixture
def make_direction(db):
    """Направление нужно только группам/оплатам — сделка теперь per ученик."""
    ids = []

    def _make(name='__renew_test_dir__', price='4000.00'):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO directions (name, active, subscription_price) "
                "VALUES (%s, true, %s) RETURNING id", [name, price])
            did = cur.fetchone()[0]
        ids.append(did)
        return did

    yield _make
    with connection.cursor() as cur:
        for did in ids:
            cur.execute('DELETE FROM directions WHERE id = %s', [did])


@pytest.fixture
def make_attendance(db):
    """
    N посещённых уроков ученику (строки lessons + lesson_attendance) — источник
    общей истории для цикла продления (та же основа, что и баланс в finances).
    """
    created: list[int] = []

    def _make(student_id: int, group_id: int, teacher_id: int, count: int = 1,
              duration: int = 60, start: str = '2026-06-01'):
        from datetime import date, timedelta
        base = date.fromisoformat(start)
        ids = []
        with connection.cursor() as cur:
            for i in range(count):
                cur.execute(
                    "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
                    "lesson_duration_minutes, lesson_type, submitted_by_token) "
                    "VALUES (%s,%s,%s,%s,%s,'regular','__renew_test__') RETURNING id",
                    [group_id, teacher_id, base + timedelta(days=i), i + 1, duration])
                lid = cur.fetchone()[0]
                cur.execute(
                    'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                    'VALUES (%s,%s,true)', [lid, student_id])
                created.append(lid)
                ids.append(lid)
        return ids

    yield _make
    with connection.cursor() as cur:
        for lid in created:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])


@pytest.fixture
def make_payment(db):
    """
    Оплата-purchase через ORM (баланс = SUM(lessons_count) − attendance).
    В обычных django_db-тестах on_commit не срабатывает, поэтому создание оплаты
    здесь НЕ закрывает сделку — удобно для тестов чистой прогрессии стадий.
    """
    from apps.payments.models import Payment
    ids = []

    def _make(student_id: int, direction_id: int | None = None, lessons: float = 8,
              total: str = '4000.00'):
        pay = Payment.objects.create(
            student_id=student_id, direction_id=direction_id,
            subscriptions_count=None, lessons_count=lessons, kind='purchase',
            unit_price=0, total_amount=total, paid_at='2026-07-01',
            created_at='2026-07-01T00:00:00Z')
        ids.append(pay.id)
        return pay.id

    yield _make
    from apps.payments.models import Payment as P
    with connection.cursor() as cur:
        for pid in ids:
            cur.execute('DELETE FROM renewal_activity WHERE payment_id = %s', [pid])
    P.objects.filter(id__in=ids).delete()


@pytest.fixture
def make_teacher(db):
    """Преподаватель для групп (groups.teacher_id — NOT NULL FK)."""
    ids = []

    def _make(name='__renew_test_teacher__'):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO teachers (name, created_at) VALUES (%s, now()) RETURNING id",
                [name])
            tid = cur.fetchone()[0]
        ids.append(tid)
        return tid

    yield _make
    with connection.cursor() as cur:
        for tid in ids:
            cur.execute('DELETE FROM teachers WHERE id = %s', [tid])
