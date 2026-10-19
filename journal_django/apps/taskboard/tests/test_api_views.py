"""API доски (колонки со счётчиками) и недельного вида."""
import datetime

import pytest

BASE = '/api/admin/tasks'


def _create(client, board_id, title='Х', **extra):
    payload = {'board_id': board_id, 'title': title}
    payload.update(extra)
    return client.post(BASE, payload, format='json').json()['id']


@pytest.mark.django_db
def test_column_counts_by_stage(manager_client, board):
    b, stages = board
    for i in range(3):
        _create(manager_client, b.id, f'Т{i}')

    resp = manager_client.get(f'{BASE}/boards/{b.id}/columns')
    assert resp.status_code == 200
    counts = {c['stage_id']: c['count'] for c in resp.json()}
    assert counts[stages['new'].id] == 3
    assert counts[stages['work'].id] == 0


@pytest.mark.django_db
def test_columns_are_ordered_and_carry_category(manager_client, board):
    b, stages = board
    body = manager_client.get(f'{BASE}/boards/{b.id}/columns').json()
    assert [c['stage_id'] for c in body] == [
        stages['new'].id, stages['work'].id, stages['done'].id]
    assert body[-1]['category'] == 'closed'


@pytest.mark.django_db
def test_columns_carry_no_cards(manager_client, board):
    """Счётчики — лёгкий агрегат: карточек в ответе быть не должно."""
    b, _ = board
    _create(manager_client, b.id)
    body = manager_client.get(f'{BASE}/boards/{b.id}/columns').json()
    assert all('tasks' not in c and 'results' not in c for c in body)


@pytest.mark.django_db
def test_column_cards_are_paginated(manager_client, board):
    b, stages = board
    _create(manager_client, b.id, 'Одна')
    resp = manager_client.get(f'{BASE}/columns/{stages["new"].id}')
    assert resp.status_code == 200
    assert 'rows' in resp.json()
    assert resp.json()['total'] == 1


@pytest.mark.django_db
def test_week_requires_valid_range(manager_client):
    resp = manager_client.get(f'{BASE}/week?date_from=2026-09-10&date_to=2026-09-01')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_week_requires_both_dates(manager_client):
    assert manager_client.get(f'{BASE}/week?date_from=2026-09-01').status_code == 400


@pytest.mark.django_db
def test_week_returns_dated_tasks(manager_client, board):
    b, _ = board
    today = datetime.date.today().isoformat()
    _create(manager_client, b.id, 'Сегодня', due_date=today)
    _create(manager_client, b.id, 'Без даты')

    resp = manager_client.get(f'{BASE}/week?date_from={today}&date_to={today}')
    assert resp.status_code == 200
    titles = [t['title'] for t in resp.json()]
    assert 'Сегодня' in titles
    assert 'Без даты' not in titles


@pytest.mark.django_db
def test_teacher_denied_on_board_views(teacher_client, board):
    b, stages = board
    assert teacher_client.get(f'{BASE}/boards/{b.id}/columns').status_code == 403
    assert teacher_client.get(f'{BASE}/columns/{stages["new"].id}').status_code == 403
    assert teacher_client.get(f'{BASE}/week?date_from=2026-09-01&date_to=2026-09-02'
                              ).status_code == 403


@pytest.mark.django_db
def test_week_respects_board_filter(manager_client, board):
    """Недельный вид обязан уважать выбранную воронку, иначе фильтр-бар врёт."""
    from apps.taskboard.models import TaskBoard, TaskStage

    b, _ = board
    today = datetime.date.today().isoformat()
    mine = _create(manager_client, b.id, 'Моя', due_date=today)

    other = TaskBoard.objects.create(name='__tb_week_other__')
    TaskStage.objects.create(board=other, label='Новая', sort_order=0, category='open')
    TaskStage.objects.create(board=other, label='Готово', sort_order=1, category='closed')
    try:
        alien = _create(manager_client, other.id, 'Чужая', due_date=today)
        ids = [t['id'] for t in manager_client.get(
            f'{BASE}/week?date_from={today}&date_to={today}&board_id={b.id}').json()]
        assert mine in ids
        assert alien not in ids
    finally:
        from apps.taskboard.models import Task, TaskActivity
        TaskActivity.objects.filter(task__board=other).delete()
        Task.objects.filter(board=other).delete()
        TaskStage.objects.filter(board=other).delete()
        other.delete()


@pytest.mark.django_db
def test_week_respects_only_open_filter(manager_client, board):
    b, stages = board
    today = datetime.date.today().isoformat()
    open_id = _create(manager_client, b.id, 'Открытая', due_date=today)
    closed_id = _create(manager_client, b.id, 'Закрытая', due_date=today)
    manager_client.post(f'{BASE}/{closed_id}/complete', {}, format='json')

    ids = [t['id'] for t in manager_client.get(
        f'{BASE}/week?date_from={today}&date_to={today}&only_open=true').json()]
    assert open_id in ids
    assert closed_id not in ids


@pytest.mark.django_db
def test_garbage_filter_value_is_400(manager_client):
    """Мусор в параметрах — понятная ошибка, а не 500."""
    assert manager_client.get(f'{BASE}?board_id=abc').status_code == 400


@pytest.mark.django_db
def test_column_cards_respect_filters(manager_client, board):
    """Фильтр-бар общий для доски и недели — колонка обязана его уважать."""
    b, stages = board
    high = _create(manager_client, b.id, 'Срочная', priority='high')
    _create(manager_client, b.id, 'Обычная')

    body = manager_client.get(
        f'{BASE}/columns/{stages["new"].id}?priority=high').json()
    assert [t['id'] for t in body['rows']] == [high]
    assert body['total'] == 1


@pytest.mark.django_db
def test_column_counts_respect_filters(manager_client, board):
    """Счётчик в шапке колонки не должен расходиться со списком под ней."""
    b, stages = board
    _create(manager_client, b.id, 'Срочная', priority='high')
    _create(manager_client, b.id, 'Обычная')

    counts = {c['stage_id']: c['count'] for c in manager_client.get(
        f'{BASE}/boards/{b.id}/columns?priority=high').json()}
    assert counts[stages['new'].id] == 1
