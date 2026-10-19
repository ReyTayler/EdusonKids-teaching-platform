"""Стартовая воронка «Общие задачи» создана миграцией."""
import pytest

from apps.taskboard.models import TaskBoard, TaskStage


@pytest.mark.django_db
def test_default_board_seeded():
    board = TaskBoard.objects.get(name='Общие задачи')
    labels = list(
        TaskStage.objects.filter(board=board).order_by('sort_order')
        .values_list('label', 'category'))
    assert labels == [('Новая', 'open'), ('В работе', 'open'), ('Готово', 'closed')]
