"""
Models for lessons — managed=False, поверх существующей БД.

Таблицы:
  lessons            — урок (операционная запись посещаемости)
  lesson_attendance  — посещение (PK = lesson_id + student_id)

Схема из db/migrations/001_initial_schema.sql.
date-поля (lesson_date) хранятся как CharField(max_length=10) — защита от timezone
drift (та же стратегия, что services/db.js setTypeParser(1082, v => v)).
numeric-поля (lesson_number) — DecimalField, рендерер выдаёт строку с сохранением масштаба.
"""
from __future__ import annotations

import pghistory
from django.db import models

# Типы уроков — единый источник для всех потребителей (сериализаторы, план курса,
# членства). Держим здесь, рядом с моделью: раньше наборы дублировались literal'ами
# в apps.lessons.serializers, apps.lessons.services и apps.memberships.repository.
#
# COURSE_LESSON_TYPES — настоящие занятия курса. Только они занимают позицию плана
#   (apps.scheduling.repository.link_facts) и двигают прогресс группы.
# SYSTEM_LESSON_TYPES — факты, которыми владеет apps.extra_lessons: доп.урок
#   (отработка пропуска) и сгорание пропуска. Сгорание — денежное списание, занятия
#   не было; доп.урок идёт сверх сетки курса. Позицию плана не занимают, общим CRUD
#   /api/admin/lessons не правятся (см. services._assert_not_system_lesson).
COURSE_LESSON_TYPES = ('regular', 'substitution', 'reschedule')
SYSTEM_LESSON_TYPES = ('extra', 'burned')


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class Lesson(models.Model):
    """
    Урок. Соответствует таблице `lessons`.
    """

    id = models.AutoField(primary_key=True)
    # FK → groups(id) / teachers(id). NO ACTION в БД → DO_NOTHING (managed=False).
    group = models.ForeignKey(
        'groups.Group',
        on_delete=models.DO_NOTHING,
        db_column='group_id',
        related_name='lessons',
    )
    teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='teacher_id',
        related_name='lessons',
    )
    # FK → teachers(id), nullable (замена/подмена преподавателя).
    original_teacher = models.ForeignKey(
        'teachers.Teacher',
        on_delete=models.DO_NOTHING,
        db_column='original_teacher_id',
        related_name='lessons_as_original',
        null=True,
        blank=True,
    )
    lesson_date = models.DateField()
    # numeric(5,1) — поддержка half-lesson (0.5)
    lesson_number = models.DecimalField(max_digits=5, decimal_places=1)
    lesson_duration_minutes = models.IntegerField()
    lesson_type = models.TextField()
    record_url = models.TextField(null=True, blank=True)
    submitted_at = models.DateTimeField()
    submitted_by_token = models.TextField()
    # Суррогат «это та же самая отправка того же занятия» — см.
    # apps.lessons.submission_key. NULL у исторических записей и у системных
    # уроков (доп.урок/сгорание: их создаёт apps.extra_lessons напрямую через
    # insert_lesson, у них своя защита — лок статуса резолюции).
    submission_key = models.TextField(null=True, blank=True)

    class Meta:
        managed = True
        db_table = 'lessons'
        indexes = [
            models.Index(fields=['group', 'lesson_date'], name='lessons_group_date_idx'),
            models.Index(fields=['teacher', 'lesson_date'], name='lessons_teacher_date_idx'),
            models.Index(fields=['-lesson_date', '-id'], name='lessons_date_desc_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['lesson_date', 'group', 'lesson_number', 'submitted_by_token'],
                name='lessons_natural_key',
            ),
            # Частичный уникальный индекс: одна курсовая запись на ключ отправки
            # в пределах группы. Условие isnull=False оставляет исторические
            # строки и системные уроки вне ограничения.
            models.UniqueConstraint(
                fields=['group', 'submission_key'],
                condition=models.Q(submission_key__isnull=False),
                name='lessons_submission_key_unique',
            ),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class LessonAttendance(models.Model):
    """
    Посещение урока. Соответствует таблице `lesson_attendance`.

    Реальный PK — составной (lesson_id, student_id). Django не выражает составной
    PK напрямую; lesson помечен primary_key для удовлетворения ORM. Раздел работает
    через raw SQL — ORM-модель только для документации/inspectdb-паритета.
    """

    lesson = models.ForeignKey(
        Lesson,
        on_delete=models.CASCADE,
        db_column='lesson_id',
        related_name='attendance',
        primary_key=True,
    )
    # FK → students(id). NO ACTION в БД → DO_NOTHING (managed=False).
    student = models.ForeignKey(
        'students.Student',
        on_delete=models.DO_NOTHING,
        db_column='student_id',
        related_name='attendance',
    )
    present = models.BooleanField()
    # Исход «бесплатное занятие»: ученик присутствовал (present=true), но занятие
    # бесплатно для ученика: баланс не трогается (списание не идёт, в
    # FIFO-потребление не входит по флагу is_free, см. finances). Преподавателю
    # зарплата начисляется как за обычное присутствие (решение 2026-09-28; с
    # 2026-07-24 по 2026-09-27 не начислялась, те уроки не пересчитывались).
    # Прогресс курса идёт (lessons_done растёт),
    # сделка продления двигается (present=true). См.
    # docs/superpowers/specs/2026-07-23-lesson-outcomes-spec.md, исход «Бесплатное занятие».
    is_free = models.BooleanField(default=False, db_default=False)
    # Исход «неоплачиваемый пропуск»: ученик этот урок НЕ посещает (перевели /
    # начал не с 1-го урока). present=false + unpaid_skip=true. Терминально: денег
    # ноль, из зарплаты преподавателя исключён, в очередь «ждёт решения» (пропуск,
    # требующий отработки/сжигания) НЕ попадает. Ставится вручную по каждому
    # ученику, в т.ч. на уже проведённом уроке. Отличается от обычного «не был»
    # (present=false, unpaid_skip=false), который порождает pending-резолюцию.
    # См. docs/superpowers/specs/2026-07-23-lesson-outcomes-spec.md.
    unpaid_skip = models.BooleanField(default=False, db_default=False)

    class Meta:
        managed = True
        db_table = 'lesson_attendance'
        unique_together = (('lesson', 'student'),)
        indexes = [
            models.Index(fields=['student'], name='lesson_attendance_student_idx'),
        ]


@pghistory.track(
    pghistory.InsertEvent(),
    pghistory.UpdateEvent(),
    pghistory.DeleteEvent(),
)
class LessonSkip(models.Model):
    """
    Пометка «неоплачиваемый пропуск» на СЛОТ группы (group × student × lesson_number),
    НЕЗАВИСИМО от того, проведён ли урок этого слота. Нужна, чтобы ставить пропуск на
    ЕЩЁ НЕ ПРОВЕДЁННЫЕ уроки (будущие слоты), где строки lesson_attendance ещё нет
    (её негде создать без даты). См. Вариант A в lesson-outcomes-spec.

    Семантика: ученик этот слот НЕ посещает (перевод / начал не с 1-го). Когда группа
    реально проведёт урок этого слота, record_lesson исключит помеченных учеников из
    посещаемости/зарплаты (материализует в lesson_attendance.unpaid_skip). На уже
    проведённом уроке пометка материализуется сразу. Денег ноль, pending не порождает.

    Трекается pghistory: раньше след действия писался в журнал ИБ через
    log_event('lesson_skip_set') на эндпоинте, но security_audit_log — журнал
    событий БЕЗОПАСНОСТИ. Пометка пропуска — доменное действие, поэтому её место
    в «Журнале изменений» (правило group.lesson_skip в apps/changelog/labels.py
    существовало и раньше, но без трекинга модели не давало ни одной записи).
    managed=True: таблицу создаёт Django-миграция.
    """
    id = models.AutoField(primary_key=True)
    group = models.ForeignKey('groups.Group', on_delete=models.CASCADE, related_name='lesson_skips')
    student = models.ForeignKey('students.Student', on_delete=models.CASCADE, related_name='lesson_skips')
    lesson_number = models.DecimalField(max_digits=5, decimal_places=1)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'lesson_skips'
        constraints = [
            models.UniqueConstraint(fields=['group', 'student', 'lesson_number'],
                                    name='lesson_skips_group_student_number_key'),
        ]
        indexes = [
            models.Index(fields=['group', 'lesson_number'], name='lesson_skips_group_num_idx'),
        ]
