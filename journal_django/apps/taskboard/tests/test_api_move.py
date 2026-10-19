"""API переноса, закрытия, комментариев."""
import pytest

BASE = '/api/admin/tasks'


def _create(client, board_id, title='Х'):
    return client.post(BASE, {'board_id': board_id, 'title': title},
                       format='json').json()['id']


@pytest.mark.django_db
def test_move_to_closed_without_resolution_is_400(manager_client, board):
    b, stages = board
    task_id = _create(manager_client, b.id)
    resp = manager_client.post(
        f'{BASE}/{task_id}/move', {'to_stage_id': stages['done'].id}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_move_to_closed_with_resolution_succeeds(manager_client, board):
    b, stages = board
    task_id = _create(manager_client, b.id)
    resp = manager_client.post(
        f'{BASE}/{task_id}/move',
        {'to_stage_id': stages['done'].id, 'resolution': 'done'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['is_closed'] is True


@pytest.mark.django_db
def test_move_rejects_stage_from_another_board(manager_client, board):
    """Стадия чужой воронки — 400, а не 500."""
    from apps.taskboard.models import TaskBoard, TaskStage

    b, _ = board
    task_id = _create(manager_client, b.id)
    other = TaskBoard.objects.create(name='__tb_api_other__')
    alien = TaskStage.objects.create(
        board=other, label='Чужая', sort_order=0, category='open')
    try:
        resp = manager_client.post(
            f'{BASE}/{task_id}/move', {'to_stage_id': alien.id}, format='json')
        assert resp.status_code == 400
    finally:
        alien.delete()
        other.delete()


@pytest.mark.django_db
def test_complete_closes_task(manager_client, board):
    b, stages = board
    task_id = _create(manager_client, b.id)
    resp = manager_client.post(f'{BASE}/{task_id}/complete', {}, format='json')
    assert resp.status_code == 200
    body = resp.json()
    assert body['is_closed'] is True
    assert body['stage_id'] == stages['done'].id
    assert body['resolution'] == 'done'


@pytest.mark.django_db
def test_complete_accepts_explicit_resolution(manager_client, board):
    b, _ = board
    task_id = _create(manager_client, b.id)
    resp = manager_client.post(
        f'{BASE}/{task_id}/complete', {'resolution': 'cancelled'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['resolution'] == 'cancelled'


@pytest.mark.django_db
def test_reopening_clears_resolution_over_api(manager_client, board):
    b, stages = board
    task_id = _create(manager_client, b.id)
    manager_client.post(f'{BASE}/{task_id}/complete', {}, format='json')
    resp = manager_client.post(
        f'{BASE}/{task_id}/move', {'to_stage_id': stages['work'].id}, format='json')
    assert resp.status_code == 200
    body = resp.json()
    assert body['is_closed'] is False
    assert body['resolution'] is None


@pytest.mark.django_db
def test_comment_appears_in_activity(manager_client, board):
    b, _ = board
    task_id = _create(manager_client, b.id)
    assert manager_client.post(
        f'{BASE}/{task_id}/comment', {'body': 'Не берёт трубку'},
        format='json').status_code == 201

    activity = manager_client.get(f'{BASE}/{task_id}/activity').json()
    comments = [a for a in activity if a['kind'] == 'comment']
    assert len(comments) == 1
    assert comments[0]['text'] == 'Не берёт трубку'


@pytest.mark.django_db
def test_empty_comment_is_400(manager_client, board):
    b, _ = board
    task_id = _create(manager_client, b.id)
    resp = manager_client.post(f'{BASE}/{task_id}/comment', {'body': '   '}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_activity_records_author(manager_client, board):
    """Автор комментария — текущий пользователь."""
    b, _ = board
    task_id = _create(manager_client, b.id)
    manager_client.post(f'{BASE}/{task_id}/comment', {'body': 'Тест'}, format='json')
    entry = [a for a in manager_client.get(f'{BASE}/{task_id}/activity').json()
             if a['kind'] == 'comment'][0]
    assert entry['author_id'] is not None


@pytest.mark.django_db
def test_teacher_cannot_move(teacher_client, board):
    b, stages = board
    resp = teacher_client.post(
        f'{BASE}/1/move', {'to_stage_id': stages['work'].id}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_move_on_missing_task_is_404(manager_client, board):
    b, stages = board
    resp = manager_client.post(
        f'{BASE}/99999999/move', {'to_stage_id': stages['work'].id}, format='json')
    assert resp.status_code == 404
