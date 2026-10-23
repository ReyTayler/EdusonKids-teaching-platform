"""API справочника причин отмены: /api/admin/cancellation-reasons.

Чтение — manager/admin/superadmin (форма отмены), запись — только superadmin.
Флаг compensation решает, заплатят ли преподавателю, то есть относится к
мотивации: не-superadmin его не видит (правило владельца 2026-10-23, В3)."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

URL = '/api/admin/cancellation-reasons'


def _detail(pk: int) -> str:
    return f'{URL}/{pk}'


def _create(client, **body):
    payload = {'side': 'client', 'name': 'Заболел', **body}
    return client.post(URL, payload, format='json')


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

def test_anon_rejected(anon_client, make_reason):
    r = make_reason()
    assert anon_client.get(URL).status_code in (401, 403)
    assert _create(anon_client).status_code in (401, 403)
    assert anon_client.patch(_detail(r.id), {'name': 'X'}, format='json').status_code in (401, 403)
    assert anon_client.delete(_detail(r.id)).status_code in (401, 403)


def test_teacher_forbidden_everywhere(teacher_client, make_reason):
    r = make_reason()
    assert teacher_client.get(URL).status_code == 403
    assert _create(teacher_client, name='Другое').status_code == 403
    assert teacher_client.patch(_detail(r.id), {'name': 'X'}, format='json').status_code == 403
    assert teacher_client.delete(_detail(r.id)).status_code == 403


@pytest.mark.parametrize('role_client', ['manager_client', 'admin_client'])
def test_staff_reads_but_cannot_write(request, role_client, make_reason):
    from apps.cancellations.models import CancellationReason

    client = request.getfixturevalue(role_client)
    r = make_reason(name='Заболел', compensation=True)

    assert client.get(URL).status_code == 200
    assert _create(client, name='Новая').status_code == 403
    assert client.patch(_detail(r.id), {'compensation': False}, format='json').status_code == 403
    assert client.delete(_detail(r.id)).status_code == 403

    r.refresh_from_db()
    assert (r.name, r.compensation, r.deleted_at) == ('Заболел', True, None)
    assert CancellationReason.objects.count() == 1


@pytest.mark.parametrize('role_client', ['manager_client', 'admin_client'])
def test_staff_does_not_see_compensation(request, role_client, make_reason):
    client = request.getfixturevalue(role_client)
    make_reason(side='client', name='Заболел', compensation=True)
    make_reason(side='teacher', name='Болезнь', compensation=False)

    body = client.get(URL).json()
    rows = body['client'] + body['teacher']
    assert len(rows) == 2
    assert all('compensation' not in row for row in rows)


def test_superadmin_sees_compensation(superadmin_client, make_reason):
    r = make_reason(side='client', name='Заболел', compensation=True)
    body = superadmin_client.get(URL).json()
    assert body == {
        'teacher': [],
        'client': [{'id': r.id, 'side': 'client', 'name': 'Заболел',
                    'position': r.position, 'compensation': True}],
    }


def test_superadmin_mutation_response_has_compensation(superadmin_client):
    """Ответ мутаций — тот же Row, что в GET (superadmin видит флаг)."""
    resp = _create(superadmin_client, compensation=True)
    assert resp.status_code == 201
    assert resp.json()['compensation'] is True


# ---------------------------------------------------------------------------
# GET — порядок и фильтр удалённых
# ---------------------------------------------------------------------------

def test_list_grouped_by_side_in_position_order(superadmin_client, make_reason):
    b = make_reason(side='client', name='Б', position=2)
    a = make_reason(side='client', name='А', position=1)
    # равные позиции — детерминированный тай-брейк по id
    c1 = make_reason(side='client', name='В', position=5)
    c2 = make_reason(side='client', name='Г', position=5)
    t = make_reason(side='teacher', name='Болезнь', position=0)

    body = superadmin_client.get(URL).json()
    assert [r['id'] for r in body['client']] == [a.id, b.id, c1.id, c2.id]
    assert [r['id'] for r in body['teacher']] == [t.id]


def test_list_hides_deleted(superadmin_client, make_reason):
    from django.utils import timezone

    keep = make_reason(name='Заболел')
    make_reason(name='Уехал', deleted_at=timezone.now())
    body = superadmin_client.get(URL).json()
    assert [r['id'] for r in body['client']] == [keep.id]


def test_list_query_count_constant(superadmin_client, make_reason, django_assert_max_num_queries):
    for i in range(10):
        make_reason(side='client' if i % 2 else 'teacher', name=f'Причина {i}')
    # аутентификация (учётка) + один запрос справочника
    with django_assert_max_num_queries(3):
        assert superadmin_client.get(URL).status_code == 200


# ---------------------------------------------------------------------------
# POST
# ---------------------------------------------------------------------------

def test_create_appends_to_end_of_side(superadmin_client, make_reason):
    make_reason(side='client', name='А', position=0)
    make_reason(side='client', name='Б', position=7)
    make_reason(side='teacher', name='В', position=40)

    resp = _create(superadmin_client, side='client', name='Новая')

    assert resp.status_code == 201
    body = resp.json()
    assert body['position'] == 8
    assert (body['side'], body['name'], body['compensation']) == ('client', 'Новая', False)


def test_create_first_in_side_gets_zero(superadmin_client, make_reason):
    make_reason(side='client', name='А', position=3)
    resp = _create(superadmin_client, side='teacher', name='Болезнь')
    assert resp.json()['position'] == 0


def test_create_position_ignores_deleted(superadmin_client, make_reason):
    from django.utils import timezone

    make_reason(side='client', name='А', position=0)
    make_reason(side='client', name='Б', position=9, deleted_at=timezone.now())
    assert _create(superadmin_client, name='Новая').json()['position'] == 1


def test_create_trims_name(superadmin_client):
    from apps.cancellations.models import CancellationReason

    resp = _create(superadmin_client, name='   Заболел  ')
    assert resp.status_code == 201
    assert resp.json()['name'] == 'Заболел'
    assert CancellationReason.objects.get(pk=resp.json()['id']).name == 'Заболел'


@pytest.mark.parametrize('name', ['', '    ', 'А' * 201])
def test_create_invalid_name(superadmin_client, name):
    from apps.cancellations.models import CancellationReason

    resp = _create(superadmin_client, name=name)
    assert resp.status_code == 400
    assert 'name' in resp.json()['details']
    assert not CancellationReason.objects.exists()


def test_create_name_max_length_accepted(superadmin_client):
    assert _create(superadmin_client, name='А' * 200).status_code == 201


@pytest.mark.parametrize('body', [
    {'side': 'student', 'name': 'Заболел'},
    {'name': 'Заболел'},
    {'side': 'client'},
    {'side': 'client', 'name': 'Заболел', 'compensation': 'может быть'},
    {'side': 'client', 'name': 'Заболел', 'position': 3},
    {'side': 'client', 'name': 'Заболел', 'colour': 'red'},
])
def test_create_invalid_body(superadmin_client, body):
    resp = superadmin_client.post(URL, body, format='json')
    assert resp.status_code == 400


def test_create_duplicate_active_name_case_insensitive(superadmin_client, make_reason):
    from apps.cancellations.models import CancellationReason

    make_reason(side='client', name='Заболел')
    resp = _create(superadmin_client, side='client', name='  заболел ')
    assert resp.status_code == 400
    assert resp.json()['details'] == {'name': ['Такая причина уже есть.']}
    assert CancellationReason.objects.count() == 1


def test_create_same_name_other_side_ok(superadmin_client, make_reason):
    make_reason(side='client', name='Заболел')
    assert _create(superadmin_client, side='teacher', name='Заболел').status_code == 201


def test_create_duplicate_race_maps_to_400(superadmin_client, make_reason, monkeypatch):
    """Гонка: предпроверка не увидела дубль, но UNIQUE в БД сработал → 400, не 500."""
    from apps.cancellations import repository

    make_reason(side='client', name='Заболел')
    monkeypatch.setattr(repository, 'active_name_taken', lambda *a, **k: False)
    resp = _create(superadmin_client, side='client', name='Заболел')
    assert resp.status_code == 400
    assert resp.json()['details'] == {'name': ['Такая причина уже есть.']}


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------

def test_patch_fields(superadmin_client, make_reason):
    r = make_reason(side='client', name='Заболел', compensation=False)
    resp = superadmin_client.patch(
        _detail(r.id), {'name': ' Болеет ', 'compensation': True, 'position': 4}, format='json')
    assert resp.status_code == 200
    assert resp.json() == {'id': r.id, 'side': 'client', 'name': 'Болеет',
                           'position': 4, 'compensation': True}
    r.refresh_from_db()
    assert (r.name, r.compensation, r.position) == ('Болеет', True, 4)


def test_patch_partial_keeps_other_fields(superadmin_client, make_reason):
    r = make_reason(side='client', name='Заболел', compensation=True)
    superadmin_client.patch(_detail(r.id), {'compensation': False}, format='json')
    r.refresh_from_db()
    assert (r.name, r.compensation) == ('Заболел', False)


@pytest.mark.parametrize('body', [
    {'side': 'teacher'},
    {'name': '   '},
    {'name': 'А' * 201},
    {'unknown': 1},
    {'position': 'первая'},
])
def test_patch_invalid(superadmin_client, make_reason, body):
    r = make_reason(side='client', name='Заболел')
    resp = superadmin_client.patch(_detail(r.id), body, format='json')
    assert resp.status_code == 400
    r.refresh_from_db()
    assert (r.side, r.name) == ('client', 'Заболел')


def test_patch_rename_to_duplicate(superadmin_client, make_reason):
    make_reason(side='client', name='Заболел')
    other = make_reason(side='client', name='Уехал')
    resp = superadmin_client.patch(_detail(other.id), {'name': 'ЗАБОЛЕЛ'}, format='json')
    assert resp.status_code == 400
    assert resp.json()['details'] == {'name': ['Такая причина уже есть.']}
    other.refresh_from_db()
    assert other.name == 'Уехал'


def test_patch_rename_case_of_itself_ok(superadmin_client, make_reason):
    r = make_reason(side='client', name='заболел')
    resp = superadmin_client.patch(_detail(r.id), {'name': 'Заболел'}, format='json')
    assert resp.status_code == 200
    assert resp.json()['name'] == 'Заболел'


def test_patch_missing_or_deleted_404(superadmin_client, make_reason):
    from django.utils import timezone

    gone = make_reason(name='Уехал', deleted_at=timezone.now())
    assert superadmin_client.patch(_detail(gone.id), {'name': 'X'},
                                   format='json').status_code == 404
    assert superadmin_client.patch(_detail(10 ** 9), {'name': 'X'},
                                   format='json').status_code == 404
    gone.refresh_from_db()
    assert gone.name == 'Уехал'


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

def test_delete_is_soft_and_hides(superadmin_client, make_reason):
    r = make_reason(name='Заболел')
    resp = superadmin_client.delete(_detail(r.id))
    assert resp.status_code == 204
    r.refresh_from_db()
    assert r.deleted_at is not None
    assert superadmin_client.get(URL).json()['client'] == []


def test_delete_twice_404(superadmin_client, make_reason):
    r = make_reason(name='Заболел')
    assert superadmin_client.delete(_detail(r.id)).status_code == 204
    assert superadmin_client.delete(_detail(r.id)).status_code == 404
    assert superadmin_client.delete(_detail(10 ** 9)).status_code == 404


def test_name_reusable_after_delete(superadmin_client, make_reason):
    r = make_reason(name='Заболел')
    superadmin_client.delete(_detail(r.id))
    resp = _create(superadmin_client, name='Заболел')
    assert resp.status_code == 201
    assert resp.json()['id'] != r.id


def test_delete_used_reason_keeps_records(superadmin_client, make_reason, make_record, school):
    """Использованная причина удаляется мягко: запись об отмене хранит ссылку и
    снимок текста."""
    import datetime

    r = make_reason(name='Заболел')
    rec = make_record(group_id=school['group_id'], teacher_id=school['teacher_id'],
                      lesson_date=datetime.date(2026, 11, 10), reason=r)
    assert superadmin_client.delete(_detail(r.id)).status_code == 204
    rec.refresh_from_db()
    assert (rec.reason_id, rec.reason_text) == (r.id, 'Заболел')


# ---------------------------------------------------------------------------
# Журнал изменений
# ---------------------------------------------------------------------------

def _last_context_id():
    from apps.cancellations.models import CancellationReasonEvent

    return (CancellationReasonEvent.objects.filter(pgh_context_id__isnull=False)
            .order_by('-pgh_id').values_list('pgh_context_id', flat=True).first())


@pytest.mark.parametrize('method, operation', [
    ('post', 'cancellation_reason.create'),
    ('patch', 'cancellation_reason.update'),
    ('delete', 'cancellation_reason.delete'),
])
def test_changelog_operation(superadmin_client, make_reason, method, operation):
    r = make_reason(name='Заболел')
    if method == 'post':
        _create(superadmin_client, name='Уехал')
    elif method == 'patch':
        superadmin_client.patch(_detail(r.id), {'compensation': False}, format='json')
    else:
        superadmin_client.delete(_detail(r.id))

    context_id = _last_context_id()
    assert context_id is not None
    detail = superadmin_client.get(f'/api/admin/changelog/{context_id}').json()
    assert detail['operation'] == operation
    assert detail['revertable'] is False


# ---------------------------------------------------------------------------
# Ревью B1–B3: границы позиции, пустой PATCH, нормализация имени
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('position', [10001, 2147483648, -1])
def test_patch_position_out_of_range_400(superadmin_client, make_reason, position):
    """За пределами int32 БД ответила бы DataError → 500; держим разумный потолок."""
    r = make_reason(side='client', name='Заболел', position=3)
    resp = superadmin_client.patch(_detail(r.id), {'position': position}, format='json')
    assert resp.status_code == 400
    assert 'position' in resp.json()['details']
    r.refresh_from_db()
    assert r.position == 3


def test_patch_position_upper_bound_accepted(superadmin_client, make_reason):
    r = make_reason(side='client', name='Заболел')
    resp = superadmin_client.patch(_detail(r.id), {'position': 10000}, format='json')
    assert resp.status_code == 200
    assert resp.json()['position'] == 10000


@pytest.mark.parametrize('body', [{}, None])
def test_patch_empty_400_without_changelog_event(superadmin_client, make_reason, body):
    """Пустой PATCH ничего не меняет — и не должен оставлять пустую операцию в журнале."""
    from apps.cancellations.models import CancellationReasonEvent

    r = make_reason(side='client', name='Заболел')
    before = CancellationReasonEvent.objects.count()
    resp = superadmin_client.patch(_detail(r.id), body, format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == 'Нечего менять.'
    assert CancellationReasonEvent.objects.count() == before


def test_create_collapses_inner_whitespace(superadmin_client):
    resp = _create(superadmin_client, name='a\nb')
    assert resp.status_code == 201
    assert resp.json()['name'] == 'a b'


def test_duplicate_detected_after_whitespace_normalisation(superadmin_client, make_reason):
    make_reason(side='client', name='Заболел ребёнок')
    resp = _create(superadmin_client, name='Заболел  ребёнок')
    assert resp.status_code == 400
    assert resp.json()['details'] == {'name': ['Такая причина уже есть.']}


@pytest.mark.parametrize('name', ['​', '﻿ ​', '⁠'])
def test_create_invisible_only_name_400(superadmin_client, name):
    from apps.cancellations.models import CancellationReason

    resp = _create(superadmin_client, name=name)
    assert resp.status_code == 400
    assert 'name' in resp.json()['details']
    assert not CancellationReason.objects.exists()


def test_create_strips_invisible_format_chars(superadmin_client):
    resp = _create(superadmin_client, name='​Заболел﻿')
    assert resp.status_code == 201
    assert resp.json()['name'] == 'Заболел'


def test_patch_name_normalised(superadmin_client, make_reason):
    r = make_reason(side='client', name='Заболел')
    resp = superadmin_client.patch(_detail(r.id), {'name': ' Уехал \t на  дачу '}, format='json')
    assert resp.status_code == 200
    assert resp.json()['name'] == 'Уехал на дачу'


@pytest.mark.parametrize('existing, candidate', [
    ('Заболел', 'ЗАБОЛЕЛ'), ('Ёлка', 'ёлка'), ('Οδος', 'ΟΔΟΣ'), ('straße', 'STRASSE'),
    ('Заболел', 'Заболела'),
])
def test_precheck_agrees_with_unique_constraint(make_reason, existing, candidate):
    """Предпроверка дубля сравнивает так же, как UNIQUE (lower(name)): иначе для
    пар, где UPPER и LOWER расходятся, она отказывала бы там, где БД разрешает, или
    пропускала то, что БД отклонит (тогда — 400 уже из перехвата IntegrityError)."""
    from django.db import IntegrityError, transaction

    from apps.cancellations import repository

    make_reason(side='client', name=existing)
    taken = repository.active_name_taken('client', candidate)
    try:
        with transaction.atomic():
            make_reason(side='client', name=candidate)
        db_rejects = False
    except IntegrityError:
        db_rejects = True
    assert taken is db_rejects


def test_update_locks_reason_without_key_lock(make_reason):
    """Правка причины берёт FOR NO KEY UPDATE: он совместим с FOR KEY SHARE,
    который ставит на причину INSERT записи об отмене (FK), — справочник не
    блокирует отмены уроков."""
    from django.db import connection
    from django.test.utils import CaptureQueriesContext

    from apps.cancellations import services

    r = make_reason(name='Заболел')
    with CaptureQueriesContext(connection) as ctx:
        services.update_reason(r.id, {'compensation': False})
    locks = [q['sql'] for q in ctx.captured_queries if 'FOR ' in q['sql']
             and '"cancellation_reasons"' in q['sql']]
    assert locks and all('FOR NO KEY UPDATE' in sql for sql in locks)
