"""
Восстановление снимка направлений у сделок, заведённых до появления поля.

Зачем. Направления сделки читались вживую из АКТИВНЫХ членств в группах, поэтому
у закрытой сделки они менялись задним числом: ученика вывели из группы — и курс,
на котором шёл цикл, пропадал из истории. Поле `directions_snapshot` это чинит,
но заполняется оно только у сделок, тронутых после миграции.

История восстановима: направления цикла берутся из ПРОВЕДЁННЫХ УРОКОВ
(repository.cycle_direction_ids), а уроки, в отличие от членств, неизменны.
Поэтому команда честно доснимает старые сделки, а не переписывает их сегодняшним
состоянием ученика.

Трогаем только сделки БЕЗ снимка. Уже снятые не перезаписываем никогда: снимок
отвечает на вопрос «о каком курсе был этот цикл», и заново вычисленный ответ мог
бы разойтись с зафиксированным (например, после переоткрытия сделки).

Изменения идут под pghistory-контекстом (RenewalDeal трекается) — попадают в
журнал изменений и откатываются оттуда.

    python manage.py backfill_deal_directions            # разбор, без записи
    python manage.py backfill_deal_directions --apply    # записать
    python manage.py backfill_deal_directions --apply --only-closed   # только закрытые

По умолчанию берутся ВСЕ сделки без снимка, включая открытые: у открытой снимок
всё равно будет снят при первом же переходе, и лучше это сделать сейчас, пока
членства ещё активны, чем после вывода ученика из группы.
"""
from __future__ import annotations

import pghistory
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.renewals import repository as repo
from apps.renewals.models import RenewalDeal


class Command(BaseCommand):
    help = ('Заполняет directions_snapshot у сделок продления без снимка, '
            'восстанавливая направления цикла по проведённым урокам.')

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true',
                            help='записать изменения (без флага — пробный прогон)')
        parser.add_argument('--only-closed', action='store_true',
                            help='только закрытые сделки (outcome_at IS NOT NULL)')

    def handle(self, *args, **options):
        apply_changes: bool = options['apply']
        qs = RenewalDeal.objects.filter(directions_snapshot__isnull=True)
        if options['only_closed']:
            qs = qs.filter(outcome_at__isnull=False)
        deals = list(qs.order_by('student_id', 'cycle_no')
                     .values_list('id', 'student_id', 'cycle_no'))

        if not deals:
            self.stdout.write('Сделок без снимка направлений нет.')
            return

        from_lessons = from_memberships = empty = 0
        planned: list[tuple[int, list[int]]] = []
        for deal_id, student_id, cycle_no in deals:
            ids = repo.cycle_direction_ids(student_id, cycle_no)
            if ids:
                from_lessons += 1
            else:
                ids = repo.active_direction_ids(student_id)
                if ids:
                    from_memberships += 1
                else:
                    empty += 1
            planned.append((deal_id, ids))

        self.stdout.write(f'Сделок без снимка: {len(deals)}')
        self.stdout.write(f'  по урокам цикла:      {from_lessons}')
        self.stdout.write(f'  по активным членствам: {from_memberships}')
        self.stdout.write(f'  восстановить нечем:    {empty}')

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                'Пробный прогон — ничего не записано. Повторите с --apply.'))
            return

        # Контекст журнала изменений: массовая правка должна быть отличима от
        # действий менеджера и откатываема как одна операция.
        with pghistory.context(source='backfill_deal_directions'), transaction.atomic():
            for deal_id, ids in planned:
                RenewalDeal.objects.filter(
                    id=deal_id, directions_snapshot__isnull=True
                ).update(directions_snapshot=ids)

        self.stdout.write(self.style.SUCCESS(f'Готово: обновлено {len(planned)} сделок.'))
