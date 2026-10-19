"""
Сигналы renewals:
  • оплата (Payment) → пересчитать авто-стадию сделки (баланс вырос) — так
    же, как после посещаемости.

ВАЖНО: оплата НЕ закрывает сделку как «Продлён» и не спавнит следующий цикл.
Она только двигает сделку между авто-стадиями (Не было урока / Урок 1–3 / Ждём
оплату / Ждём продление) вместе с изменившимся балансом. Окончательное решение о продлении
принимает менеджер вручную — drag-в-зону или диалог на доске/в карточке
(repository.move_deal). Это осознанное решение (2026-07-13): раньше оплата
закрывала сделку автоматически, что путало менеджеров.

Оплаты в проде создаются через ORM (apps/payments/repository.create_payment →
Payment.objects.create), поэтому post_save отрабатывает штатно.

ВАЖНО: Payment — денежный immutable-инвариант. Синхронизация CRM-стадии —
вторичная фича и НЕ должна влиять на транзакцию оплаты. Поэтому:
  • вызов отложен на transaction.on_commit — выполнится ТОЛЬКО после успешного
    коммита оплаты (сбой CRM не откатит деньги);
  • sync_lesson_stage_safe сама проглатывает исключения с логированием —
    сбой синхронизации никогда не сломает денежный поток.
"""
from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver

from apps.payments.models import Payment
from apps.renewals import engine

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Payment, dispatch_uid='renewals_on_payment')
def on_payment_created(sender, instance: Payment, created: bool, **kwargs) -> None:
    """
    Оплата пересчитывает авто-стадию сделки ученика (баланс вырос). Возвраты
    (kind='refund') и легаси-строки без покупки не трогают воронку.
    """
    if not created or instance.kind != 'purchase':
        return

    student_id = instance.student_id
    transaction.on_commit(lambda: engine.sync_lesson_stage_safe(student_id))


@receiver(pre_delete, sender=Payment, dispatch_uid='renewals_on_payment_delete')
def on_payment_deleted(sender, instance: Payment, **kwargs) -> None:
    """
    Удаление оплаты переоткрывает сделку, которую эта оплата закрыла (если
    была) — деньги и CRM не должны расходиться.

    С 2026-07-13 оплата больше не закрывает сделку автоматически (см.
    on_payment_created), поэтому новых activity с kind='payment_linked' не
    появляется — для новых данных это фактически no-op. Сигнал оставлен ради
    исторических сделок, закрытых оплатой ДО этого изменения: удаление такой
    легаси-оплаты по-прежнему корректно переоткрывает связанную сделку.

    Именно pre_delete: FK renewal_activity.payment_id при удалении обнуляется
    (SET_NULL), поэтому deal_id ловим ДО удаления, а выполняем ПОСЛЕ коммита
    (on_commit) — с теми же гарантиями: сбой CRM не мешает денежной операции
    и проглатывается с логированием.
    """
    if instance.kind != 'purchase':
        return
    from apps.renewals.models import RenewalActivity
    deal_ids = list(
        RenewalActivity.objects
        .filter(payment_id=instance.id, kind='payment_linked',
                deal__outcome_at__isnull=False)
        .values_list('deal_id', flat=True))
    if not deal_ids:
        return
    payment_id = instance.id

    def _reopen() -> None:
        for deal_id in deal_ids:
            try:
                engine.reopen_deal(
                    deal_id, note=f'Оплата #{payment_id} удалена — сделка переоткрыта')
            except Exception:
                logger.exception(
                    'renewals: не удалось переоткрыть сделку %s после удаления оплаты %s',
                    deal_id, payment_id)

    transaction.on_commit(_reopen)
