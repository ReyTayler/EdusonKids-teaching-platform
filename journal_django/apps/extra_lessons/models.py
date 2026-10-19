"""
Models for extra_lessons — доп.уроки/резолюции пропусков, привязанные к
конкретному ученику, пропустившему основной (уже проведённый) урок.

AbsenceResolution — пер-ученик (1:1) «пропуск, требующий решения»: одна строка
на (пропущенный урок × ученик). pending (авто-создан по пропуску, ждёт решения)
→ makeup_scheduled (доп.урок назначен) → makeup_done (проведён, fact_lesson
заполнен). Отмена назначения / откат факта возвращают в pending (терминального
cancelled нет). Группа доп.урока отдельно не хранится — это всегда группа
missed_lesson. См.
docs/superpowers/specs/2026-07-18-unify-absences-makeup-burn-design.md.
"""
from __future__ import annotations

import pghistory
from django.db import models

PENDING = 'pending'
MAKEUP_SCHEDULED = 'makeup_scheduled'
MAKEUP_DONE = 'makeup_done'
BURNED = 'burned'
# waived — «неоплачиваемый пропуск, закрыт без денег»: терминальный статус БЕЗ
# факта-урока, БЕЗ списания баланса, БЕЗ выплаты.
# LEGACY / READ-ONLY: действие снято (эндпоинт /waive и services.waive удалены),
# новых waived-записей не создаётся. Статус остаётся в choices и в CHECK-констрейнте
# ради уже существующих записей — убрать его = сломать constraint на боевых данных.
WAIVED = 'waived'
STATUS_CHOICES = [PENDING, MAKEUP_SCHEDULED, MAKEUP_DONE, BURNED, WAIVED]

# kind: 'makeup' — отработка/сгорание по РЕАЛЬНОМУ пропуску (missed_lesson задан);
# 'extra' — доп.урок СВЕРХ курса, не привязан к пропуску (missed_lesson=NULL,
# группа берётся из поля group). См. lesson-outcomes-spec.
MAKEUP = 'makeup'
EXTRA = 'extra'
KIND_CHOICES = [MAKEUP, EXTRA]

# Совпадает с VALID_LESSON_DURATIONS admin-формы обычных уроков + 30 мин
# (доп.урок может быть короче группового занятия).
VALID_DURATIONS = (30, 45, 60, 90)


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class AbsenceResolution(models.Model):
    """
    Пер-ученик (1:1) «пропуск, требующий решения» — заменила групповую пару
    ExtraLessonAssignment+ExtraLessonParticipant. Одна строка на (пропущенный
    урок × ученик). Статусы: pending → makeup_scheduled → makeup_done ЛИБО
    pending → burned (сжечь). См.
    docs/superpowers/specs/2026-07-18-unify-absences-makeup-burn-design.md.
    """
    id = models.AutoField(primary_key=True)
    # missed_lesson: пропущенный урок для kind='makeup'; NULL для kind='extra'
    # (доп.урок сверх курса не привязан к пропуску — группа берётся из поля `group`).
    missed_lesson = models.ForeignKey('lessons.Lesson', on_delete=models.CASCADE,
                                      null=True, blank=True,
                                      related_name='absence_resolutions')
    # kind='extra' → берём группу отсюда (missed_lesson нет). Для makeup — NULL
    # (группа = missed_lesson.group).
    group = models.ForeignKey('groups.Group', on_delete=models.PROTECT,
                              null=True, blank=True, related_name='extra_resolutions')
    kind = models.CharField(max_length=16, default=MAKEUP, db_default=MAKEUP)
    # target_lesson_number: «за какой урок» проводится доп.урок сверх курса
    # (kind='extra') — выбирается менеджером при назначении, задаёт lesson_number
    # факта. NULL для makeup (там номер = missed_lesson.lesson_number) и для extra
    # без явного выбора (тогда record() берёт следующую позицию ученика в группе).
    target_lesson_number = models.DecimalField(max_digits=5, decimal_places=1,
                                               null=True, blank=True)
    student = models.ForeignKey('students.Student', on_delete=models.PROTECT,
                                related_name='absence_resolutions')
    assigned_teacher = models.ForeignKey('teachers.Teacher', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='absence_resolutions')
    scheduled_date = models.DateField(null=True, blank=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    duration_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, default=PENDING)
    fact_lesson = models.ForeignKey('lessons.Lesson', on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='absence_resolution_facts')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'absence_resolutions'
        indexes = [
            models.Index(fields=['status'], name='ar_status_idx'),
            models.Index(fields=['missed_lesson'], name='ar_missed_lesson_idx'),
            models.Index(fields=['assigned_teacher', 'scheduled_date'], name='ar_teacher_date_idx'),
            models.Index(fields=['student'], name='ar_student_idx'),
        ]
        constraints = [
            # Уникальный на (пропуск × ученик): РОВНО одна резолюция на реальный
            # пропуск. Для kind='extra' missed_lesson=NULL, а NULL в Postgres не
            # конфликтуют (NULLS DISTINCT) — несколько доп.уроков сверх курса на
            # одного ученика допустимы этим же constraint'ом.
            models.UniqueConstraint(fields=['missed_lesson', 'student'],
                                    name='absence_resolutions_missed_student_key'),
            models.CheckConstraint(name='absence_resolutions_status_check',
                                   condition=models.Q(status__in=STATUS_CHOICES)),
            models.CheckConstraint(name='absence_resolutions_kind_check',
                                   condition=models.Q(kind__in=KIND_CHOICES)),
            # Консистентность формы: makeup привязан к пропуску; extra — без
            # пропуска, но с группой (источник направления/группы доп.урока).
            models.CheckConstraint(
                name='absence_resolutions_kind_shape',
                condition=(
                    (models.Q(kind=MAKEUP) & models.Q(missed_lesson__isnull=False))
                    | (models.Q(kind=EXTRA) & models.Q(missed_lesson__isnull=True)
                       & models.Q(group__isnull=False))
                ),
            ),
        ]
