"""
prune_changelog — retention журнала изменений (спека §8: 12 месяцев).

Удаляет события старше --keep-months (месяц ≈ 30 дней) и контексты старше
порога, на которые не осталось ссылок. Запускать cron'ом на VPS (раз в сутки).

ВАЖНО: события удаления самих event-строк триггерами не пишутся (event-модели
не трекаются) — команда не порождает рекурсивной истории.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Exists, OuterRef
from django.utils import timezone
from pghistory.models import Context

from apps.changelog import registry


class Command(BaseCommand):
    help = 'Удалить события журнала изменений старше N месяцев (default 12).'

    def add_arguments(self, parser):
        parser.add_argument('--keep-months', type=int, default=12)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options['keep_months'] * 30)
        total = 0
        for model_label in registry.TRACKED:
            event_model = registry.event_model(model_label)
            deleted, _ = event_model.objects.filter(
                pgh_created_at__lt=cutoff).delete()
            total += deleted

        ctx_qs = Context.objects.filter(created_at__lt=cutoff)
        for model_label in registry.TRACKED:
            event_model = registry.event_model(model_label)
            ctx_qs = ctx_qs.exclude(Exists(
                event_model.objects.filter(pgh_context_id=OuterRef('pk'))))
        ctx_deleted, _ = ctx_qs.delete()

        self.stdout.write(self.style.SUCCESS(
            f'Удалено событий: {total}, контекстов: {ctx_deleted} '
            f'(старше {options["keep_months"]} мес).'))
