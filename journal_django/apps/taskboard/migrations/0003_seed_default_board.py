"""Сид стартовой воронки «Общие задачи».

Идемпотентная data-миграция (get_or_create) — безопасна к повторному прогону.
Обратима: unseed удаляет стадии и воронку, но только если в ней нет задач.
"""
from django.db import migrations

# (label, color, category)
STAGES = [
    ('Новая',    '#6366F1', 'open'),
    ('В работе', '#F59E0B', 'open'),
    ('Готово',   '#22C55E', 'closed'),
]

BOARD_NAME = 'Общие задачи'


def seed(apps, schema_editor):
    TaskBoard = apps.get_model('taskboard', 'TaskBoard')
    TaskStage = apps.get_model('taskboard', 'TaskStage')

    board, _ = TaskBoard.objects.get_or_create(
        name=BOARD_NAME,
        defaults={'description': 'Воронка по умолчанию', 'sort_order': 0},
    )
    for i, (label, color, category) in enumerate(STAGES):
        TaskStage.objects.get_or_create(
            board=board,
            label=label,
            defaults={'color': color, 'category': category, 'sort_order': i},
        )


def unseed(apps, schema_editor):
    TaskBoard = apps.get_model('taskboard', 'TaskBoard')
    TaskStage = apps.get_model('taskboard', 'TaskStage')
    Task = apps.get_model('taskboard', 'Task')

    board = TaskBoard.objects.filter(name=BOARD_NAME).first()
    if board is None:
        return
    # В воронке появились задачи — откат сида молча пропускаем, иначе FK RESTRICT
    # уронит миграцию посреди отката.
    if Task.objects.filter(board=board).exists():
        return
    TaskStage.objects.filter(board=board).delete()
    board.delete()


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0002_tasktagevent_tasktype_tasktypeevent_task_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
