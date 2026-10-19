"""API воронок: чтение — сотрудники, запись — только суперадмин."""
import pytest

BASE = '/api/admin/tasks/boards'


@pytest.mark.django_db
def test_manager_reads_boards(manager_client):
    assert manager_client.get(BASE).status_code == 200


@pytest.mark.django_db
def test_teacher_is_denied(teacher_client):
    assert teacher_client.get(BASE).status_code == 403


@pytest.mark.django_db
def test_manager_cannot_create_board(manager_client):
    assert manager_client.post(BASE, {'name': 'Своя'}, format='json').status_code == 403


@pytest.mark.django_db
def test_superadmin_creates_renames_and_deletes(superadmin_client):
    created = superadmin_client.post(
        BASE, {'name': '__tb_api_board__', 'description': 'Проверка'}, format='json')
    assert created.status_code == 201
    board_id = created.json()['id']

    renamed = superadmin_client.patch(
        f'{BASE}/{board_id}', {'name': '__tb_api_board_2__'}, format='json')
    assert renamed.status_code == 200
    assert renamed.json()['name'] == '__tb_api_board_2__'

    assert superadmin_client.delete(f'{BASE}/{board_id}').status_code == 204


@pytest.mark.django_db
def test_board_with_tasks_cannot_be_deleted(superadmin_client, board):
    """Воронку с задачами удалить нельзя → 409, не 500."""
    b, _ = board
    superadmin_client.post(
        '/api/admin/tasks', {'board_id': b.id, 'title': 'Держит воронку'}, format='json')
    resp = superadmin_client.delete(f'{BASE}/{b.id}')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_tasks'


@pytest.mark.django_db
def test_duplicate_board_name_is_409(superadmin_client, board):
    b, _ = board
    assert superadmin_client.post(BASE, {'name': b.name}, format='json').status_code == 409


@pytest.mark.django_db
def test_board_ignores_removed_archive_flag(superadmin_client, board):
    """Архивирования больше нет: воронку либо ведут, либо удаляют целиком."""
    b, _ = board
    resp = superadmin_client.patch(f'{BASE}/{b.id}', {'is_archived': True}, format='json')
    assert resp.status_code == 200
    assert 'is_archived' not in resp.json()


@pytest.mark.django_db
def test_board_list_carries_counts(manager_client, board):
    """Полоса выбора воронки показывает число стадий и открытых задач."""
    from apps.taskboard import services

    b, stages = board
    open_task = services.create_task(board_id=b.id, title='Открытая', author_id=None)
    closed = services.create_task(board_id=b.id, title='Закрытая', author_id=None)
    services.move_task(closed, to_stage_id=stages['done'].id,
                       resolution='done', author_id=None)
    assert open_task.id  # задача создана, иначе счётчик проверять нечего

    rows = manager_client.get('/api/admin/tasks/boards').json()
    row = next(r for r in rows if r['id'] == b.id)
    assert row['stages_count'] == 3
    # Закрытая в счётчик не входит: полоса показывает, сколько ещё в работе.
    assert row['open_tasks_count'] == 1


@pytest.mark.django_db
def test_board_counts_do_not_multiply_each_other(manager_client, board):
    """Стадии и задачи — независимые связи; JOIN'ом их счётчики перемножились бы."""
    from apps.taskboard import services

    b, _ = board
    for i in range(4):
        services.create_task(board_id=b.id, title=f'Задача {i}', author_id=None)

    rows = manager_client.get('/api/admin/tasks/boards').json()
    row = next(r for r in rows if r['id'] == b.id)
    assert row['stages_count'] == 3
    assert row['open_tasks_count'] == 4


@pytest.mark.django_db
def test_admin_writes_boards(admin_client):
    """Права админа и суперадмина на воронки уравнены (решение 2026-08-27)."""
    created = admin_client.post(BASE, {'name': '__tb_admin_board__'}, format='json')
    assert created.status_code == 201
    board_id = created.json()['id']

    renamed = admin_client.patch(
        f'{BASE}/{board_id}', {'name': '__tb_admin_board2__'}, format='json')
    assert renamed.status_code == 200

    assert admin_client.delete(f'{BASE}/{board_id}').status_code == 204
