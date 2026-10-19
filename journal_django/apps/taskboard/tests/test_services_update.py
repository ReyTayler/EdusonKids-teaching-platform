"""Правка полей, смена исполнителя, комментарии, теги."""
import pytest

from apps.taskboard import services
from apps.taskboard.models import TaskActivity


@pytest.mark.django_db
def test_update_changes_title_and_due_date(board):
    import datetime

    b, _ = board
    task = services.create_task(board_id=b.id, title='Старое', author_id=None)
    updated = services.update_task(
        task, author_id=None,
        fields={'title': 'Новое', 'due_date': datetime.date(2026, 9, 1)})
    assert updated.title == 'Новое'
    assert updated.due_date == datetime.date(2026, 9, 1)


@pytest.mark.django_db
def test_update_ignores_unknown_fields(board):
    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    updated = services.update_task(task, author_id=None, fields={'closed_at': 'нельзя'})
    assert updated.closed_at is None


@pytest.mark.django_db
def test_update_cannot_change_stage_or_resolution(board):
    """Стадия и результат меняются только через move_task/complete_task."""
    from apps.taskboard.models import Task

    b, stages = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.update_task(
        task, author_id=None,
        fields={'stage_id': stages['done'].id, 'resolution': 'done'})
    fresh = Task.objects.get(id=task.id)
    assert fresh.stage_id == stages['new'].id
    assert fresh.resolution is None


@pytest.mark.django_db
def test_assignee_change_writes_assign_activity(board, admin_account_id):
    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    services.set_assignees(task, assignee_ids=[admin_account_id], author_id=None)
    kinds = list(TaskActivity.objects.filter(task=task).values_list('kind', flat=True))
    assert 'assign' in kinds


@pytest.mark.django_db
def test_update_without_changes_writes_nothing(board):
    """Пустая правка не должна засорять ленту."""
    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    before = TaskActivity.objects.filter(task=task).count()
    services.update_task(task, author_id=None, fields={'title': 'Х'})
    assert TaskActivity.objects.filter(task=task).count() == before


@pytest.mark.django_db
def test_add_comment_creates_entry(board):
    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    entry = services.add_comment(task, body='Позвонил, не берёт', author_id=None)
    assert entry.kind == 'comment'
    assert entry.text == 'Позвонил, не берёт'


@pytest.mark.django_db
def test_update_rejects_unknown_priority(board):
    """Неизвестный приоритет — ошибка валидации, а не IntegrityError из БД."""
    from rest_framework.serializers import ValidationError

    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    with pytest.raises(ValidationError):
        services.update_task(task, author_id=None, fields={'priority': 'срочно'})


@pytest.mark.django_db
def test_assign_activity_records_actual_previous_assignee(board, admin_account_id):
    """
    Двое админов правят карточку. Второй держит объект, загруженный ДО чужой
    правки. Лента обязана записать реальное «было» из БД, а не из устаревшего
    объекта — иначе история вводит в заблуждение.
    """
    from apps.taskboard.models import Task

    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    stale = Task.objects.get(id=task.id)  # снимок без исполнителей

    services.set_assignees(task, assignee_ids=[admin_account_id], author_id=None)
    services.set_assignees(stale, assignee_ids=[], author_id=None)

    entry = (TaskActivity.objects
             .filter(task=task, kind='assign').order_by('-id').first())
    assert entry.meta['from_assignee_ids'] == [admin_account_id]
    assert entry.meta['to_assignee_ids'] == []


@pytest.mark.django_db
def test_update_rejects_unknown_assignee(board):
    """Правка с несуществующим исполнителем — ошибка валидации."""
    from rest_framework.serializers import ValidationError

    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    with pytest.raises(ValidationError):
        services.set_assignees(task, assignee_ids=[99999999], author_id=None)


@pytest.mark.django_db
def test_update_rejects_unknown_group(board):
    from rest_framework.serializers import ValidationError

    b, _ = board
    task = services.create_task(board_id=b.id, title='Х', author_id=None)
    with pytest.raises(ValidationError):
        services.update_task(task, author_id=None, fields={'group_id': 99999999})


@pytest.mark.django_db
def test_update_allows_clearing_assignee(board, admin_account_id):
    """Пустой набор — не ошибка: у задачи законно может не быть исполнителя."""
    from apps.taskboard.models import Task

    b, _ = board
    task = services.create_task(
        board_id=b.id, title='Х', author_id=None, assignee_ids=[admin_account_id])
    services.set_assignees(task, assignee_ids=[], author_id=None)
    assert list(Task.objects.get(id=task.id).assignees.all()) == []


@pytest.mark.django_db
def test_due_date_change_is_logged(board):
    import datetime

    from apps.taskboard.models import TaskActivity

    b, _ = board
    task = services.create_task(board_id=b.id, title='Задача', author_id=None)
    services.update_task(
        task, author_id=None, fields={'due_date': datetime.date(2026, 8, 28)})

    entry = (TaskActivity.objects
             .filter(task=task, kind='system')
             .order_by('-id').first())
    assert entry.meta['field'] == 'due_date'
    assert entry.meta['from'] is None
    assert entry.meta['to'] == '2026-08-28'


@pytest.mark.django_db
def test_priority_change_is_logged(board):
    from apps.taskboard.models import TaskActivity

    b, _ = board
    task = services.create_task(board_id=b.id, title='Задача', author_id=None)
    services.update_task(task, author_id=None, fields={'priority': 'high'})

    entry = (TaskActivity.objects
             .filter(task=task, kind='system')
             .order_by('-id').first())
    assert entry.meta == {'field': 'priority', 'from': 'normal', 'to': 'high'}


@pytest.mark.django_db
def test_unchanged_field_writes_nothing(board):
    from apps.taskboard.models import TaskActivity

    b, _ = board
    task = services.create_task(board_id=b.id, title='Задача', author_id=None)
    before = TaskActivity.objects.filter(task=task).count()
    services.update_task(task, author_id=None, fields={'priority': 'normal'})

    assert TaskActivity.objects.filter(task=task).count() == before


@pytest.mark.django_db
def test_assignee_change_still_uses_assign_kind(board, admin_account_id):
    """Смена исполнителя остаётся записью kind='assign', а не системной."""
    from apps.taskboard.models import TaskActivity

    b, _ = board
    task = services.create_task(board_id=b.id, title='Задача', author_id=None)
    services.set_assignees(task, assignee_ids=[admin_account_id], author_id=None)

    kinds = list(TaskActivity.objects.filter(task=task)
                 .order_by('id').values_list('kind', flat=True))
    assert kinds == ['system', 'assign']  # 'system' — запись о создании


@pytest.mark.django_db
def test_task_keeps_several_assignees(board, admin_account_id, manager_account_id):
    """Исполнителей может быть несколько — набор хранится целиком."""
    from apps.taskboard.models import Task

    b, _ = board
    task = services.create_task(
        board_id=b.id, title='Вдвоём', author_id=None,
        assignee_ids=[admin_account_id, manager_account_id])

    ids = sorted(Task.objects.get(id=task.id).assignees.values_list('id', flat=True))
    assert ids == sorted([admin_account_id, manager_account_id])


@pytest.mark.django_db
def test_same_assignee_set_writes_nothing(board, admin_account_id):
    """Пересохранение того же набора не должно засорять ленту."""
    b, _ = board
    task = services.create_task(
        board_id=b.id, title='Х', author_id=None, assignee_ids=[admin_account_id])
    before = TaskActivity.objects.filter(task=task).count()
    services.set_assignees(task, assignee_ids=[admin_account_id], author_id=None)
    assert TaskActivity.objects.filter(task=task).count() == before
