"""API карточек: доступ по ролям, создание, правка, удаление."""
import pytest

BASE = '/api/admin/tasks'


@pytest.mark.django_db
def test_teacher_is_denied(teacher_client, board):
    b, _ = board
    assert teacher_client.get(f'{BASE}?board_id={b.id}').status_code == 403


@pytest.mark.django_db
def test_anonymous_is_denied(anon_client, board):
    b, _ = board
    assert anon_client.get(f'{BASE}?board_id={b.id}').status_code == 401


@pytest.mark.django_db
def test_manager_creates_and_reads(manager_client, board):
    b, _ = board
    created = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Позвонить Ивановым'}, format='json')
    assert created.status_code == 201
    task_id = created.json()['id']

    listing = manager_client.get(f'{BASE}?board_id={b.id}')
    assert listing.status_code == 200
    assert task_id in [t['id'] for t in listing.json()['rows']]


@pytest.mark.django_db
def test_created_task_records_author(manager_client, board):
    """Постановщик проставляется из текущего пользователя, а не из запроса."""
    b, _ = board
    created = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Х'}, format='json')
    assert created.json()['created_by_id'] is not None


@pytest.mark.django_db
def test_manager_patches_title(manager_client, board):
    b, _ = board
    task_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Старое'}, format='json').json()['id']
    resp = manager_client.patch(f'{BASE}/{task_id}', {'title': 'Новое'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['title'] == 'Новое'


@pytest.mark.django_db
def test_patch_cannot_change_stage(manager_client, board):
    """Стадия меняется только через /move — PATCH её игнорирует."""
    b, stages = board
    task_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Х'}, format='json').json()['id']
    resp = manager_client.patch(
        f'{BASE}/{task_id}', {'stage_id': stages['done'].id}, format='json')
    assert resp.status_code == 200
    assert resp.json()['stage_id'] == stages['new'].id


@pytest.mark.django_db
def test_create_rejects_unknown_assignee(manager_client, board):
    """Несуществующий исполнитель — 400, а не 500."""
    b, _ = board
    resp = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Х', 'assignee_ids': [99999999]}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_create_requires_title(manager_client, board):
    b, _ = board
    resp = manager_client.post(BASE, {'board_id': b.id}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_manager_cannot_delete(manager_client, board):
    b, _ = board
    task_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Х'}, format='json').json()['id']
    assert manager_client.delete(f'{BASE}/{task_id}').status_code == 403


@pytest.mark.django_db
def test_admin_deletes(admin_client, board):
    b, _ = board
    task_id = admin_client.post(
        BASE, {'board_id': b.id, 'title': 'Х'}, format='json').json()['id']
    assert admin_client.delete(f'{BASE}/{task_id}').status_code == 204
    assert admin_client.get(f'{BASE}/{task_id}').status_code == 404


@pytest.mark.django_db
def test_detail_returns_404_for_missing(manager_client):
    assert manager_client.get(f'{BASE}/99999999').status_code == 404


@pytest.mark.django_db
def test_list_is_paginated(manager_client, board):
    """Список обязан приходить страницами со счётчиком."""
    b, _ = board
    for i in range(3):
        manager_client.post(BASE, {'board_id': b.id, 'title': f'Т{i}'}, format='json')
    body = manager_client.get(f'{BASE}?board_id={b.id}&page_size=2').json()
    assert body['total'] == 3
    assert len(body['rows']) == 2


@pytest.mark.django_db
def test_only_open_filter(manager_client, board):
    b, stages = board
    open_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Открытая'}, format='json').json()['id']
    closed_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Закрытая'}, format='json').json()['id']
    from apps.taskboard import services
    from apps.taskboard.models import Task
    services.move_task(Task.objects.get(id=closed_id), to_stage_id=stages['done'].id,
                       resolution='done', author_id=None)

    ids = [t['id'] for t in
           manager_client.get(f'{BASE}?board_id={b.id}&only_open=true').json()['rows']]
    assert open_id in ids
    assert closed_id not in ids


@pytest.mark.django_db
def test_patch_is_atomic_when_assignees_fail(manager_client, board):
    """
    Правка полей и смена тегов — одна транзакция. Упали теги — не должно
    сохраниться и остальное, иначе клиент получает 400 при частично
    применённой правке.
    """
    b, _ = board
    task_id = manager_client.post(
        BASE, {'board_id': b.id, 'title': 'Исходное'}, format='json').json()['id']

    resp = manager_client.patch(
        f'{BASE}/{task_id}',
        {'title': 'Изменённое', 'assignee_ids': [99999999]}, format='json')
    assert resp.status_code == 400

    assert manager_client.get(f'{BASE}/{task_id}').json()['title'] == 'Исходное'


