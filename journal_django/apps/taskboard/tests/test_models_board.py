"""Ограничения БД на воронке и стадии."""
import pytest
from django.db import IntegrityError, transaction

from apps.taskboard.models import TaskBoard, TaskStage


@pytest.fixture
def board(db):
    b = TaskBoard.objects.create(name='__tb_test_board__')
    yield b
    TaskStage.objects.filter(board=b).delete()
    b.delete()


@pytest.mark.django_db
def test_stage_label_unique_within_board(board):
    TaskStage.objects.create(board=board, label='Новая', sort_order=0, category='open')
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TaskStage.objects.create(board=board, label='Новая', sort_order=1, category='open')


@pytest.mark.django_db
def test_stage_rejects_unknown_category(board):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TaskStage.objects.create(board=board, label='Х', sort_order=0, category='wat')


@pytest.mark.django_db
def test_stage_rejects_malformed_color(board):
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            TaskStage.objects.create(
                board=board, label='Х', sort_order=0, category='open', color='красный')


@pytest.mark.django_db
def test_stage_accepts_null_color(board):
    stage = TaskStage.objects.create(board=board, label='Без цвета', sort_order=0, category='open')
    assert stage.color is None
