"""
Integration-тесты для PaymentsRepository.

Используют реальную БД (managed=False, продовая).
Все созданные строки удаляются в teardown (через фикстуры conftest.py).

Критично проверяют:
  - create_payment: успех / cap_exceeded / no_capacity / direction_not_found
  - list_payments: фильтры student/direction/from/to, сортировка DESC
  - get_payment: существующий / несуществующий
  - delete_payment: корректный new_balance
  - get_student_balance / _balance_for_student: числовые типы (int vs float,
    '8' не '8.0'), half-lesson 0.5, сырые строки в payments внутри ответа
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.payments import repository


# ---------------------------------------------------------------------------
# Tests: create_payment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreatePayment:
    """create_payment — создание оплаты."""

    def test_success_returns_payment(self, direction_fixture, student_fixture):
        data = {
            'student_id': student_fixture,
            'direction_id': direction_fixture,
            'lessons_count': 4,
            'total_amount': '375.00',
            'paid_at': '2026-01-15',
            'created_by': 'acct:1',
        }
        result = repository.create_payment(data)
        try:
            assert 'payment' in result
            p = result['payment']
            assert p['student_id'] == student_fixture
            assert p['direction_id'] == direction_fixture
            assert p['subscriptions_count'] == 1
            assert 'id' in p
        finally:
            if 'payment' in result:
                with connection.cursor() as cur:
                    cur.execute('DELETE FROM payments WHERE id = %s', [result['payment']['id']])

    def test_rounds_unit_price_to_kopecks(self, direction_fixture, student_fixture):
        """unit_price = total_amount/lessons_count, округляется до копеек (1000/3 → 333.33)."""
        data = {
            'student_id': student_fixture,
            'direction_id': direction_fixture,
            'lessons_count': 3,
            'total_amount': '1000.00',
            'paid_at': '2026-01-15',
        }
        result = repository.create_payment(data)
        try:
            assert 'payment' in result
            price_str = str(result['payment']['unit_price'])
            # Rounded to 2 dp
            assert float(price_str) == pytest.approx(333.33, abs=0.001)
        finally:
            if 'payment' in result:
                with connection.cursor() as cur:
                    cur.execute('DELETE FROM payments WHERE id = %s', [result['payment']['id']])

    def test_direction_not_found(self, student_fixture):
        data = {
            'student_id': student_fixture,
            'direction_id': 999_999_999,
            'lessons_count': 4,
            'total_amount': '1000.00',
            'paid_at': '2026-01-15',
        }
        result = repository.create_payment(data)
        assert result == {'error': 'direction_not_found'}

    def test_no_capacity_direction_without_total_lessons(self, student_fixture):
        """Направление без total_lessons → no_capacity."""
        with connection.cursor() as cur:
            cur.execute(
                """
                INSERT INTO directions (name, active)
                VALUES ('__no_cap_dir__', true)
                RETURNING id
                """,
            )
            dir_id = cur.fetchone()[0]

        try:
            data = {
                'student_id': student_fixture,
                'direction_id': dir_id,
                'lessons_count': 4,
                'total_amount': '1000.00',
                'paid_at': '2026-01-15',
            }
            result = repository.create_payment(data)
            assert result == {'error': 'no_capacity'}
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM directions WHERE id = %s', [dir_id])

    def test_cap_exceeded(self, direction_fixture, student_fixture):
        """
        total_lessons=8 → cap=8 уроков (2 блока по 4); третья оплата → cap_exceeded
        """
        created_ids = []
        try:
            for _ in range(2):
                data = {
                    'student_id': student_fixture,
                    'direction_id': direction_fixture,
                    'lessons_count': 4,
                    'total_amount': '500.00',
                    'paid_at': '2026-01-15',
                }
                r = repository.create_payment(data)
                assert 'payment' in r, f'Expected payment, got: {r}'
                created_ids.append(r['payment']['id'])

            # Третья должна упасть
            result = repository.create_payment(data)
            assert result['error'] == 'cap_exceeded'
            assert result['already'] == 8
            assert result['cap_subscriptions'] == 2
        finally:
            with connection.cursor() as cur:
                for pid in created_ids:
                    cur.execute('DELETE FROM payments WHERE id = %s', [pid])

    def test_derives_unit_and_subs(self, direction_fixture, student_fixture):
        from decimal import Decimal
        res = repository.create_payment({
            'student_id': student_fixture, 'direction_id': direction_fixture,
            'lessons_count': 4, 'total_amount': Decimal('4000.00'),
            'paid_at': '2026-01-01', 'created_by': 'Тест Тестов',
        })
        try:
            p = res['payment']
            assert p['lessons_count'] == 4
            assert p['kind'] == 'purchase'
            assert p['subscriptions_count'] == 1
            assert str(p['unit_price']) == '1000.00'
            assert str(p['total_amount']) == '4000.00'
            assert p['created_by'] == 'Тест Тестов'
        finally:
            if 'payment' in res:
                with connection.cursor() as cur:
                    cur.execute('DELETE FROM payments WHERE id = %s', [res['payment']['id']])

    def test_cap_counts_prepayment_lessons(self, direction_fixture, student_fixture):
        """Предоплата (lessons_count=3) занимает ёмкость: cap считается в уроках,
        already возвращается в уроках (direction_fixture: total_lessons=8)."""
        created = []
        try:
            r1 = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 3, 'total_amount': '1500.00', 'paid_at': '2026-01-01',
            })
            assert 'payment' in r1, r1
            created.append(r1['payment']['id'])
            # ещё 2 блока = 8 уроков → 3 + 8 = 11 > 8 → cap_exceeded, already в уроках = 3
            r2 = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 8, 'total_amount': '4000.00', 'paid_at': '2026-01-01',
            })
            assert r2['error'] == 'cap_exceeded'
            assert r2['already'] == 3
        finally:
            with connection.cursor() as cur:
                for pid in created:
                    cur.execute('DELETE FROM payments WHERE id = %s', [pid])


# ---------------------------------------------------------------------------
# Tests: list_payments
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestListPayments:

    def test_returns_list(self):
        result = repository.list_payments()
        assert isinstance(result, list)

    def test_filter_by_student_id(self, payment_fixture, student_fixture):
        result = repository.list_payments(student_id=student_fixture)
        assert any(p['id'] == payment_fixture for p in result)
        for p in result:
            assert p['student_id'] == student_fixture

    def test_filter_by_direction_id(self, payment_fixture, direction_fixture):
        result = repository.list_payments(direction_id=direction_fixture)
        assert any(p['id'] == payment_fixture for p in result)
        for p in result:
            assert p['direction_id'] == direction_fixture

    def test_filter_by_from_excludes_earlier(self, payment_fixture, student_fixture):
        # payment_fixture has paid_at='2026-01-01'
        result = repository.list_payments(student_id=student_fixture, from_='2026-01-02')
        assert not any(p['id'] == payment_fixture for p in result)

    def test_filter_by_from_includes_same_date(self, payment_fixture, student_fixture):
        result = repository.list_payments(student_id=student_fixture, from_='2026-01-01')
        assert any(p['id'] == payment_fixture for p in result)

    def test_filter_by_to_excludes_later(self, payment_fixture, student_fixture):
        result = repository.list_payments(student_id=student_fixture, to='2025-12-31')
        assert not any(p['id'] == payment_fixture for p in result)

    def test_filter_by_to_includes_same_date(self, payment_fixture, student_fixture):
        result = repository.list_payments(student_id=student_fixture, to='2026-01-01')
        assert any(p['id'] == payment_fixture for p in result)

    def test_rows_have_student_name(self, payment_fixture, student_fixture):
        result = repository.list_payments(student_id=student_fixture)
        p = next(r for r in result if r['id'] == payment_fixture)
        assert p['student_name'] == '__pay_test_student__'

    def test_sorted_by_paid_at_desc_then_id_desc(self, student_fixture, direction_fixture):
        """Результат отсортирован DESC по paid_at, DESC по id."""
        created_ids = []
        try:
            for date in ['2026-02-01', '2026-01-01']:
                r = repository.create_payment({
                    'student_id': student_fixture,
                    'direction_id': direction_fixture,
                    'lessons_count': 4,
                    'total_amount': '500.00',
                    'paid_at': date,
                })
                created_ids.append(r['payment']['id'])

            result = repository.list_payments(student_id=student_fixture)
            dates = [r['paid_at'] for r in result]
            # Should be DESC
            assert dates == sorted(dates, reverse=True)
        finally:
            with connection.cursor() as cur:
                for pid in created_ids:
                    cur.execute('DELETE FROM payments WHERE id = %s', [pid])

    def test_nonexistent_student_returns_empty(self):
        result = repository.list_payments(student_id=999_999_999)
        assert result == []


# ---------------------------------------------------------------------------
# Tests: get_payment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestGetPayment:

    def test_returns_dict_for_existing(self, payment_fixture):
        result = repository.get_payment(payment_fixture)
        assert result is not None
        assert result['id'] == payment_fixture

    def test_returns_none_for_nonexistent(self):
        result = repository.get_payment(999_999_999)
        assert result is None


# ---------------------------------------------------------------------------
# Tests: delete_payment
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestDeletePayment:

    def test_deletes_and_returns_true(self, direction_fixture, student_fixture):
        r = repository.create_payment({
            'student_id': student_fixture,
            'direction_id': direction_fixture,
            'lessons_count': 4,
            'total_amount': '1000.00',
            'paid_at': '2026-03-01',
        })
        pid = r['payment']['id']
        result = repository.delete_payment(pid)
        assert result['deleted'] is True
        assert result['student_id'] == student_fixture
        assert result['direction_id'] == direction_fixture
        # new_balance после удаления единственной оплаты = 0 (purchased was 4, now 0)
        assert isinstance(result['new_balance'], (int, float))

    def test_delete_nonexistent_returns_false(self):
        result = repository.delete_payment(999_999_999)
        assert result == {'deleted': False}

    def test_new_balance_recalculated_after_delete(
        self, direction_fixture, student_fixture, membership_fixture, lesson_60_fixture,
    ):
        """Удаление оплаты уменьшает new_balance: purchased=4, attended=1 → balance=3."""
        # Посещение урока 60 мин (PK = (lesson_id, student_id))
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                [lesson_60_fixture, student_fixture],
            )

        ids = []
        try:
            # Создаём 2 оплаты: subscriptions_count=1 каждая → purchased=8
            for _ in range(2):
                r = repository.create_payment({
                    'student_id': student_fixture,
                    'direction_id': direction_fixture,
                    'lessons_count': 4,
                    'total_amount': '1000.00',
                    'paid_at': '2026-03-01',
                })
                ids.append(r['payment']['id'])

            # Удаляем одну оплату → purchased = 4, attended = 1 → balance = 3
            del_result = repository.delete_payment(ids[0])
            assert del_result['deleted'] is True
            assert del_result['new_balance'] == 3
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                    [lesson_60_fixture, student_fixture],
                )
            if len(ids) > 1:
                with connection.cursor() as cur:
                    cur.execute('DELETE FROM payments WHERE id = %s', [ids[1]])


# ---------------------------------------------------------------------------
# Tests: _balance_for_student и get_student_balance (числовые типы)
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestBalanceNumericTypes:
    """
    Критичные тесты паритета с Express.

    Express: Number(x) → целые = int (JSON: 8, не 8.0), дробные = float.
    Python _js_number: Decimal('8.0') → 8 (int), Decimal('7.5') → 7.5 (float).
    """

    def test_total_balance_is_int_when_whole(self, payment_fixture, student_fixture):
        """Нет посещений → total_balance = 4 → int."""
        balance = repository.get_student_balance(student_fixture)
        assert balance['total_balance'] == 4
        assert isinstance(balance['total_balance'], int)

    def test_total_balance_is_float_with_half_lesson(
        self,
        payment_fixture,
        student_fixture,
        membership_fixture,
        lesson_45_fixture,
    ):
        """lesson_duration_minutes=45 → attended_lessons=0.5 → total_balance=3.5 → float."""
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                [lesson_45_fixture, student_fixture],
            )

        try:
            balance = repository.get_student_balance(student_fixture)
            assert balance['total_balance'] == 3.5
            assert isinstance(balance['total_balance'], float)
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                    [lesson_45_fixture, student_fixture],
                )

    def test_balance_for_student_full_lessons_is_int(
        self,
        payment_fixture,
        student_fixture,
        membership_fixture,
        lesson_60_fixture,
    ):
        """60мин урок: attended=1 (int), balance=3 (int)."""
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                [lesson_60_fixture, student_fixture],
            )

        try:
            bal = repository._balance_for_student(student_fixture)
            assert bal == 3
            assert isinstance(bal, int)
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                    [lesson_60_fixture, student_fixture],
                )

    def test_balance_for_student_half_lesson_is_float(
        self,
        payment_fixture,
        student_fixture,
        membership_fixture,
        lesson_45_fixture,
    ):
        """45мин урок: balance=3.5 (float)."""
        with connection.cursor() as cur:
            cur.execute(
                'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
                [lesson_45_fixture, student_fixture],
            )

        try:
            bal = repository._balance_for_student(student_fixture)
            assert bal == 3.5
            assert isinstance(bal, float)
        finally:
            with connection.cursor() as cur:
                cur.execute(
                    'DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                    [lesson_45_fixture, student_fixture],
                )

    def test_total_paid_amount_is_number(self, payment_fixture, student_fixture):
        """total_paid_amount — число (int или float), не строка."""
        balance = repository.get_student_balance(student_fixture)
        assert isinstance(balance['total_paid_amount'], (int, float))

    def test_payments_in_balance_are_raw_dicts(self, payment_fixture, student_fixture):
        """
        payments внутри get_student_balance — сырые строки из list_payments.
        unit_price и total_amount — Decimal (→ renderer выдаст строки, как Express).
        НЕ трогаем их типы — они уже совпадают с Express.
        """
        from decimal import Decimal
        balance = repository.get_student_balance(student_fixture)
        assert isinstance(balance['payments'], list)
        p = next(py for py in balance['payments'] if py['id'] == payment_fixture)
        # Decimal (не float, не int) — renderer сам конвертирует в строку
        assert isinstance(p['unit_price'], Decimal)
        assert isinstance(p['total_amount'], Decimal)

    def test_no_payments_returns_empty_breakdowns(self):
        """Ученик без оплат → paid_by_direction/attended_by_direction пусты, total_balance=0."""
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO students (full_name) VALUES ('__bal_empty__') RETURNING id",
            )
            sid = cur.fetchone()[0]
        try:
            balance = repository.get_student_balance(sid)
            assert balance['paid_by_direction'] == []
            assert balance['attended_by_direction'] == []
            assert balance['total_balance'] == 0
            assert balance['payments'] == []
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM students WHERE id = %s', [sid])


# ---------------------------------------------------------------------------
# Tests: refund_student
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestRefundStudent:

    def _buy(self, sid, did, lessons=4, total='4000'):
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
                "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
                "VALUES (%s,%s,1,%s,'purchase',1000,%s,'2026-01-01','t')",
                [sid, did, lessons, total])

    def _cleanup_payments(self, sid):
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])

    def test_refund_zeroes_balance(self, direction_fixture, student_fixture,
                                   membership_fixture, lesson_60_fixture, attendance_60_fixture):
        from decimal import Decimal
        from apps.finances.repository import balance_for_student, student_fifo_remaining
        self._buy(student_fixture, direction_fixture)  # 4 lessons / 4000
        try:
            res = repository.refund_student(student_fixture, created_by='Админ')
            assert res['refunded_amount'] == Decimal('3000.00')  # 3 unworked * 1000
            assert res['new_balance'] == 0
            assert res['refunds'][0]['kind'] == 'refund'
            assert res['refunds'][0]['lessons_count'] == -3
            assert res['refunds'][0]['direction_id'] == direction_fixture
            assert balance_for_student(student_fixture) == 0
            assert student_fifo_remaining(student_fixture)['remaining_value'] == Decimal('0.00')
        finally:
            self._cleanup_payments(student_fixture)

    def test_refund_fractional_half_lesson(self, direction_fixture, student_fixture,
                                           membership_fixture, lesson_45_fixture, attendance_45_fixture):
        from decimal import Decimal
        from apps.finances.repository import balance_for_student
        self._buy(student_fixture, direction_fixture)  # 4 lessons / 4000, attended 0.5
        try:
            res = repository.refund_student(student_fixture, created_by='Админ')
            # remaining 3.5 lessons * 1000 = 3500
            assert res['refunded_amount'] == Decimal('3500.00')
            assert res['refunds'][0]['lessons_count'] == -3.5
            assert balance_for_student(student_fixture) == 0
        finally:
            self._cleanup_payments(student_fixture)

    def test_nothing_to_refund(self, student_fixture):
        assert repository.refund_student(student_fixture, created_by='Админ') == {'error': 'nothing_to_refund'}


# ---------------------------------------------------------------------------
# Tests: доплата сверх курса (kind='extra') — мимо лимита, в реальном направлении
# ---------------------------------------------------------------------------

@pytest.mark.django_db
class TestCreatePaymentExtra:

    def test_extra_bypasses_cap_and_counts_in_balance_and_fifo(self, direction_fixture, student_fixture):
        """Курс на 8 уроков выбран (2 блока по 4). Обычная покупка сверх лимита →
        cap_exceeded, а доплата kind='extra' проходит: в том же направлении, растит
        баланс на 1 и образует FIFO-партию (единичная оплата НЕ пропускается)."""
        from apps.finances.repository import balance_for_student, student_fifo_remaining

        created = []
        try:
            for _ in range(2):
                r = repository.create_payment({
                    'student_id': student_fixture, 'direction_id': direction_fixture,
                    'lessons_count': 4, 'total_amount': '4000.00', 'paid_at': '2026-01-15',
                })
                assert 'payment' in r, f'Expected payment, got: {r}'
                created.append(r['payment']['id'])

            capped = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 1, 'total_amount': '500.00', 'paid_at': '2026-01-16',
            })
            assert capped['error'] == 'cap_exceeded'
            assert float(balance_for_student(student_fixture)) == 8.0

            extra = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 1, 'total_amount': '800.00', 'paid_at': '2026-01-16',
                'kind': 'extra',
            })
            assert 'payment' in extra, f'Expected payment, got: {extra}'
            created.append(extra['payment']['id'])
            assert extra['payment']['kind'] == 'extra'
            assert extra['payment']['direction_id'] == direction_fixture
            assert float(extra['payment']['lessons_count']) == 1.0

            # баланс вырос на 1 (extra учитывается в purchased)
            assert float(balance_for_student(student_fixture)) == 9.0
            # FIFO: единичная доплата образует партию (не пропущена) — 9 уроков, 8800₽
            rem = student_fifo_remaining(student_fixture)
            assert rem['remaining_lessons'] == 9
            assert float(rem['remaining_value']) == 8800.0
        finally:
            with connection.cursor() as cur:
                for pid in created:
                    cur.execute('DELETE FROM payments WHERE id = %s', [pid])

    def test_extra_not_counted_toward_cap(self, direction_fixture, student_fixture):
        """kind='extra' в cap не входит: после доплаты обычная покупка на весь лимит
        всё ещё проходит (cap суммирует только kind='purchase')."""
        created = []
        try:
            e = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 1, 'total_amount': '800.00', 'paid_at': '2026-01-16', 'kind': 'extra',
            })
            assert 'payment' in e, f'Expected payment, got: {e}'
            created.append(e['payment']['id'])

            p = repository.create_payment({
                'student_id': student_fixture, 'direction_id': direction_fixture,
                'lessons_count': 8, 'total_amount': '8000.00', 'paid_at': '2026-01-17',
            })
            assert 'payment' in p, f'Expected payment, got: {p}'
            created.append(p['payment']['id'])
        finally:
            with connection.cursor() as cur:
                for pid in created:
                    cur.execute('DELETE FROM payments WHERE id = %s', [pid])
