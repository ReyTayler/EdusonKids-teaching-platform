"""Создание задачи через сервисный слой."""
import pytest

from apps.taskboard import services
from apps.taskboard.models import TaskActivity


@pytest.mark.django_db
def test_create_puts_task_into_first_open_stage(board):
    b, stages = board
    task = services.create_task(board_id=b.id, title='Позвонить Ивановым', author_id=None)
    assert task.stage_id == stages['new'].id
    assert task.closed_at is None
    assert task.resolution is None


@pytest.mark.django_db
def test_create_writes_system_activity(board):
    b, _ = board
    task = services.create_task(board_id=b.id, title='Позвонить', author_id=None)
    entry = TaskActivity.objects.get(task=task)
    assert entry.kind == 'system'


@pytest.mark.django_db
def test_create_accepts_explicit_stage(board):
    b, stages = board
    task = services.create_task(
        board_id=b.id, title='Уже в работе', author_id=None, stage_id=stages['work'].id)
    assert task.stage_id == stages['work'].id


@pytest.mark.django_db
def test_create_rejects_stage_from_another_board(board):
    from rest_framework.serializers import ValidationError

    from apps.taskboard.models import TaskBoard, TaskStage

    b, _ = board
    other = TaskBoard.objects.create(name='__tb_other_board__')
    alien = TaskStage.objects.create(board=other, label='Чужая', sort_order=0, category='open')
    try:
        with pytest.raises(ValidationError):
            services.create_task(
                board_id=b.id, title='Х', author_id=None, stage_id=alien.id)
    finally:
        alien.delete()
        other.delete()


@pytest.mark.django_db
def test_create_rejects_board_without_open_stage(db):
    from rest_framework.serializers import ValidationError

    from apps.taskboard.models import TaskBoard, TaskStage

    empty = TaskBoard.objects.create(name='__tb_empty_board__')
    TaskStage.objects.create(board=empty, label='Готово', sort_order=0, category='closed')
    try:
        with pytest.raises(ValidationError):
            services.create_task(board_id=empty.id, title='Х', author_id=None)
    finally:
        TaskStage.objects.filter(board=empty).delete()
        empty.delete()


@pytest.mark.django_db
def test_create_rejects_unknown_assignee(board):
    """Несуществующий исполнитель — ошибка валидации, а не 500 на коммите."""
    from rest_framework.serializers import ValidationError

    b, _ = board
    with pytest.raises(ValidationError):
        services.create_task(
            board_id=b.id, title='Х', author_id=None, assignee_ids=[99999999])


@pytest.mark.django_db
def test_create_rejects_unknown_student(board):
    from rest_framework.serializers import ValidationError

    b, _ = board
    with pytest.raises(ValidationError):
        services.create_task(
            board_id=b.id, title='Х', author_id=None, student_id=99999999)


@pytest.mark.django_db
def test_create_rejects_unknown_group(board):
    from rest_framework.serializers import ValidationError

    b, _ = board
    with pytest.raises(ValidationError):
        services.create_task(
            board_id=b.id, title='Х', author_id=None, group_id=99999999)


@pytest.mark.django_db
def test_create_rejects_closed_stage(board):
    """
    Задача не может родиться закрытой: стадия была бы closed, а поля закрытия
    пустые — тот же разрыв инварианта, что и при смене категории стадии.
    """
    from rest_framework.serializers import ValidationError

    b, stages = board
    with pytest.raises(ValidationError):
        services.create_task(
            board_id=b.id, title='Х', author_id=None, stage_id=stages['done'].id)
