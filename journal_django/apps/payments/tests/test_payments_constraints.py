"""
Тесты CHECK-constraints таблицы payments после снятия payments_direction_count_match
(2026-07-09): subscriptions_count теперь можно задать НЕЗАВИСИМО от direction_id —
легаси-оплаты (direction_id NULL) тоже должны считаться в глобальном балансе.
"""
from __future__ import annotations

import pytest
from django.db import IntegrityError, connection, transaction

from apps.core.tests.minimal_school import ensure_minimal_school

from apps.payments.models import Payment


def _make_student():
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) "
            "VALUES ('__constraint_test_student__') RETURNING id",
        )
        return cur.fetchone()[0]


@pytest.mark.django_db
class TestPaymentsDirectionCountConstraint:

    def test_subscriptions_count_without_direction_is_allowed(self):
        """Ключевой тест фикса: direction=NULL + subscriptions_count заданный — разрешено."""
        sid = _make_student()
        try:
            p = Payment.objects.create(
                student_id=sid, direction_id=None, subscriptions_count=2, lessons_count=8,
                unit_price='100.00', total_amount='200.00',
                paid_at='2026-01-01', created_at='2026-01-01T00:00:00Z',
            )
            assert p.id is not None
            assert p.direction_id is None
            assert p.subscriptions_count == 2
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])

    def test_purchase_zero_lessons_rejected(self):
        """payments_purchase_signs: purchase с lessons_count=0 (требуется >0) отклоняется."""
        sid = _make_student()
        try:
            with pytest.raises(IntegrityError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO payments (student_id, direction_id, kind, lessons_count, "
                        "unit_price, total_amount, paid_at, created_at) "
                        "VALUES (%s, NULL, 'purchase', 0, 100.00, 0.00, '2026-01-01', "
                        "'2026-01-01T00:00:00Z')",
                        [sid],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])

    def test_purchase_null_lessons_rejected(self):
        """payments_purchase_signs: purchase с lessons_count=NULL отклоняется —
        иначе оплата молча выпала бы из purchased (завышенный «долг»). Раньше CHECK
        пропускал NULL (`NULL > 0` = NULL, а CHECK валит только на FALSE)."""
        sid = _make_student()
        try:
            with pytest.raises(IntegrityError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO payments (student_id, direction_id, kind, lessons_count, "
                        "unit_price, total_amount, paid_at, created_at) "
                        "VALUES (%s, NULL, 'purchase', NULL, 100.00, 200.00, '2026-01-01', "
                        "'2026-01-01T00:00:00Z')",
                        [sid],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])

    def test_refund_positive_lessons_rejected(self):
        """payments_refund_signs: refund с положительным lessons_count (требуется <0) отклоняется."""
        sid = _make_student()
        try:
            with pytest.raises(IntegrityError), transaction.atomic():
                with connection.cursor() as cur:
                    cur.execute(
                        "INSERT INTO payments (student_id, direction_id, kind, lessons_count, "
                        "unit_price, total_amount, paid_at, created_at) "
                        "VALUES (%s, NULL, 'refund', 1, 100.00, 0.00, '2026-01-01', "
                        "'2026-01-01T00:00:00Z')",
                        [sid],
                    )
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])


import importlib

# Имя файла миграции начинается с цифры — не валидный идентификатор Python для
# обычного `import`, поэтому грузим модуль через importlib (сам Django точно так же
# динамически грузит файлы миграций). Ничего в apps/payments/migrations/__init__.py
# менять не нужно — импорт полностью локален для этого теста.
backfill_module = importlib.import_module(
    'apps.payments.migrations.0004_backfill_legacy_subscriptions_count'
)


@pytest.mark.django_db
class TestBackfillLegacySubscriptionsCount:

    def test_backfill_sets_one_for_null_direction_null_subs(self):
        """Легаси-строка (direction=NULL, subscriptions_count=NULL, unit_price=total_amount)
        после бэкафилла получает subscriptions_count=1."""
        sid = _make_student()
        try:
            with connection.cursor() as cur:
                # lessons_count задан (CHECK payments_purchase_signs требует NOT NULL);
                # тест про бэкафилл subscriptions_count (direction+subs=NULL → subs=1),
                # lessons_count на эту логику не влияет.
                cur.execute(
                    "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
                    "lessons_count, unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, NULL, NULL, 4, 9990.00, 9990.00, '2024-03-15', 'backfill-script') "
                    "RETURNING id",
                    [sid],
                )
                pid = cur.fetchone()[0]

            from django.apps import apps as global_apps
            backfill_module.backfill_subscriptions_count(global_apps, None)

            p = Payment.objects.get(id=pid)
            assert p.subscriptions_count == 1
            assert p.direction_id is None  # направление сознательно НЕ восстанавливаем
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])

    def test_backfill_does_not_touch_properly_tagged_payments(self):
        """Оплата с direction_id уже заданным (и subscriptions_count заданным) — не трогается."""
        sid = _make_student()
        did = ensure_minimal_school()['direction_id']
        try:
            with connection.cursor() as cur:
                # форк Eduson: тест раньше пропускался из-за пустой БД. INSERT отстал от
                # схемы: CHECK payments_purchase_signs требует lessons_count > 0 у purchase
                # (3 абонемента × 4 урока = 12, как пишет create_payment).
                cur.execute(
                    "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
                    "lessons_count, unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, %s, 3, 12, 1000.00, 3000.00, '2026-01-01', 'test') RETURNING id",
                    [sid, did],
                )
                pid = cur.fetchone()[0]

            from django.apps import apps as global_apps
            backfill_module.backfill_subscriptions_count(global_apps, None)

            p = Payment.objects.get(id=pid)
            assert p.subscriptions_count == 3  # не тронуто бэкафиллом
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])

    def test_backfilled_payment_counts_in_global_balance(self):
        """Легаси-оплата (direction NULL) считается в balance_for_student по lessons_count.

        Баланс теперь считается по lessons_count (не subscriptions_count), поэтому
        строка с lessons_count=4 даёт куплено=4 сразу; бэкафилл subscriptions_count —
        безобидный no-op для баланса, но не должен его сломать.
        """
        from apps.finances.repository import balance_for_student

        sid = _make_student()
        try:
            with connection.cursor() as cur:
                cur.execute(
                    "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
                    "lessons_count, unit_price, total_amount, paid_at, created_by) "
                    "VALUES (%s, NULL, NULL, 4, 9990.00, 9990.00, '2024-03-15', 'backfill-script')",
                    [sid],
                )

            assert balance_for_student(sid) == 4  # lessons_count=4, отработано 0

            from django.apps import apps as global_apps
            backfill_module.backfill_subscriptions_count(global_apps, None)

            assert balance_for_student(sid) == 4  # бэкафилл subscriptions_count не меняет баланс
        finally:
            with connection.cursor() as cur:
                cur.execute('DELETE FROM payments WHERE student_id = %s', [sid])
                cur.execute('DELETE FROM students WHERE id = %s', [sid])
