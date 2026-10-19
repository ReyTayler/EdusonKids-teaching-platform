"""
Перенос единственного исполнителя в набор исполнителей.

Идёт отдельной миграцией между добавлением M2M (0005) и сносом старого поля
(0007): в одной миграции Django выполнил бы AddField и RemoveField подряд, и
переносить данные было бы уже неоткуда.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Task = apps.get_model('taskboard', 'Task')
    Through = Task.assignees.through
    links = [
        Through(task_id=task_id, account_id=account_id)
        for task_id, account_id in (Task.objects
                                    .filter(assignee_id__isnull=False)
                                    .values_list('id', 'assignee_id'))
    ]
    Through.objects.bulk_create(links, ignore_conflicts=True)


def backwards(apps, schema_editor):
    """
    Обратный перенос заведомо с потерей: у задачи с несколькими исполнителями
    в одиночное поле влезет только один. Берём наименьший id — детерминированно,
    а не «какой попался».
    """
    Task = apps.get_model('taskboard', 'Task')
    Through = Task.assignees.through
    first_by_task: dict[int, int] = {}
    for task_id, account_id in (Through.objects
                                .order_by('task_id', 'account_id')
                                .values_list('task_id', 'account_id')):
        first_by_task.setdefault(task_id, account_id)
    for task_id, account_id in first_by_task.items():
        Task.objects.filter(id=task_id).update(assignee_id=account_id)


class Migration(migrations.Migration):

    dependencies = [
        ('taskboard', '0005_add_task_assignees'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
