"""
Интеграция lessons → renewals: реальные точки записи посещаемости
(services.create_lesson_full → record_lesson, repository.update_attendance_cell,
repository.delete_lesson_full) должны подвинуть авто-стадию «Урок N» сделки
продления через transaction.on_commit(engine.sync_lesson_stage_safe(...)).

Проверяем именно вызов через продовый путь записи lessons (не сам engine
напрямую — это уже покрыто apps/renewals/tests/test_lesson_progress.py),
чтобы убедиться, что проводка реально подключена к продовому пути записи.
"""
from __future__ import annotations

import pytest

from apps.lessons import repository, services
from apps.renewals import engine
from apps.renewals.models import RenewalDeal


def _cleanup_deal(student_id: int) -> None:
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                    '(SELECT id FROM renewal_deal WHERE student_id = %s)', [student_id])
        cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [student_id])


def _make_payment(student_id: int, direction_id: int) -> int:
    """Оплата с положительным балансом: без неё движок уводит сделку в «Ждём оплату»."""
    from apps.payments.models import Payment
    return Payment.objects.create(
        student_id=student_id, direction_id=direction_id, subscriptions_count=None,
        lessons_count=8, kind='purchase', unit_price=0, total_amount='4000.00',
        paid_at='2026-07-01', created_at='2026-07-01T00:00:00Z').id


def _cleanup_payment(payment_id: int) -> None:
    from apps.payments.models import Payment
    Payment.objects.filter(id=payment_id).delete()


@pytest.mark.django_db
def test_create_lesson_full_advances_renewal_stage(
    django_capture_on_commit_callbacks, direction_fixture, group_fixture,
    teacher_id_fixture, student_fixture, membership_fixture,
):
    payment_id = _make_payment(student_fixture, direction_fixture)
    deal = engine.ensure_deal(student_fixture, cycle_no=1)
    assert deal.stage.key == 'no_lesson_yet'
    try:
        with django_capture_on_commit_callbacks(execute=True):
            result = services.create_lesson_full({
                'lesson_date': '2026-07-08',
                'group_id': group_fixture,
                'teacher_id': teacher_id_fixture,
                'lesson_number': 1,
                'lesson_duration_minutes': 60,
                'attendance': [{'student_id': student_fixture, 'present': True}],
            })
        lesson_id = result['lesson_id']
        deal.refresh_from_db()
        assert deal.stage.key == 'lesson_1'
    finally:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
        _cleanup_deal(student_fixture)
        _cleanup_payment(payment_id)


@pytest.mark.django_db
def test_update_attendance_cell_advances_renewal_stage(
    django_capture_on_commit_callbacks, direction_fixture, group_fixture,
    teacher_id_fixture, student_fixture, membership_fixture,
):
    payment_id = _make_payment(student_fixture, direction_fixture)
    deal = engine.ensure_deal(student_fixture, cycle_no=1)
    lesson_id = services.create_lesson_full({
        'lesson_date': '2026-07-08',
        'group_id': group_fixture,
        'teacher_id': teacher_id_fixture,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': False}],
    })['lesson_id']
    deal.refresh_from_db()
    assert deal.stage.key == 'no_lesson_yet'  # не отмечен присутствующим — прогресса нет
    try:
        with django_capture_on_commit_callbacks(execute=True):
            repository.update_attendance_cell(lesson_id, student_fixture, True)
        deal.refresh_from_db()
        assert deal.stage.key == 'lesson_1'
    finally:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
        _cleanup_deal(student_fixture)
        _cleanup_payment(payment_id)


@pytest.mark.django_db
def test_sync_ignored_when_no_open_deal(
    django_capture_on_commit_callbacks, direction_fixture, group_fixture,
    teacher_id_fixture, student_fixture, membership_fixture,
):
    """Без сделки продления (feature ещё не породила её) — просто no-op, без ошибок."""
    assert not RenewalDeal.objects.filter(student_id=student_fixture).exists()
    with django_capture_on_commit_callbacks(execute=True):
        lesson_id = services.create_lesson_full({
            'lesson_date': '2026-07-08',
            'group_id': group_fixture,
            'teacher_id': teacher_id_fixture,
            'lesson_number': 1,
            'lesson_duration_minutes': 60,
            'attendance': [{'student_id': student_fixture, 'present': True}],
        })['lesson_id']
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
