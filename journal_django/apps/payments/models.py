"""
Models for payments — managed=False, поверх существующей БД.

Таблица:
  payments — финансовые записи оплат (immutable: только POST/DELETE)

Схема из db/migrations/008_payments.sql + 009_payments_legacy.sql, дополнена
Django-миграцией 0003 (2026-07-09): убран constraint payments_direction_count_match —
subscriptions_count теперь можно задать независимо от direction_id (легаси-оплаты
без направления тоже должны считаться в balance_for_student, который per-direction
скоуп не использует).
FK student_id/direction_id → ON DELETE RESTRICT (защита истории оплат от хард-удаления).
"""
from __future__ import annotations

import pghistory
from django.db import models


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Payment(models.Model):
    """
    Оплата. Соответствует таблице `payments`.

    Источник правды о количестве — lessons_count; subscriptions_count —
    презентационный; total_amount авторитетен, unit_price информационный.

    Инварианты БД (CHECK): kind ∈ {purchase, refund, extra, surcharge}; unit_price ≥ 0;
    purchase/extra → lessons_count > 0 и total_amount ≥ 0; refund → lessons_count < 0
    и total_amount ≤ 0.

    kind='extra' — доплата за доп.урок СВЕРХ курса (2026-07-23): деньги в реальном
    направлении, но МИМО лимита курса (`already + lessons_count > total_lessons` в
    create_payment этот вид не проверяет и в cap не считает — cap суммирует только
    kind='purchase'). Знаки — как у purchase (положительные). См. lesson-outcomes-spec.

    kind='surcharge' — доплата к уже купленному абонементу (блоку из 4 уроков)
    внутри существующей оплаты (2026-07-28): деньги без уроков (lessons_count=NULL),
    привязана к parent_payment + subscription_index. Баланс уроков не меняет, лимит
    курса не занимает — считается только в цене блока. См.
    docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
    """

    id = models.AutoField(primary_key=True)
    # FK → students(id), ON DELETE RESTRICT.
    student = models.ForeignKey(
        'students.Student',
        on_delete=models.RESTRICT,
        db_column='student_id',
        related_name='payments',
    )
    # FK → directions(id), ON DELETE RESTRICT, nullable (легаси-оплаты).
    direction = models.ForeignKey(
        'directions.Direction',
        on_delete=models.RESTRICT,
        db_column='direction_id',
        related_name='payments',
        null=True,
        blank=True,
    )
    subscriptions_count = models.IntegerField(null=True, blank=True)
    lessons_count = models.DecimalField(max_digits=12, decimal_places=1, null=True, blank=True)
    kind = models.TextField(default='purchase', db_default='purchase')
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)
    paid_at = models.DateField()
    note = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    created_by = models.TextField(null=True, blank=True)
    # Доплата к абонементу (kind='surcharge'): деньги без уроков, добивающие цену
    # уже купленного блока. parent_payment — та оплата, чей абонемент дорожает;
    # subscription_index — номер абонемента внутри неё (1-based). У остальных видов
    # оба поля NULL. См. docs/superpowers/specs/2026-07-28-course-surcharge-design.md.
    parent_payment = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        db_column='parent_payment_id',
        related_name='surcharges',
        null=True,
        blank=True,
    )
    subscription_index = models.IntegerField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'payments'
        indexes = [
            models.Index(fields=['student'], name='payments_student_idx'),
            models.Index(fields=['direction'], name='payments_direction_idx'),
            models.Index(fields=['paid_at'], name='payments_paid_at_idx'),
            models.Index(fields=['-paid_at', '-id'], name='payments_paid_at_desc_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                name='payments_kind_check',
                condition=models.Q(kind__in=['purchase', 'refund', 'extra', 'surcharge']),
            ),
            models.CheckConstraint(
                name='payments_unit_price_check',
                condition=models.Q(unit_price__gte=0),
            ),
            # purchase/extra: положительные количества и сумма; refund: отрицательные.
            # extra (доплата сверх курса) списывается/учитывается как обычная покупка,
            # только мимо лимита — поэтому те же знаковые правила, что у purchase.
            # lessons_count__isnull=False обязателен ЯВНО: `lessons_count > 0` на
            # NULL даёт NULL, а CHECK пропускает всё, кроме FALSE → без isnull-ветки
            # purchase с lessons_count=NULL молча прошёл бы и выпал из purchased
            # (balances_for_students SUM игнорит NULL) → завышенный «долг» без ошибки.
            models.CheckConstraint(
                name='payments_purchase_signs',
                condition=(
                    ~models.Q(kind__in=['purchase', 'extra'])
                    | (models.Q(lessons_count__isnull=False)
                       & models.Q(lessons_count__gt=0) & models.Q(total_amount__gte=0))
                ),
            ),
            models.CheckConstraint(
                name='payments_refund_signs',
                condition=(
                    ~models.Q(kind='refund')
                    | (models.Q(lessons_count__isnull=False)
                       & models.Q(lessons_count__lt=0) & models.Q(total_amount__lte=0))
                ),
            ),
            # Форма доплаты: деньги есть, уроков нет, родитель и номер блока обязательны.
            models.CheckConstraint(
                name='payments_surcharge_shape',
                condition=(
                    ~models.Q(kind='surcharge')
                    | (models.Q(lessons_count__isnull=True)
                       & models.Q(total_amount__gt=0)
                       & models.Q(parent_payment__isnull=False)
                       & models.Q(subscription_index__gte=1))
                ),
            ),
            # Родитель и номер блока бывают ТОЛЬКО у доплаты.
            models.CheckConstraint(
                name='payments_parent_only_surcharge',
                condition=(
                    models.Q(kind='surcharge')
                    | (models.Q(parent_payment__isnull=True)
                       & models.Q(subscription_index__isnull=True))
                ),
            ),
        ]
