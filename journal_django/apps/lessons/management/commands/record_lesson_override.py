"""
Записать проведённое занятие, невзирая на отрицательный баланс учеников.

Обычная запись урока (кабинет преподавателя, admin API) блокируется, если у
присутствовавшего ученика нет оплаченных уроков. Иногда занятие всё же было, а
долг разбирается отдельно — нужен ручной путь для администратора.

С 2026-08-25 то же самое доступно суперадмину прямо из интерфейса: редактор
урока после отказа предлагает модалку «записать в долг» (флаг `allow_debt`,
apps/lessons/views.py). Команда осталась для случаев, когда интерфейс недоступен
или нужно записать пачку задним числом; поведение у них одно — `skip_balance_check`.

Занятие остаётся ПЛАТНЫМ: списывается с баланса (тот уйдёт глубже в минус) и
попадает в зарплату преподавателя. Это не «бесплатное занятие», где с ученика
не списывается ничего.

Номер урока не задаётся руками — считается тем же правилом, что в кабинете
преподавателя: «пройдено учениками группы + шаг курса» (у 45-минутных групп шаг
0.5). Так нумерация остаётся согласованной с планом; произвольный номер — прямой
путь к сдвигу, который потом приходится расчищать по всей школе.

Кто присутствовал: по умолчанию все активные ученики группы. --absent исключает
конкретных (они получат «не был» и нерешённый пропуск, как при обычной записи).

Всё под pghistory-контекстом — попадает в журнал изменений и откатывается оттуда.

    python manage.py record_lesson_override --group 2353 --date 2026-06-29
    python manage.py record_lesson_override --group 2353 --date 2026-06-29 --apply
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pghistory
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.groups.models import Group
from apps.lessons import services
from apps.lessons.models import Lesson
from apps.memberships.models import GroupMembership
from apps.students.models import Student


class Command(BaseCommand):
    help = 'Записывает занятие, игнорируя запрет по отрицательному балансу учеников.'

    def add_arguments(self, parser):
        parser.add_argument('--group', type=int, required=True, metavar='ID')
        parser.add_argument('--date', required=True, metavar='YYYY-MM-DD')
        parser.add_argument('--absent', nargs='*', type=int, default=[], metavar='ID',
                            help='ID учеников, которых не было (по умолчанию — были все).')
        parser.add_argument('--apply', action='store_true',
                            help='Записать. Без флага — только показать, что получится.')

    def handle(self, *args, **options):
        group_id, date_str = options['group'], options['date']
        absent = set(options['absent'])
        apply_changes = options['apply']

        try:
            lesson_date = datetime.date.fromisoformat(date_str)
        except ValueError:
            raise CommandError(f'Дата «{date_str}» не в формате YYYY-MM-DD.')

        group = Group.objects.filter(id=group_id).values(
            'id', 'name', 'teacher_id', 'lesson_duration_minutes').first()
        if group is None:
            raise CommandError(f'Группы {group_id} нет.')
        if group['teacher_id'] is None:
            raise CommandError('У группы не задан преподаватель.')

        clash = Lesson.objects.filter(group_id=group_id, lesson_date=lesson_date).values(
            'id', 'lesson_number', 'lesson_type').first()
        if clash:
            raise CommandError(
                f'На {lesson_date} в группе уже есть занятие: id={clash["id"]}, '
                f'№{clash["lesson_number"]} ({clash["lesson_type"]}). '
                'Второе занятие в тот же день заводится через интерфейс.')

        members = list(
            GroupMembership.objects.filter(group_id=group_id, active=True)
            .values('student_id', 'lessons_done')
        )
        if not members:
            raise CommandError('В группе нет активных учеников.')

        unknown = absent - {m['student_id'] for m in members}
        if unknown:
            raise CommandError(f'Эти ученики не состоят в группе: {sorted(unknown)}')

        names = dict(
            Student.objects.filter(id__in=[m['student_id'] for m in members])
            .values_list('id', 'full_name'))
        attendance = [
            {'student_id': m['student_id'], 'present': m['student_id'] not in absent}
            for m in members
        ]
        # Номер — тем же правилом, что в кабинете преподавателя: максимум пройденного
        # по ученикам группы + шаг курса (45 мин → полурока). Decimal, чтобы 12.0+0.5
        # не превратилось в 12.499999.
        step = Decimal('0.5') if group['lesson_duration_minutes'] == 45 else Decimal('1')
        done = max((Decimal(str(m['lessons_done'] or 0)) for m in members), default=Decimal('0'))
        lesson_number = done + step

        self.stdout.write(
            f"\nГруппа {group['name']} (id={group_id}), {group['lesson_duration_minutes']} мин"
            f"\nДата: {lesson_date} ({lesson_date:%a})"
            f"\nНомер урока: {lesson_number} (пройдено {done} + шаг {step})"
        )
        for a in attendance:
            mark = 'был' if a['present'] else 'НЕ был'
            self.stdout.write(f"  {names.get(a['student_id'], '?')}: {mark}")

        with transaction.atomic():
            with pghistory.context(
                operation='manual.record_lesson_override',
                url=f'manual/record-lesson/group/{group_id}/{lesson_date}',
                method='COMMAND',
                note='Занятие записано вручную, невзирая на отрицательный баланс',
            ):
                result = services.record_lesson(
                    lesson_date=date_str,
                    teacher_id=group['teacher_id'],
                    group_id=group_id,
                    original_teacher_id=None,
                    lesson_number=lesson_number,
                    lesson_duration_minutes=group['lesson_duration_minutes'],
                    lesson_type='regular',
                    record_url=None,
                    submitted_by_token='manual-override',
                    submit_date=date_str,
                    attendance=attendance,
                    skip_balance_check=True,
                )

            lesson = Lesson.objects.filter(id=result['lesson_id']).values(
                'id', 'lesson_number', 'lesson_date').first()
            self.stdout.write(
                f"\nЗаписано: урок id={lesson['id']}, №{lesson['lesson_number']}, "
                f"{lesson['lesson_date']}, зарплата {result.get('payment')}")
            for m in GroupMembership.objects.filter(
                    group_id=group_id, active=True).values('student_id', 'lessons_done'):
                self.stdout.write(
                    f"  {names.get(m['student_id'], '?')}: прогресс {m['lessons_done']}")

            if not apply_changes:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    '\nПробный прогон — в базе ничего не изменилось. '
                    'Повторите с --apply, чтобы записать.'))
                return

        self.stdout.write(self.style.SUCCESS('\nЗаписано.'))
