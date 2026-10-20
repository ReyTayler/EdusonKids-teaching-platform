"""
Пересчёт начислений преподавателям по правилам «Мотивации»
(apps/payroll/engine.py — правила, apps/payroll/recalc.py — пересчёт по БД).

    python manage.py recalculate_payroll                      # вся история
    python manage.py recalculate_payroll --from 2026-09-01 --to 2026-09-30
    python manage.py recalculate_payroll --group 12 --group 15 --dry-run

Нужна после миграции данных и при ручной сверке. Ручные правки (is_manual)
сохраняются: у них обновляются только расчётные суммы. --dry-run считает и
пишет в той же транзакции, затем откатывает её — итог показывает, что изменится.
Вывод — только счётчики, без имён (ПДн).
"""
from __future__ import annotations

import datetime

import pghistory
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.payroll.recalc import recalculate


def _date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise CommandError(f'Неверная дата: "{value}". Ожидается ГГГГ-ММ-ДД.')


class Command(BaseCommand):
    help = 'Пересчитывает начисления за уроки и отмены по ставкам «Мотивации».'

    def add_arguments(self, parser):
        parser.add_argument('--from', dest='date_from', metavar='ГГГГ-ММ-ДД',
                            help='С даты урока/отмены включительно')
        parser.add_argument('--to', dest='date_to', metavar='ГГГГ-ММ-ДД',
                            help='По дату урока/отмены включительно')
        parser.add_argument('--group', dest='groups', type=int, action='append',
                            metavar='ID', help='Только эта группа (можно несколько раз)')
        parser.add_argument('--dry-run', action='store_true',
                            help='Посчитать и показать итог, ничего не сохраняя')

    def handle(self, *args, **opts):
        date_from = _date(opts['date_from']) if opts['date_from'] else None
        date_to = _date(opts['date_to']) if opts['date_to'] else None
        if date_from and date_to and date_from > date_to:
            raise CommandError('--from позже --to.')
        dry_run = opts['dry_run']

        # Контекст pghistory как у прочих команд (sync_eduson): изменения видны в
        # журнале одной операцией с пометкой, что это команда, а не запрос.
        with transaction.atomic(), pghistory.context(
                operation='payroll.recalculate', method='COMMAND',
                url='manual/recalculate-payroll', note='Пересчёт зарплат'):
            stats = recalculate(group_ids=opts['groups'],
                                date_from=date_from, date_to=date_to)
            if dry_run:
                transaction.set_rollback(True)

        prefix = 'DRY-RUN, ничего не сохранено. ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f"{prefix}Уроков: {stats['lessons']}, отмен: {stats['cancellations']}. "
            f"Изменено начислений: {stats['changed']} "
            f"(создано {stats['created']}, обновлено {stats['updated']}, "
            f"удалено {stats['deleted']})."
        ))
