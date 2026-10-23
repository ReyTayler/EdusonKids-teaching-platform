"""
Причины отмены урока и записи об отменах (спека 2026-10-23).

Запись об отмене — отдельная сущность, а не поля маркера: маркер
PlannedLesson status=cancelled удаляют пересборка плана (reset_plan),
wipe_one_offs и мягкое обновление (sync_eduson). Причина и решение о
компенсации должны пережить маркер так же, как его переживает CancellationPay.

Обе модели в журнале изменений, но не откатываются (registry.py): запись —
снимок решения о деньгах преподавателя, справочник правит только superadmin.
"""
from __future__ import annotations

import pghistory
from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower


class CancellationSide(models.TextChoices):
    TEACHER = 'teacher', 'Преподаватель'
    CLIENT = 'client', 'Клиент'


_SIDES = [CancellationSide.TEACHER, CancellationSide.CLIENT]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class CancellationReason(models.Model):
    """Пункт справочника причин отмены одной стороны.

    «Другая причина» здесь не хранится: это постоянный последний пункт формы
    каждой стороны, компенсации по ней нет никогда (спека §3.1)."""

    id = models.BigAutoField(primary_key=True)
    side = models.CharField(max_length=16, choices=CancellationSide.choices)
    # Сервис хранит уже обрезанное имя; CHECK ниже ловит пустое в обход API.
    name = models.CharField(max_length=200)
    compensation = models.BooleanField(default=False)
    # Порядок в списке стороны; новая причина — в конец.
    position = models.IntegerField()
    # Мягкое удаление: из формы пропадает, а записи об отменах хранят снимок
    # текста и ссылку (FK PROTECT — жёстко удалить использованную нельзя).
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'cancellation_reasons'
        indexes = [
            # Список справочника (repository.list_active): WHERE deleted_at IS NULL
            # ORDER BY position, id — частичный индекс ровно под этот предикат и
            # порядок; удалённые причины копятся, но в форму не попадают.
            models.Index(fields=['position', 'id'], name='cancel_reason_order_idx',
                         condition=Q(deleted_at__isnull=True)),
        ]
        constraints = [
            models.CheckConstraint(name='cancel_reason_side_check',
                                   condition=Q(side__in=_SIDES)),
            models.CheckConstraint(name='cancel_reason_name_nonblank',
                                   condition=Q(name__regex=r'\S')),
            # Уникальность только среди активных: имя удалённой причины можно
            # завести заново. Без учёта регистра — «Заболел» и «заболел» в форме
            # неразличимы для человека.
            models.UniqueConstraint(F('side'), Lower('name'),
                                    condition=Q(deleted_at__isnull=True),
                                    name='cancel_reason_active_name_uq'),
        ]

    def __str__(self) -> str:
        return f'CancellationReason#{self.pk} ({self.side}: {self.name})'


# planned_lesson исключён из трекинга (решение В4 плана): коллектор Django при
# удалении маркера делает UPDATE … SET planned_lesson_id = NULL, а движок зарплат
# перепривязывает запись к новому маркеру. Будь поле отслеживаемым, эти
# технические UPDATE'ы попадали бы в операции перевода ученика и смены
# расписания и делали бы их неоткатываемыми (запись revertable=False).
@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent(),
                 exclude=['planned_lesson'])
class LessonCancellation(models.Model):
    """Запись об отмене занятия — неизменяемый снимок: сторона, причина (текст на
    момент отмены), дата уведомления и решение о компенсации.

    Естественный ключ — группа + lesson_date + scheduled_time, как у
    CancellationPay: по нему движок зарплат находит запись для пересозданного
    маркера, когда прежний удалён."""

    id = models.BigAutoField(primary_key=True)
    group = models.ForeignKey(
        'groups.Group', on_delete=models.CASCADE, db_column='group_id',
        related_name='lesson_cancellations',
        # Покрыт префиксом составного lesson_cancel_group_date_idx.
        db_index=False,
    )
    lesson_date = models.DateField()
    scheduled_time = models.TimeField()
    # Эффективный преподаватель маркера (замена учтена). NULL допустим (В2):
    # у занятия группы без преподавателя маркер тоже без него, и отмена такого
    # занятия не должна падать. PROTECT — преподавателей удаляют мягко.
    teacher = models.ForeignKey(
        'teachers.Teacher', on_delete=models.PROTECT, null=True, blank=True,
        db_column='teacher_id', related_name='lesson_cancellations',
    )
    # SET_NULL, не CASCADE: запись переживает маркер. ON DELETE SET NULL
    # продублирован в БД (миграция 0002, RunSQL) — маркеры чистят и сырым SQL.
    # ⚠️ AlterField этого поля пересоздаст FK без ON DELETE — после него нужна
    # повторная RunSQL (как payroll/0006, extra_lessons/0012).
    planned_lesson = models.OneToOneField(
        'scheduling.PlannedLesson', on_delete=models.SET_NULL, null=True, blank=True,
        db_column='planned_lesson_id', related_name='cancellation_record',
    )
    side = models.CharField(max_length=16, choices=CancellationSide.choices)
    # NULL — «Другая причина». PROTECT: причины удаляются только мягко.
    reason = models.ForeignKey(
        CancellationReason, on_delete=models.PROTECT, null=True, blank=True,
        db_column='reason_id', related_name='cancellations',
    )
    # Снимок: название причины на момент отмены либо текст «Другой причины».
    # Переименование причины в настройках прошлые записи не меняет.
    reason_text = models.CharField(max_length=1000)
    is_other = models.BooleanField()
    # Когда клиент сообщил об отмене; только для стороны «Клиент».
    notified_on = models.DateField(null=True, blank=True)
    # Снимок решения о компенсации на момент отмены (спека §4): позднейшая
    # правка флага причины прошлые отмены не меняет.
    compensated = models.BooleanField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        db_column='created_by_id', related_name='+',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'lesson_cancellations'
        indexes = [
            # Один индекс на два предиката (В7г): вкладка группы — новые сверху,
            # ORDER BY lesson_date DESC, scheduled_time DESC, id DESC; и равенство
            # по ключу (группа, дата, время) при привязке движком зарплат.
            models.Index(F('group'), F('lesson_date').desc(), F('scheduled_time').desc(),
                         F('id').desc(), name='lesson_cancel_group_date_idx'),
        ]
        constraints = [
            models.CheckConstraint(name='lesson_cancel_side_check',
                                   condition=Q(side__in=_SIDES)),
            models.CheckConstraint(
                name='lesson_cancel_notified_by_side',
                condition=Q(side=CancellationSide.CLIENT, notified_on__isnull=False)
                | Q(side=CancellationSide.TEACHER, notified_on__isnull=True),
            ),
            models.CheckConstraint(
                name='lesson_cancel_other_xor_reason',
                condition=Q(is_other=True, reason__isnull=True)
                | Q(is_other=False, reason__isnull=False),
            ),
            # «Другая причина» не компенсируется никогда (решение владельца).
            models.CheckConstraint(name='lesson_cancel_other_never_compensated',
                                   condition=~Q(is_other=True, compensated=True)),
            models.CheckConstraint(name='lesson_cancel_text_nonblank',
                                   condition=Q(reason_text__regex=r'\S')),
        ]

    def __str__(self) -> str:
        return (f'LessonCancellation#{self.pk} '
                f'(group={self.group_id}, {self.lesson_date} {self.scheduled_time})')
