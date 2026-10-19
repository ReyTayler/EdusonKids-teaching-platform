from unittest.mock import patch

import pytest
from django.db import connection
from django.db.models.functions import Now
from django.utils import timezone

from apps.payments.models import Payment
from apps.renewals import engine
from apps.renewals.models import RenewalActivity, RenewalDeal


@pytest.mark.django_db
def test_payment_orm_create_syncs_stage_without_closing(
        django_capture_on_commit_callbacks, make_student, make_direction,
        make_teacher, make_attendance):
    """
    Оплата пересчитывает авто-стадию (баланс вырос) — так же, как посещаемость.
    НЕ закрывает сделку и не спавнит следующий цикл: окончательное решение о
    продлении принимает менеджер вручную.
    """
    sid, did, tid = make_student(), make_direction(), make_teacher()
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
            "lesson_number_offset) "
            "VALUES ('__sig_group__', %s, %s, false, true, now(), 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
    engine.ensure_deal(sid, cycle_no=1)
    try:
        make_attendance(sid, gid, tid, count=1)  # 1 урок отработан, оплат ещё нет
        engine.sync_lesson_stage(sid)  # как после реальной отметки посещаемости
        deal = RenewalDeal.objects.get(student_id=sid, cycle_no=1)
        assert deal.stage.key == 'awaiting_payment'  # баланс <= 0

        pay = None
        try:
            with django_capture_on_commit_callbacks(execute=True) as callbacks:
                # lessons_count — источник баланса (balance_for_student), а не
                # total_amount/subscriptions_count: без него баланс не меняется.
                pay = Payment.objects.create(
                    student_id=sid, direction_id=did, subscriptions_count=1,
                    lessons_count=8, unit_price=500, total_amount=4000,
                    paid_at='2026-07-08', created_at=Now())
            assert len(callbacks) >= 1
            deal.refresh_from_db()
            assert deal.stage.key == 'lesson_1'  # баланс положительный → «Урок 1» (1 урок отработан)
            assert deal.outcome_at is None  # НЕ закрыта
            assert not RenewalDeal.objects.filter(student_id=sid, cycle_no=2).exists()
        finally:
            if pay is not None:
                pay.delete()
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_refund_does_not_touch_deal(django_capture_on_commit_callbacks,
                                    make_student, make_direction):
    """kind='refund' (возврат) не должен закрывать сделку и не должен
    запускать пересчёт стадии вовсе."""
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    pay = None
    try:
        with django_capture_on_commit_callbacks(execute=True):
            pay = Payment.objects.create(
                student_id=sid, direction_id=did, subscriptions_count=None,
                lessons_count=-4, kind='refund', unit_price=0, total_amount=-4000,
                paid_at='2026-07-12', created_at=Now())
        deal = RenewalDeal.objects.get(student_id=sid, cycle_no=1)
        assert deal.outcome_at is None
        assert deal.stage.key == 'no_lesson_yet'  # не сдвинулась
    finally:
        if pay is not None:
            pay.delete()


@pytest.mark.django_db
def test_payment_delete_reopens_legacy_payment_linked_deal(
        django_capture_on_commit_callbacks, make_student, make_direction):
    """
    Оплата больше не закрывает сделку сама, но исторические сделки, закрытые
    ДО этого изменения (activity kind='payment_linked' хранит payment_id),
    удаление такой оплаты по-прежнему корректно переоткрывает.
    """
    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    pay = Payment.objects.create(
        student_id=sid, direction_id=did, subscriptions_count=1, lessons_count=4,
        unit_price=4000, total_amount=4000, paid_at='2026-07-12', created_at=Now())
    # имитируем «историческое» закрытие оплатой (до удаления auto-close)
    RenewalDeal.objects.filter(id=deal.id).update(outcome_at=timezone.now())
    RenewalActivity.objects.create(
        deal=deal, kind='payment_linked', payment_id=pay.id, body='legacy')

    with django_capture_on_commit_callbacks(execute=True):
        pay.delete()

    deal.refresh_from_db()
    assert deal.outcome_at is None


@pytest.mark.django_db
def test_payment_create_survives_crm_failure(django_capture_on_commit_callbacks,
                                             make_student, make_direction):
    """Сбой во вторичной CRM-логике (синхронизация стадии) НЕ должен ронять
    создание денежной оплаты."""
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    pay = None
    try:
        with patch('apps.renewals.engine.sync_lesson_stage', side_effect=RuntimeError('boom')):
            # колбэк выполнится на выходе из with — исключение проглатывается
            # внутри sync_lesson_stage_safe, наружу не всплывает и оплату не откатывает.
            with django_capture_on_commit_callbacks(execute=True):
                pay = Payment.objects.create(
                    student_id=sid, direction_id=did, subscriptions_count=1, lessons_count=4,
                    unit_price=4000, total_amount=4000, paid_at='2026-07-08', created_at=Now())
        # оплата фактически создана
        assert Payment.objects.filter(id=pay.id).exists()
        # сделка не пострадала — осталась открытой в исходной стадии
        deal = RenewalDeal.objects.get(student_id=sid, cycle_no=1)
        assert deal.outcome_at is None
    finally:
        if pay is not None:
            pay.delete()
