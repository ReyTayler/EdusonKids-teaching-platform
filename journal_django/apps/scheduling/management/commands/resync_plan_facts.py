"""
Снять сдвиг нумерации между планом курса и фактами в одной группе.

Зачем. При первичном заполнении плана позиции раскладывались по датам занятий, а
номера в самих уроках шли своим порядком. Если в середине оказывалась «лишняя»
позиция (день не по расписанию, дубль номера), все последующие факты садились на
позицию с номером на единицу больше. Преподаватель видит в календаре номер, не
совпадающий с номером в журнале урока, а освободившаяся позиция висит
непроведённой (ПИ316, №26 — типичный случай).

Что делает: приводит привязки к инварианту «номер факта = номер позиции».
  • каждый факт вешается на позицию со своим номером;
  • плановая дата позиции становится датой факта (позиция описывает прошедшее занятие);
  • позиции, которым факта не досталось, освобождаются и остаются в плане впереди.

Чего НЕ делает: не создаёт и не удаляет ни уроки, ни позиции, не меняет номера в
самих уроках. Если фактам не находится позиции с их номером — команда отказывается
работать целиком, чтобы не чинить наполовину.

Здесь только разбор аргументов, вывод и pghistory-контекст: сама логика живёт в
apps.scheduling.repository (plan_resync_diff / resync_plan_facts) и делится с
эндпоинтами /plan/health и /plan/resync — чинить в двух местах нечего.

Контекст ставит именно команда: в HTTP-пути его открывает ChangelogMiddleware, и
явная метка перебила бы правило plan.resync в журнале изменений.

    python manage.py resync_plan_facts --group 87            # разбор, без записи
    python manage.py resync_plan_facts --group 87 --apply    # записать
"""
from __future__ import annotations

import pghistory
from django.core.management.base import BaseCommand, CommandError

from apps.groups.models import Group
from apps.scheduling import repository, services
from apps.scheduling.exceptions import PlanResyncBlocked

# Подписи проверок слоя 3 — зеркало PLAN_HEALTH_CHECK_LABELS во фронте
# (admin-src/src/lib/labels.ts). Без них команда печатала сырые английские ключи.
_CHECK_RU = {
    'fact_without_position': 'занятие без позиции в курсе',
    'duplicate_dates': 'два занятия на одну дату',
    'duplicate_position_numbers': 'две позиции с одним номером урока',
}

_REASON_RU = {
    'no_position': 'нет позиции с таким номером',
    'locked_position': 'занятие стоит на перенесённой/отменённой позиции',
    'duplicate_fact_number': 'два занятия с одним номером',
}


class Command(BaseCommand):
    help = 'Приводит привязки план↔факт группы к правилу «номер факта = номер позиции».'

    def add_arguments(self, parser):
        parser.add_argument('--group', type=int, required=True, metavar='ID',
                            help='ID группы.')
        parser.add_argument('--apply', action='store_true',
                            help='Записать. Без флага — только показать, что изменится.')

    def handle(self, *args, **options):
        group_id = options['group']
        apply_changes = options['apply']

        group = Group.objects.filter(id=group_id).first()
        if group is None:
            raise CommandError(f'Группы {group_id} нет.')

        diff = repository.plan_resync_diff(group_id)
        if diff['blocked_by'] or diff['orphan_facts']:
            self._report_blocked(diff)
            raise CommandError('Ничего не изменено.')

        changes = diff['changes']
        self.stdout.write(f'\nГруппа {group.name} (id={group_id}): '
                          f'позиций изменится {len(changes)}')
        if not changes:
            self.stdout.write(self.style.SUCCESS('Всё уже согласовано — менять нечего.'))
            return

        self.stdout.write('\n  позиция  №     было: факт / дата         станет: факт / дата')
        for c in changes:
            self.stdout.write(
                f'  {c["position_id"]:<8} {str(c["lesson_number"]):<5} '
                f'{str(c["from"]["fact_lesson_id"]):<8} {c["from"]["scheduled_date"]}  ->  '
                f'{str(c["to"]["fact_lesson_id"]):<8} {c["to"]["scheduled_date"]}'
            )
        if diff['freed']:
            self.stdout.write('\nОсвобождаются (станут ближайшими занятиями):')
            for f in diff['freed']:
                self.stdout.write(f'  №{f["lesson_number"]} на {f["scheduled_date"]}')

        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                '\nПробный прогон — в базе ничего не изменилось. '
                'Повторите с --apply, чтобы записать.'))
            return

        with pghistory.context(
            operation='manual.resync_plan_facts',
            url=f'manual/resync-plan-facts/group/{group_id}',
            method='COMMAND',
            note=f'{group.name}: снят сдвиг нумерации плана относительно занятий',
        ):
            try:
                # expected=None: у команды нет рукопожатия с предпросмотром —
                # применяется то состояние, которое видно под локом.
                result = services.resync_plan(group_id, expected=None)
            except PlanResyncBlocked as exc:
                raise CommandError(str(exc))

        self.stdout.write(self.style.SUCCESS(
            f'\nЗаписано: позиций изменено {result["applied"]}, '
            f'освобождено {result["freed_count"]}.'))

    def _report_blocked(self, diff: dict) -> None:
        if diff['blocked_by']:
            names = [_CHECK_RU.get(key, key) for key in diff['blocked_by']]
            self.stdout.write(self.style.ERROR(
                'Сначала разберитесь с этим: ' + ', '.join(names)))
        for f in diff['orphan_facts']:
            self.stdout.write(self.style.ERROR(
                f'  урок {f["lesson_id"]} №{f["lesson_number"]} {f["lesson_date"]} — '
                f'{_REASON_RU.get(f["reason"], f["reason"])}'))
