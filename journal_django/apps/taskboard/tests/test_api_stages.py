"""API стадий и справочников: порядок, категории, защита от удаления."""
import pytest

BASE = '/api/admin/tasks'


@pytest.mark.django_db
def test_manager_reads_stages(manager_client, board):
    b, _ = board
    assert manager_client.get(f'{BASE}/boards/{b.id}/stages').status_code == 200


@pytest.mark.django_db
def test_manager_cannot_create_stage(manager_client, board):
    b, _ = board
    resp = manager_client.post(
        f'{BASE}/boards/{b.id}/stages',
        {'label': 'Своя', 'category': 'open'}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_superadmin_creates_stage(superadmin_client, board):
    b, _ = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages',
        {'label': 'Ждём ответа', 'category': 'open', 'color': '#AABBCC'}, format='json')
    assert resp.status_code == 201
    assert resp.json()['label'] == 'Ждём ответа'


@pytest.mark.django_db
def test_duplicate_stage_label_is_409(superadmin_client, board):
    b, _ = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages', {'label': 'Новая', 'category': 'open'}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_cannot_delete_last_closed_stage(superadmin_client, board):
    """В воронке обязана остаться минимум одна стадия каждой категории."""
    b, stages = board
    resp = superadmin_client.delete(f'{BASE}/stages/{stages["done"].id}')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'last_stage_of_category'


@pytest.mark.django_db
def test_cannot_delete_stage_with_tasks(superadmin_client, board):
    b, stages = board
    superadmin_client.post(
        BASE, {'board_id': b.id, 'title': 'Держит стадию'}, format='json')
    resp = superadmin_client.delete(f'{BASE}/stages/{stages["new"].id}')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_tasks'


@pytest.mark.django_db
def test_reorder_changes_sort_order(superadmin_client, board):
    b, stages = board
    order = [stages['work'].id, stages['new'].id, stages['done'].id]
    resp = superadmin_client.post(f'{BASE}/boards/{b.id}/stages/reorder', {'order': order}, format='json')
    assert resp.status_code == 200
    assert [s['id'] for s in resp.json()] == order


@pytest.mark.django_db
def test_reorder_rejects_unknown_stage(superadmin_client, board):
    b, stages = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages/reorder', {'order': [stages['new'].id, 99999999]}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_stage_rejects_unknown_category(superadmin_client, board):
    b, _ = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages', {'label': 'Х', 'category': 'paused'}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cannot_flip_category_of_last_closed_stage(superadmin_client, board):
    """
    Переключить единственную закрытую стадию в «открыта» нельзя: воронка
    осталась бы без закрытых стадий, и кнопка «Выполнено» перестала бы работать.
    """
    b, stages = board
    resp = superadmin_client.patch(
        f'{BASE}/stages/{stages["done"].id}', {'category': 'open'}, format='json')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'last_stage_of_category'


@pytest.mark.django_db
def test_can_flip_category_when_another_remains(superadmin_client, board):
    """Если закрытая стадия не единственная — переключать можно."""
    b, stages = board
    created = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages',
        {'label': 'Отменено', 'category': 'closed'}, format='json')
    assert created.status_code == 201
    resp = superadmin_client.patch(
        f'{BASE}/stages/{stages["done"].id}', {'category': 'open'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['category'] == 'open'


@pytest.mark.django_db
def test_patch_stage_label_still_works(superadmin_client, board):
    """Переименование без смены категории не должно упираться в новую проверку."""
    b, stages = board
    resp = superadmin_client.patch(
        f'{BASE}/stages/{stages["done"].id}', {'label': 'Закрыто'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['label'] == 'Закрыто'


@pytest.mark.django_db
def test_reorder_rejects_stages_from_different_boards(superadmin_client, board):
    """Стадии разных воронок нельзя переставлять одним запросом."""
    from apps.taskboard.models import TaskBoard, TaskStage

    b, stages = board
    other = TaskBoard.objects.create(name='__tb_reorder_other__')
    alien = TaskStage.objects.create(
        board=other, label='Чужая', sort_order=0, category='open')
    try:
        resp = superadmin_client.post(
            f'{BASE}/boards/{b.id}/stages/reorder',
            {'order': [stages['new'].id, alien.id]}, format='json')
        assert resp.status_code == 400
    finally:
        alien.delete()
        other.delete()


@pytest.mark.django_db
def test_week_rejects_too_wide_range(manager_client):
    """Недельный вид отдаёт список без пагинации — диапазон обязан быть ограничен."""
    resp = manager_client.get(f'{BASE}/week?date_from=2020-01-01&date_to=2030-01-01')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_cannot_flip_category_of_stage_with_tasks(superadmin_client, board):
    """
    Смена категории у стадии с карточками рвёт инвариант: задачи оказались бы
    в закрытой колонке с пустыми closed_at/resolution.
    """
    b, stages = board
    # stage_id обязателен: без него задача уходит в ПЕРВУЮ открытую стадию
    # ('new'), и проверка сменила бы категорию у пустой 'work' — тест был бы
    # зелёным по неверной причине.
    superadmin_client.post(
        BASE, {'board_id': b.id, 'title': 'Держит стадию',
               'stage_id': stages['work'].id}, format='json')
    resp = superadmin_client.patch(
        f'{BASE}/stages/{stages["work"].id}', {'category': 'closed'}, format='json')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_tasks'


@pytest.mark.django_db
def test_reorder_requires_full_set_of_board_stages(superadmin_client, board):
    """Неполный набор стадий раздал бы позиции, конфликтующие с нетронутыми."""
    b, stages = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages/reorder',
        {'order': [stages['work'].id, stages['new'].id]}, format='json')
    assert resp.status_code == 400


@pytest.mark.django_db
def test_reorder_rejects_empty_order(superadmin_client, board):
    """
    Регрессия: пустой набор давал 500.

    Прежняя вьюха выводила воронку из присланных стадий (`boards.pop()`), и на
    пустом order множество оказывалось пустым — KeyError вместо ответа 400.
    Теперь воронка приходит из адреса, а пустой набор упирается в проверку
    полноты.
    """
    b, _ = board
    resp = superadmin_client.post(
        f'{BASE}/boards/{b.id}/stages/reorder', {'order': []}, format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == 'incomplete_stage_set'


@pytest.mark.django_db
def test_reorder_rejects_unknown_board(superadmin_client, board):
    """Несуществующая воронка в адресе — 404, а не молчаливая перестановка."""
    b, stages = board
    resp = superadmin_client.post(
        f'{BASE}/boards/99999999/stages/reorder',
        {'order': [stages['new'].id]}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_admin_writes_stages(admin_client, board):
    """Права админа и суперадмина на стадии уравнены (решение 2026-08-27)."""
    b, _ = board
    created = admin_client.post(
        f'{BASE}/boards/{b.id}/stages',
        {'label': '__tb_admin_stage__', 'category': 'open'}, format='json')
    assert created.status_code == 201
    stage_id = created.json()['id']

    renamed = admin_client.patch(
        f'{BASE}/stages/{stage_id}', {'label': '__tb_admin_stage2__'}, format='json')
    assert renamed.status_code == 200

    assert admin_client.delete(f'{BASE}/stages/{stage_id}').status_code == 204


@pytest.mark.django_db
def test_manager_still_cannot_write_stages(manager_client, board):
    """Менеджер ведёт задачи внутри готовой структуры, а не правит её."""
    b, _ = board
    resp = manager_client.post(
        f'{BASE}/boards/{b.id}/stages', {'label': 'Своя', 'category': 'open'}, format='json')
    assert resp.status_code == 403
