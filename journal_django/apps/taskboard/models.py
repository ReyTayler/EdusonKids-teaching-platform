"""
Модели раздела «Задачи» — управляемые Django (managed=True), новые таблицы.

task_board    — воронка (произвольное число, заводит суперадмин).
task_stage    — стадия воронки; category ∈ {open, closed} — ЕДИНСТВЕННЫЙ источник
                истины о том, закрыта ли задача. Название и порядок — кастомные.
task          — карточка.
task_activity — лента карточки: смена стадии, смена исполнителя, комментарий, системное.

Признак «просрочена» НЕ хранится — выводится на чтении (due_date < сегодня AND
closed_at IS NULL).
"""
from __future__ import annotations

import pghistory
from django.db import models

from apps.core.db_fields import TolerantJSONField


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class TaskBoard(models.Model):
    id = models.BigAutoField(primary_key=True)
    name = models.TextField()
    description = models.TextField(null=True, blank=True)
    sort_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'task_board'
        constraints = [
            models.UniqueConstraint(fields=['name'], name='task_board_name_uq'),
        ]
        indexes = [
            models.Index(fields=['sort_order'], name='task_board_order_idx'),
        ]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class TaskStage(models.Model):
    class Category(models.TextChoices):
        OPEN = 'open', 'Открыта'
        CLOSED = 'closed', 'Закрыта'

    id = models.BigAutoField(primary_key=True)
    board = models.ForeignKey(
        TaskBoard, on_delete=models.CASCADE,
        db_column='board_id', related_name='stages',
    )
    label = models.TextField()
    color = models.TextField(null=True, blank=True)
    sort_order = models.IntegerField()
    # Стабильного машинного ключа (как RenewalStage.key) здесь НЕТ намеренно:
    # он в продлениях существует ради авто-правил движка, а движка у нас нет.
    category = models.CharField(max_length=6, choices=Category.choices)

    class Meta:
        managed = True
        db_table = 'task_stage'
        constraints = [
            models.UniqueConstraint(fields=['board', 'label'], name='task_stage_board_label_uq'),
            models.CheckConstraint(
                name='task_stage_category_check',
                condition=models.Q(category__in=['open', 'closed']),
            ),
            models.CheckConstraint(
                name='task_stage_color_check',
                condition=models.Q(color__isnull=True) | models.Q(color__regex=r'^#[0-9a-fA-F]{6}$'),
            ),
        ]
        indexes = [
            models.Index(fields=['board', 'sort_order'], name='task_stage_order_idx'),
        ]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class Task(models.Model):
    """
    Карточка задачи. id служит человекочитаемым номером «#20» в интерфейсе —
    отдельного поля номера нет намеренно (сквозной нумерации внутри воронки,
    как PROJ-12 в Jira, не делаем).

    Закрыта задача ⇔ её стадия имеет category='closed'. Отдельного флага
    «выполнено» нет: единственный источник истины — стадия.
    """
    class Priority(models.TextChoices):
        LOW = 'low', 'Низкий'
        NORMAL = 'normal', 'Обычный'
        HIGH = 'high', 'Высокий'

    class Resolution(models.TextChoices):
        DONE = 'done', 'Выполнено'
        CANCELLED = 'cancelled', 'Отменено'
        IRRELEVANT = 'irrelevant', 'Неактуально'

    id = models.BigAutoField(primary_key=True)
    board = models.ForeignKey(
        TaskBoard, on_delete=models.RESTRICT,
        db_column='board_id', related_name='tasks',
    )
    stage = models.ForeignKey(
        TaskStage, on_delete=models.RESTRICT,
        db_column='stage_id', related_name='tasks',
    )
    title = models.TextField()
    description = models.TextField(null=True, blank=True)
    # Исполнителей может быть несколько. Промежуточную таблицу pghistory не
    # трекает (модель автогенерируемая) — как и у меток, смена набора пишется
    # записью в TaskActivity вручную.
    assignees = models.ManyToManyField(
        'accounts.Account', db_table='task_assignee',
        related_name='assigned_tasks', blank=True,
    )
    created_by = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        db_column='created_by_id', related_name='created_tasks',
        null=True, blank=True,
    )
    # SET NULL на ученике и группе, а не RESTRICT: иначе старая задача
    # заблокировала бы удаление ученика или группы. История не теряется —
    # прежний student_id/group_id сохраняет pghistory в TaskEvent.
    student = models.ForeignKey(
        'students.Student', on_delete=models.SET_NULL,
        db_column='student_id', related_name='tasks',
        null=True, blank=True,
    )
    group = models.ForeignKey(
        'groups.Group', on_delete=models.SET_NULL,
        db_column='group_id', related_name='tasks',
        null=True, blank=True,
    )
    # Дата без времени: время тянет часовые пояса, а «позвонить в 15:00» — это
    # уже календарь, а не задачник.
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(
        max_length=6, choices=Priority.choices, default=Priority.NORMAL)
    resolution = models.CharField(
        max_length=10, choices=Resolution.choices, null=True, blank=True)
    stage_entered_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        managed = True
        db_table = 'task'
        constraints = [
            models.CheckConstraint(
                name='task_priority_check',
                condition=models.Q(priority__in=['low', 'normal', 'high']),
            ),
            models.CheckConstraint(
                name='task_resolution_check',
                condition=models.Q(resolution__isnull=True)
                | models.Q(resolution__in=['done', 'cancelled', 'irrelevant']),
            ),
            # closed_at и resolution заполняются строго вместе. Защита от
            # «полузакрытой» карточки, которую иначе легко создать мимо сервиса.
            models.CheckConstraint(
                name='task_closed_resolution_check',
                condition=models.Q(closed_at__isnull=True, resolution__isnull=True)
                | models.Q(closed_at__isnull=False, resolution__isnull=False),
            ),
        ]
        indexes = [
            models.Index(fields=['board', 'stage'], name='task_board_stage_idx'),
            models.Index(
                fields=['student'], name='task_student_idx',
                condition=models.Q(student__isnull=False),
            ),
            models.Index(
                fields=['due_date'], name='task_due_open_idx',
                condition=models.Q(closed_at__isnull=True),
            ),
        ]


@pghistory.track(pghistory.InsertEvent(), pghistory.UpdateEvent(), pghistory.DeleteEvent())
class TaskActivity(models.Model):
    class Kind(models.TextChoices):
        STAGE_CHANGE = 'stage_change', 'Смена стадии'
        ASSIGN = 'assign', 'Смена исполнителя'
        COMMENT = 'comment', 'Комментарий'
        SYSTEM = 'system', 'Системная запись'

    id = models.BigAutoField(primary_key=True)
    task = models.ForeignKey(
        Task, on_delete=models.CASCADE,
        db_column='task_id', related_name='activity',
    )
    kind = models.CharField(max_length=12, choices=Kind.choices)
    author = models.ForeignKey(
        'accounts.Account', on_delete=models.SET_NULL,
        db_column='author_id', related_name='task_activity',
        null=True, blank=True,
    )
    text = models.TextField(null=True, blank=True)
    # TolerantJSONField, а не JSONField: apps.core.apps регистрирует
    # register_default_jsonb, и psycopg2 отдаёт уже готовый объект — обычное
    # поле падало бы на json.loads(dict).
    meta = TolerantJSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        managed = True
        db_table = 'task_activity'
        constraints = [
            models.CheckConstraint(
                name='task_activity_kind_check',
                condition=models.Q(kind__in=['stage_change', 'assign', 'comment', 'system']),
            ),
        ]
        indexes = [
            models.Index(fields=['task', 'created_at'], name='task_activity_task_idx'),
        ]
