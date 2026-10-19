"""Сквозной инвариант закрытия: стадия и поля закрытия не расходятся."""
import pytest

from apps.taskboard.models import Task


@pytest.mark.django_db
def test_closed_flag_matches_stage_category_across_all_tasks(board, manager_client):
    """
    Для КАЖДОЙ задачи: стадия закрытая ⇔ closed_at заполнен.

    Это единственная точка, где два определения «закрыта» сверяются между собой:
    доктрина раздела говорит про category стадии, а всё чтение (фильтры,
    просрочка, частичные индексы) смотрит на closed_at.
    """
    b, stages = board
    BASE = '/api/admin/tasks'
    open_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Открытая'}, format='json').json()['id']
    closed_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Закрытая'}, format='json').json()['id']
    manager_client.post(f'{BASE}/{closed_id}/complete', {}, format='json')

    mismatched = []
    for task in Task.objects.select_related('stage').filter(board=b):
        is_closed_by_stage = task.stage.category == 'closed'
        is_closed_by_field = task.closed_at is not None
        if is_closed_by_stage != is_closed_by_field:
            mismatched.append(task.id)
    assert mismatched == []
    assert {open_id, closed_id} <= set(
        Task.objects.filter(board=b).values_list('id', flat=True))
