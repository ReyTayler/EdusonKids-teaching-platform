"""
Инвалидация кэшей дашборда (реестр куратора + финансовая сводка) на мутациях.

Дашборд (read-model) САМ подписывается на изменения Payment/Lesson — write-домены
про кэши дашборда ничего не знают (правильное направление зависимостей). Сброс
откладывается на transaction.on_commit: срабатывает ТОЛЬКО после успешного
коммита, при откате кэш не трогается.

Финансовый кэш (спека 2026-07-13, фаза B): Payment (create/delete/refund) и
Lesson (submitLesson создаёт урок → post_save; правка даты/препода → post_save;
удаление → post_delete). Точечные правки посещаемости идут bulk-операциями БЕЗ
сигналов — их дрейф покрывает короткий TTL кэша (как принято для реестра).
"""
from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.dashboard.registry_service import invalidate_registry_cache
from apps.dashboard.services import invalidate_finance_cache
from apps.lessons.models import Lesson
from apps.payments.models import Payment


@receiver(post_save, sender=Payment, dispatch_uid='registry_invalidate_on_payment_save')
@receiver(post_delete, sender=Payment, dispatch_uid='registry_invalidate_on_payment_delete')
def invalidate_registry_on_payment_change(sender, **kwargs) -> None:
    """Баланс ученика изменился → сбросить кэш сводки после коммита транзакции."""
    transaction.on_commit(invalidate_registry_cache)


@receiver(post_save, sender=Payment, dispatch_uid='finance_invalidate_on_payment_save')
@receiver(post_delete, sender=Payment, dispatch_uid='finance_invalidate_on_payment_delete')
@receiver(post_save, sender=Lesson, dispatch_uid='finance_invalidate_on_lesson_save')
@receiver(post_delete, sender=Lesson, dispatch_uid='finance_invalidate_on_lesson_delete')
def invalidate_finance_on_money_change(sender, **kwargs) -> None:
    """Оплата или урок изменились → FIFO устарел → сменить генерацию кэша."""
    transaction.on_commit(invalidate_finance_cache)
