"""
Возврат в работу сделок, ошибочно закрытых как «Продлён» на рубеже цикла.

Причина. `backfill_renewal_history` закрывает цикл, как только сумма посещений
достигла границы (`while total >= boundary`). Но правило раздела —
`cycle.open_cycle_no` — считает, что РОВНО на рубеже (attended кратно 4) цикл ещё
открыт: уроки отработаны, а решение «продлил / ушёл» не принято, и сделка должна
стоять на «Ждём продление». Два правила разошлись, поэтому ученик с количеством
уроков, кратным 4, получал последний цикл закрытым как «Продлён» и выпадал из
воронки: открытых сделок нет, в сводке он не виден.

Что делает команда: находит учеников без открытых сделок, у которых ПОСЛЕДНЯЯ
сделка закрыта в стадии «Продлён», и переоткрывает её штатной engine.reopen_deal —
outcome_at → NULL, стадия пересчитывается авто-правилом (_target_auto_stage).
Свою стадию не выставляем: правило одно на весь раздел, дублировать его здесь
нельзя. Для учеников на рубеже правило даёт «Ждём продление» (приоритет выше
«Ждём оплату», поэтому долг картину не меняет).

Изменения идут под pghistory-контекстом (RenewalDeal трекается) — попадают в
журнал изменений и откатываются оттуда.

    python manage.py reopen_stale_renewed_deals                      # разбор, без записи
    python manage.py reopen_stale_renewed_deals --apply              # записать всех найденных
    python manage.py reopen_stale_renewed_deals --students 91 138    # только этих учеников
    python manage.py reopen_stale_renewed_deals --students 91 --apply

`--students` берёт id из колонки «ученик» пробного прогона. Ученики вне списка не
затрагиваются, даже если попадают под критерий. Если указанный ученик под критерий
не подходит (у него есть открытая сделка или последняя закрыта не в «Продлён») —
команда об этом скажет и его пропустит.
"""
from __future__ import annotations

import pghistory
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.finances.repository import balance_for_student
from apps.renewals import engine
from apps.renewals.models import RenewalDeal
from apps.students.models import Student


class Command(BaseCommand):
    help = ('Переоткрывает последние сделки, закрытые как «Продлён» на рубеже цикла '
            '(ученик остался без открытой сделки).')

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Записать изменения. Без флага — только показать, что будет сделано.',
        )
        parser.add_argument(
            '--students', nargs='+', type=int, metavar='ID', default=None,
            help='ID учеников: обработать только их. Без флага — всех подходящих.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        only_students = options['students']
        targets = self._find_targets()

        if only_students is not None:
            requested = set(only_students)
            found = {d.student_id for d in targets}
            targets = [d for d in targets if d.student_id in requested]
            skipped = requested - found
            if skipped:
                self.stdout.write(self.style.WARNING(
                    f'Под критерий не подходят и пропущены: {sorted(skipped)}\n'
                    '(у такого ученика либо уже есть открытая сделка, либо последняя '
                    'закрыта не в «Продлён»)'))

        if not targets:
            self.stdout.write(self.style.SUCCESS('Таких сделок нет — всё в порядке.'))
            return

        names = dict(
            Student.objects.filter(id__in=[d.student_id for d in targets])
            .values_list('id', 'full_name')
        )
        active = set(
            Student.objects.filter(id__in=[d.student_id for d in targets],
                                   memberships__active=True)
            .values_list('id', flat=True)
        )

        self.stdout.write(f'\nНайдено учеников: {len(targets)}\n')
        self.stdout.write('  сделка  ученик                             id  цикл  уроков  баланс  активен  станет')

        plan = []
        for deal in sorted(targets, key=lambda d: names.get(d.student_id, '')):
            auto = engine._auto_stages(deal.pipeline)
            progress = engine._progress_stages(deal.pipeline)
            attended = engine._attended_total(deal.student_id)
            balance = float(balance_for_student(deal.student_id))
            stage, _matured = engine._target_auto_stage(deal, attended, balance, auto, progress)
            plan.append((deal, stage))
            self.stdout.write(
                f'  {deal.id:<7} {names.get(deal.student_id, "?")[:27]:<27} '
                f'{deal.student_id:<5} {deal.cycle_no:<5} {attended:<7} {balance:<7} '
                f'{"да" if deal.student_id in active else "нет":<8} '
                f'{stage.label if stage else "— правило не дало стадии"}'
            )

        self.stdout.write('\nИтого по стадиям:')
        for label, count in sorted(_count_by_stage(plan).items(), key=lambda kv: -kv[1]):
            self.stdout.write(f'  {label}: {count}')
        self.stdout.write(
            f'  из них активных учеников: {len([d for d, _ in plan if d.student_id in active])}')

        with transaction.atomic():
            with pghistory.context(
                operation='manual.reopen_stale_renewed',
                url='manual/reopen-stale-renewed',
                method='COMMAND',
                note='Сделка была закрыта как «Продлён» на рубеже цикла — возвращена в работу',
            ):
                reopened = sum(
                    1 for deal, _ in plan
                    if engine.reopen_deal(
                        deal.id, author_id=None,
                        note='Возврат в работу: цикл был закрыт как «Продлён» на рубеже, '
                             'хотя решение о продлении не принималось',
                    ) is not None
                )

            self.stdout.write(f'\nПереоткрыто сделок: {reopened} из {len(plan)}')

            # Контроль: у каждого затронутого ученика появилась открытая сделка.
            without_open = [
                d.student_id for d, _ in plan
                if not RenewalDeal.objects.filter(
                    student_id=d.student_id, outcome_at__isnull=True).exists()
            ]
            if without_open:
                raise RuntimeError(f'Без открытой сделки остались ученики: {without_open}')

            fact = {}
            for deal, _ in plan:
                deal.refresh_from_db()
                fact[deal.stage.label] = fact.get(deal.stage.label, 0) + 1
            self.stdout.write('Фактические стадии после переоткрытия:')
            for label, count in sorted(fact.items(), key=lambda kv: -kv[1]):
                self.stdout.write(f'  {label}: {count}')

            if not apply_changes:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    '\nПробный прогон — в базе ничего не изменилось. '
                    'Повторите с --apply, чтобы записать.'))
                return

        self.stdout.write(self.style.SUCCESS('\nЗаписано.'))

    @staticmethod
    def _find_targets() -> list[RenewalDeal]:
        """Последняя сделка ученика закрыта в «Продлён», открытых сделок нет."""
        open_students = set(
            RenewalDeal.objects.filter(outcome_at__isnull=True).values_list('student_id', flat=True)
        )
        targets, seen = [], set()
        rows = (RenewalDeal.objects.select_related('stage', 'pipeline')
                .exclude(student_id__in=open_students)
                .order_by('student_id', '-cycle_no', '-id'))
        for deal in rows:
            if deal.student_id in seen:
                continue
            seen.add(deal.student_id)      # первая строка на ученика = последняя сделка
            if deal.stage.key == 'renewed':
                targets.append(deal)
        return targets


def _count_by_stage(plan) -> dict:
    result = {}
    for _, stage in plan:
        label = stage.label if stage else '—'
        result[label] = result.get(label, 0) + 1
    return result
