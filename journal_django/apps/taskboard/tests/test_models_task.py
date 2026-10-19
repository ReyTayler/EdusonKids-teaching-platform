"""Ограничения БД на карточке задачи."""
import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.taskboard.models import Task, TaskBoard, TaskStage


@pytest.fixture
def board_with_stages(db):
    board = TaskBoard.objects.create(name='__tb_test_task_board__')
    open_stage = TaskStage.objects.create(
        board=board, label='В работе', sort_order=0, category='open')
    closed_stage = TaskStage.objects.create(
        board=board, label='Готово', sort_order=1, category='closed')
    yield board, open_stage, closed_stage
    Task.objects.filter(board=board).delete()
    TaskStage.objects.filter(board=board).delete()
    board.delete()


@pytest.mark.django_db
def test_open_task_has_no_resolution(board_with_stages):
    board, open_stage, _ = board_with_stages
    task = Task.objects.create(board=board, stage=open_stage, title='Позвонить')
    assert task.resolution is None
    assert task.closed_at is None


@pytest.mark.django_db
def test_closed_at_requires_resolution(board_with_stages):
    """closed_at и resolution заполняются только вместе — CHECK на уровне БД."""
    board, _, closed_stage = board_with_stages
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Task.objects.create(
                board=board, stage=closed_stage, title='Битая',
                closed_at=timezone.now(), resolution=None)


@pytest.mark.django_db
def test_resolution_requires_closed_at(board_with_stages):
    board, open_stage, _ = board_with_stages
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Task.objects.create(
                board=board, stage=open_stage, title='Битая',
                closed_at=None, resolution='done')


@pytest.mark.django_db
def test_rejects_unknown_priority(board_with_stages):
    board, open_stage, _ = board_with_stages
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Task.objects.create(
                board=board, stage=open_stage, title='Х', priority='urgent')


@pytest.mark.django_db
def test_stage_with_tasks_cannot_be_deleted(board_with_stages):
    """FK RESTRICT: стадию с задачами удалить нельзя."""
    from django.db.models import RestrictedError

    board, open_stage, _ = board_with_stages
    Task.objects.create(board=board, stage=open_stage, title='Держит стадию')
    with pytest.raises(RestrictedError):
        with transaction.atomic():
            open_stage.delete()
