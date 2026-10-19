"""API стадий воронки: read — staff (manager/admin/superadmin), write — только superadmin."""
import pytest

BASE = '/api/admin/renewals/stages'


@pytest.mark.django_db
def test_manager_reads_stages(manager_client):
    assert manager_client.get(BASE).status_code == 200


@pytest.mark.django_db
def test_manager_cannot_create(manager_client):
    resp = manager_client.post(BASE, {'label': 'X', 'kind': 'decision'}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_super_creates_and_deletes(superadmin_client):
    resp = superadmin_client.post(BASE, {'label': 'Перезвонить позже', 'kind': 'decision',
                                         'color': '#AABBCC'}, format='json')
    assert resp.status_code == 201
    sid = resp.json()['id']
    assert superadmin_client.delete(f'{BASE}/{sid}').status_code == 204


@pytest.mark.django_db
def test_cannot_delete_protected_auto_stage(superadmin_client):
    """Авто-стадию «Не было урока»/«Урок N» (is_auto) удалить нельзя → 409."""
    stages = superadmin_client.get(BASE).json()
    auto = next(s for s in stages if s['key'] == 'no_lesson_yet')
    resp = superadmin_client.delete(f"{BASE}/{auto['id']}")
    assert resp.status_code == 409
    assert resp.json()['error'] == 'protected'


@pytest.mark.django_db
def test_cannot_delete_stage_with_closed_deal(superadmin_client, make_student, make_direction):
    """Стадию с ЗАКРЫТОЙ сделкой удалить нельзя (FK RESTRICT) → 409, не 500."""
    from django.utils import timezone
    from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage

    # вторая won-стадия: count(won)==2 → не срабатывает protected-правило единственной
    created = superadmin_client.post(
        BASE, {'label': 'Продлён-2', 'kind': 'won', 'color': '#22C55E'}, format='json')
    assert created.status_code == 201
    stage_id = created.json()['id']

    pipe = RenewalPipeline.objects.get(is_default=True)
    sid = make_student()
    RenewalDeal.objects.create(
        student_id=sid, cycle_no=1, pipeline=pipe,
        stage_id=stage_id, outcome_at=timezone.now())

    resp = superadmin_client.delete(f'{BASE}/{stage_id}')
    assert resp.status_code == 409
    assert resp.json()['error'] == 'has_open_deals'
    # стадия и сделка на месте
    assert RenewalStage.objects.filter(id=stage_id).exists()


@pytest.mark.django_db
def test_super_reorders_stages(superadmin_client):
    """Reorder меняет sort_order: GET отражает новый порядок."""
    stages = superadmin_client.get(BASE).json()
    order = [s['id'] for s in stages]
    reversed_order = list(reversed(order))
    resp = superadmin_client.post(f'{BASE}/reorder', {'order': reversed_order}, format='json')
    assert resp.status_code == 200
    after = [s['id'] for s in resp.json()]
    assert after == reversed_order
    # GET подтверждает персистентность
    again = [s['id'] for s in superadmin_client.get(BASE).json()]
    assert again == reversed_order


@pytest.mark.django_db
def test_manager_cannot_reorder(manager_client):
    resp = manager_client.post(f'{BASE}/reorder', {'order': [1, 2]}, format='json')
    assert resp.status_code == 403


@pytest.mark.django_db
def test_cyrillic_labels_get_distinct_keys(superadmin_client):
    """Два кириллических названия не должны схлопываться в один key (UNIQUE constraint)."""
    r1 = superadmin_client.post(BASE, {'label': 'Перезвонить позже', 'kind': 'decision'}, format='json')
    r2 = superadmin_client.post(BASE, {'label': 'Думает ещё', 'kind': 'decision'}, format='json')
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()['key'] != r2.json()['key']
