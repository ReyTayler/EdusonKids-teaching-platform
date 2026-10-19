"""
Отметить ученика присутствовавшим, невзирая на отрицательный баланс.

Обычный путь (PATCH ячейки в интерфейсе) блокирует отметку платного присутствия у
ученика без оплаченных уроков — assert_students_paid. Иногда администратору нужно
провести отметку всё равно: занятие было, долг разбирается отдельно.

С 2026-08-25 суперадмину это доступно и из интерфейса: после отказа редактор
урока предлагает модалку «записать в долг» (флаг `allow_debt`). Команда осталась
для ролей ниже суперадмина и для работы без интерфейса.

Отличие от «бесплатного занятия» (is_free): здесь урок остаётся ПЛАТНЫМ — он
списывается с баланса (тот уйдёт глубже в минус) и попадает в зарплату
преподавателя. is_free означал бы «с ученика не списывается», а это другой смысл.

Что делает, кроме самой галочки (всё внутри штатного update_attendance_cell):
  • двигает прогресс ученика в группе (lessons_done);
  • пересчитывает зарплату за урок (present_count/payment);
  • обновляет стадию сделки продления.
Плюс снимает ставший беспредметным пропуск: если по этому уроку у ученика висела
нерешённая резолюция (pending — «ждёт отработки или сгорания»), она удаляется —
пропуска не было. Резолюцию с уже принятым решением (назначен доп.урок, проведён,
сожжён) команда не трогает и предупреждает: там задействованы деньги.

Всё под pghistory-контекстом — попадает в журнал изменений и откатывается оттуда.

    python manage.py mark_attendance_override --lesson 21475 --student 269
    python manage.py mark_attendance_override --lesson 21475 --student 269 --apply
"""
from __future__ import annotations

import pghistory
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.extra_lessons.models import PENDING, AbsenceResolution
from apps.lessons import repository
from apps.lessons.models import Lesson, LessonAttendance
from apps.memberships.models import GroupMembership
from apps.payroll.models import Payroll
from apps.students.models import Student


class Command(BaseCommand):
    help = 'Ставит присутствие ученику на уроке, игнорируя запрет по отрицательному балансу.'

    def add_arguments(self, parser):
        parser.add_argument('--lesson', type=int, required=True, metavar='ID')
        parser.add_argument('--student', type=int, required=True, metavar='ID')
        parser.add_argument('--apply', action='store_true',
                            help='Записать. Без флага — только показать, что изменится.')

    def handle(self, *args, **options):
        lesson_id, student_id = options['lesson'], options['student']
        apply_changes = options['apply']

        lesson = Lesson.objects.filter(id=lesson_id).values(
            'id', 'group_id', 'lesson_date', 'lesson_number', 'lesson_type').first()
        if lesson is None:
            raise CommandError(f'Урока {lesson_id} нет.')
        student = Student.objects.filter(id=student_id).values('id', 'full_name').first()
        if student is None:
            raise CommandError(f'Ученика {student_id} нет.')

        cell = LessonAttendance.objects.filter(
            lesson_id=lesson_id, student_id=student_id).values(
            'present', 'is_free', 'unpaid_skip').first()
        if cell and cell['present']:
            self.stdout.write(self.style.SUCCESS('Присутствие уже отмечено — менять нечего.'))
            return

        resolutions = list(
            AbsenceResolution.objects.filter(missed_lesson_id=lesson_id, student_id=student_id)
            .values('id', 'status')
        )
        decided = [r for r in resolutions if r['status'] != PENDING]
        if decided:
            raise CommandError(
                f'По этому пропуску уже принято решение: {decided}. Сначала откатите его '
                'в разделе «Доп.уроки» — иначе занятие спишется дважды.')

        member = GroupMembership.objects.filter(
            group_id=lesson['group_id'], student_id=student_id).values(
            'lessons_done', 'active').first()
        payroll = Payroll.objects.filter(lesson_id=lesson_id).values(
            'total_students', 'present_count', 'payment').first()

        self.stdout.write(
            f"\nУрок {lesson_id}: №{lesson['lesson_number']} от {lesson['lesson_date']}"
            f"\nУченик: {student['full_name']} (id={student_id})"
            f"\nСейчас: присутствие={cell['present'] if cell else 'строки нет'}, "
            f"прогресс={member['lessons_done'] if member else '—'}, "
            f"зарплата={payroll['payment'] if payroll else '—'} "
            f"({payroll['present_count'] if payroll else '—'} из "
            f"{payroll['total_students'] if payroll else '—'})"
        )
        if resolutions:
            self.stdout.write(f'Нерешённых пропусков по уроку: {len(resolutions)} — будут сняты')

        with transaction.atomic():
            with pghistory.context(
                operation='manual.mark_attendance_override',
                url=f'manual/mark-attendance/lesson/{lesson_id}/student/{student_id}',
                method='COMMAND',
                note='Присутствие отмечено вручную, невзирая на отрицательный баланс',
            ):
                ok = repository.update_attendance_cell(
                    lesson_id, student_id, present=True, is_free=False,
                    skip_balance_check=True,
                )
                if not ok:
                    raise CommandError('Урок не найден при записи.')
                # Пропуска не было — нерешённая резолюция беспредметна.
                AbsenceResolution.objects.filter(
                    missed_lesson_id=lesson_id, student_id=student_id, status=PENDING).delete()

            member_after = GroupMembership.objects.filter(
                group_id=lesson['group_id'], student_id=student_id).values(
                'lessons_done').first()
            payroll_after = Payroll.objects.filter(lesson_id=lesson_id).values(
                'total_students', 'present_count', 'payment').first()
            self.stdout.write(
                f"\nСтанет: присутствие=True, "
                f"прогресс={member_after['lessons_done'] if member_after else '—'}, "
                f"зарплата={payroll_after['payment'] if payroll_after else '—'} "
                f"({payroll_after['present_count'] if payroll_after else '—'} из "
                f"{payroll_after['total_students'] if payroll_after else '—'})"
            )

            if not apply_changes:
                transaction.set_rollback(True)
                self.stdout.write(self.style.WARNING(
                    '\nПробный прогон — в базе ничего не изменилось. '
                    'Повторите с --apply, чтобы записать.'))
                return

        self.stdout.write(self.style.SUCCESS('\nЗаписано.'))
