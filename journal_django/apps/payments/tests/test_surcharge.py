"""
Доплата к абонементу (payments.kind='surcharge').

См. docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
Схема eduson_test общая — данные создаём и чистим сами.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import IntegrityError, connection, transaction


@pytest.fixture
def parent_payment(direction_fixture, student_fixture):
    """Оплата: 9 абонементов, 36 уроков, 44 000 ₽. Возвращает id."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO payments (student_id, direction_id, subscriptions_count,
                                  lessons_count, kind, unit_price, total_amount,
                                  paid_at, created_at)
            VALUES (%s, %s, 9, 36, 'purchase', 4888.89, 44000, DATE '2026-01-10', NOW())
            RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        pid = cur.fetchone()[0]
    yield pid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE parent_payment_id = %s', [pid])
        cur.execute('DELETE FROM payments WHERE id = %s', [pid])


@pytest.mark.django_db
def test_surcharge_row_is_stored(parent_payment, student_fixture, direction_fixture):
    """Доплата хранится без уроков, со ссылкой на оплату и номером абонемента."""
    from apps.payments.models import Payment
    s = Payment.objects.create(
        student_id=student_fixture, direction_id=direction_fixture,
        kind='surcharge', parent_payment_id=parent_payment, subscription_index=2,
        lessons_count=None, subscriptions_count=None,
        unit_price=Decimal('0'), total_amount=Decimal('1000'),
        paid_at='2026-02-10', created_at='2026-02-10T00:00:00+03:00',
    )
    s.refresh_from_db()
    assert s.lessons_count is None
    assert s.subscription_index == 2
    assert s.parent_payment_id == parent_payment


@pytest.mark.django_db
def test_surcharge_requires_parent(student_fixture, direction_fixture):
    """Доплата без родителя запрещена CHECK-констрейнтом."""
    from apps.payments.models import Payment
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                student_id=student_fixture, direction_id=direction_fixture,
                kind='surcharge', parent_payment_id=None, subscription_index=1,
                lessons_count=None, unit_price=Decimal('0'), total_amount=Decimal('1000'),
                paid_at='2026-02-10', created_at='2026-02-10T00:00:00+03:00',
            )


@pytest.mark.django_db
def test_purchase_cannot_have_parent(parent_payment, student_fixture, direction_fixture):
    """Родитель есть только у доплаты — обычная покупка с parent запрещена."""
    from apps.payments.models import Payment
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Payment.objects.create(
                student_id=student_fixture, direction_id=direction_fixture,
                kind='purchase', parent_payment_id=parent_payment, subscription_index=1,
                lessons_count=4, unit_price=Decimal('1000'), total_amount=Decimal('4000'),
                paid_at='2026-02-10', created_at='2026-02-10T00:00:00+03:00',
            )


@pytest.mark.django_db
def test_deleting_parent_cascades_surcharges(parent_payment, student_fixture, direction_fixture):
    """Удаление оплаты уносит её доплаты (db-каскад)."""
    from apps.payments.models import Payment
    Payment.objects.create(
        student_id=student_fixture, direction_id=direction_fixture,
        kind='surcharge', parent_payment_id=parent_payment, subscription_index=1,
        lessons_count=None, unit_price=Decimal('0'), total_amount=Decimal('500'),
        paid_at='2026-02-10', created_at='2026-02-10T00:00:00+03:00',
    )
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE id = %s', [parent_payment])
        cur.execute('SELECT COUNT(*) FROM payments WHERE parent_payment_id = %s', [parent_payment])
        assert cur.fetchone()[0] == 0


BASE_URL = '/api/admin/payments'


def _surcharge_payload(parent_id, index=2, amount='1000'):
    return {
        'kind': 'surcharge',
        'parent_payment_id': parent_id,
        'subscription_index': index,
        'total_amount': amount,
        'paid_at': '2026-02-10',
    }


@pytest.mark.django_db
def test_api_create_surcharge(admin_client, parent_payment, student_fixture):
    """Доплата создаётся, баланс уроков не меняется."""
    from apps.finances.repository import balance_for_student
    before = balance_for_student(student_fixture)

    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    resp = admin_client.post(BASE_URL, payload, format='json')

    assert resp.status_code == 201, resp.content
    assert resp.json()['kind'] == 'surcharge'
    assert resp.json()['lessons_count'] is None
    assert balance_for_student(student_fixture) == before


@pytest.mark.django_db
def test_api_surcharge_block_out_of_range(admin_client, parent_payment, student_fixture):
    """Номер абонемента больше, чем есть в оплате → 400."""
    payload = _surcharge_payload(parent_payment, index=99)
    payload['student_id'] = student_fixture
    resp = admin_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_api_surcharge_parent_of_another_student(admin_client, parent_payment):
    """Родитель другого ученика → 400 (иначе деньги уедут не туда)."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO students (full_name, created_at) "
                    "VALUES ('__sur_other__', NOW()) RETURNING id")
        other = cur.fetchone()[0]
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = other
    resp = admin_client.post(BASE_URL, payload, format='json')
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE student_id = %s', [other])
        cur.execute('DELETE FROM students WHERE id = %s', [other])
    assert resp.status_code == 400


@pytest.mark.django_db
def test_api_surcharge_does_not_consume_course_cap(admin_client, parent_payment,
                                                   student_fixture, direction_fixture):
    """Доплата не занимает лимит курса: сумма уроков направления не изменилась."""
    from django.db.models import Sum
    from apps.payments.models import Payment
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    admin_client.post(BASE_URL, payload, format='json')

    used = (Payment.objects
            .filter(student_id=student_fixture, direction_id=direction_fixture,
                    kind__in=('purchase', 'refund'))
            .aggregate(s=Sum('lessons_count'))['s'])
    assert used == 36


@pytest.mark.django_db
def test_manager_cannot_create_surcharge(manager_client, parent_payment, student_fixture):
    """RBAC: доплата — деньги, менеджеру запрещена (как и обычная оплата)."""
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    resp = manager_client.post(BASE_URL, payload, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_surcharge_is_folded_into_parent_payment_row(
    admin_client, parent_payment, student_fixture, group_fixture, teacher_id_fixture,
):
    """
    Доплата — не самостоятельное поступление: собственной партии она не образует,
    поэтому в реестре признания выручки видна колонкой «Доплаты» у своей
    родительской оплаты, а строки у самой доплаты нет.
    """
    from apps.finances.reports import collect_monthly_report
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    resp = admin_client.post(BASE_URL, payload, format='json')
    surcharge_id = resp.json()['id']
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s,%s,'2026-02-05',1,60,'group','test') RETURNING id",
            [group_fixture, teacher_id_fixture],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s,%s,true)',
            [lesson_id, student_fixture],
        )
    try:
        rows = collect_monthly_report('2026-02').rows
        row = next(r for r in rows if r.payment_id == parent_payment)
        assert row.surcharge_amount == Decimal('1000')
        assert not [r for r in rows if r.payment_id == surcharge_id]
        # Инвариант строки: сумма + доплаты = выручка + возврат + аванс.
        assert row.total_amount + row.surcharge_amount == (
            row.revenue_total + row.refunded + row.advance
        )
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


@pytest.mark.django_db
def test_surcharge_raises_refund_amount(admin_client, parent_payment, student_fixture):
    """Возврат считает остаток по подорожавшим ценам: доплата к неотработанному
    абонементу увеличивает сумму к возврату ровно на себя."""
    from apps.finances.repository import student_fifo_remaining
    before = student_fifo_remaining(student_fixture)['remaining_value']

    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    admin_client.post(BASE_URL, payload, format='json')

    after = student_fifo_remaining(student_fixture)['remaining_value']
    assert after - before == Decimal('1000')


def test_changelog_labels_for_surcharge_fields():
    """Новые поля подписаны по-русски, иначе журнал изменений покажет имена колонок."""
    from apps.changelog.summary import FIELD_RU
    assert FIELD_RU['parent_payment_id'] == 'доплата к оплате'
    assert FIELD_RU['subscription_index'] == 'номер абонемента'


# ---------------------------------------------------------------------------
# Регресс 2026-07-28: доплата доезжала до фронта без полей привязки, потому что
# list_payments отдаёт колонки по whitelist _PAYMENT_FIELDS. Итог — интерфейс не
# мог сопоставить доплату с абонементом и цена блока не пересчитывалась.
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_list_payments_exposes_surcharge_link(admin_client, parent_payment, student_fixture):
    """Список оплат отдаёт привязку доплаты — без неё фронт не найдёт её блок."""
    from apps.payments.repository import list_payments
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    assert admin_client.post(BASE_URL, payload, format='json').status_code == 201

    rows = list_payments(student_id=student_fixture)
    surcharge = next(r for r in rows if r['kind'] == 'surcharge')
    assert surcharge['parent_payment_id'] == parent_payment
    assert surcharge['subscription_index'] == 2


@pytest.mark.django_db
def test_student_balance_exposes_surcharge_link(admin_client, parent_payment, student_fixture):
    """То же на эндпоинте баланса — именно он питает историю платежей на карточке."""
    from apps.finances.balance import get_student_balance
    payload = _surcharge_payload(parent_payment)
    payload['student_id'] = student_fixture
    assert admin_client.post(BASE_URL, payload, format='json').status_code == 201

    balance = get_student_balance(student_fixture)
    surcharge = next(p for p in balance['payments'] if p['kind'] == 'surcharge')
    assert surcharge['parent_payment_id'] == parent_payment
    assert surcharge['subscription_index'] == 2
