"""Сводка «Ученики без сделок» + ручное создание сделки (POST /api/admin/renewals)."""
import pytest
from django.db import connection
from django.utils import timezone

from apps.renewals import engine
from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage

BASE = '/api/admin/renewals'


def _make_group_membership(did, tid, sid, name='__un_group__'):
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
                    "lesson_number_offset) "
                    "VALUES (%s, %s, %s, false, true, now(), 0) RETURNING id", [name, did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    return gid


def _cleanup(sid, gid):
    with connection.cursor() as cur:
        cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                    '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
        cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_unassigned_requires_staff(teacher_client):
    assert teacher_client.get(f'{BASE}/unassigned').status_code == 403


@pytest.mark.django_db
def test_unassigned_lists_student_and_create_removes(manager_client, make_student,
                                                     make_direction, make_teacher):
    """Активный ученик без сделки виден в сводке; после создания — исчезает."""
    sid, did, tid = make_student('__un_student__'), make_direction(), make_teacher()
    gid = _make_group_membership(did, tid, sid)
    try:
        rows = manager_client.get(f'{BASE}/unassigned').json()
        mine = [r for r in rows if r['student_id'] == sid]
        assert mine and mine[0]['cycle_no'] == 1
        assert {'student_name', 'directions', 'attended', 'debt'} <= set(mine[0])

        created = manager_client.post(BASE, {'student_id': sid}, format='json')
        assert created.status_code == 201
        assert created.json()['cycle_no'] == 1
        assert created.json()['outcome_at'] is None

        rows = manager_client.get(f'{BASE}/unassigned').json()
        assert not [r for r in rows if r['student_id'] == sid]
    finally:
        _cleanup(sid, gid)


@pytest.mark.django_db
def test_unassigned_count_requires_staff(teacher_client):
    assert teacher_client.get(f'{BASE}/unassigned/count').status_code == 403


@pytest.mark.django_db
def test_unassigned_count_matches_list(manager_client, make_student, make_direction,
                                       make_teacher):
    """
    Бейдж «Без сделок (N)» берёт число отдельной лёгкой ручкой — она обязана
    считать тем же правилом, что и сам список (иначе бейдж и диалог разойдутся).
    """
    sid, did, tid = make_student('__un_count__'), make_direction(), make_teacher()
    gid = _make_group_membership(did, tid, sid, name='__un_count_group__')
    try:
        rows = manager_client.get(f'{BASE}/unassigned').json()
        before = manager_client.get(f'{BASE}/unassigned/count').json()
        assert any(r['student_id'] == sid for r in rows)
        assert before['count'] == len(rows)

        manager_client.post(BASE, {'student_id': sid}, format='json')

        after = manager_client.get(f'{BASE}/unassigned/count').json()
        assert after['count'] == before['count'] - 1
    finally:
        _cleanup(sid, gid)


@pytest.mark.django_db
def test_student_with_closed_deal_is_not_listed(manager_client, make_student,
                                                make_direction, make_teacher):
    """
    Сводка — только про новичков: ученик, у которого сделка когда-либо БЫЛА
    (закрыта «Ушёл» или «Продлён»), в неё не попадает, даже если членство активно.
    Такого возвращают в воронку переоткрытием его сделки, а не созданием новой —
    иначе номер цикла перешагивается и прогресс обнуляется.
    """
    sid, did, tid = make_student('__un_closed__'), make_direction(), make_teacher()
    gid = _make_group_membership(did, tid, sid, name='__un_closed_group__')
    try:
        pipe = RenewalPipeline.objects.get(is_default=True)
        lost = RenewalStage.objects.filter(pipeline=pipe, kind='lost').first()
        RenewalDeal.objects.create(student_id=sid, cycle_no=1, pipeline=pipe,
                                   stage=lost, outcome_at=timezone.now())

        rows = manager_client.get(f'{BASE}/unassigned').json()
        assert not [r for r in rows if r['student_id'] == sid]
    finally:
        _cleanup(sid, gid)


@pytest.mark.django_db
def test_boundary_cycle_creates_decision_deal(manager_client, make_student, make_direction,
                                              make_teacher, make_attendance):
    """
    Ровно 4 отработанных урока: решение по циклу 1 ещё НЕ принято, поэтому сделка
    создаётся циклом 1 на «Ждём продление», а не циклом 2 «Не было урока».
    Та же раскладка, что у пересбора (rebuild.plan_for_student) — один механизм.
    """
    sid, did, tid = make_student('__un_boundary__'), make_direction(), make_teacher()
    gid = _make_group_membership(did, tid, sid, name='__un_boundary_group__')
    try:
        make_attendance(sid, gid, tid, count=4)

        row = [r for r in manager_client.get(f'{BASE}/unassigned').json()
               if r['student_id'] == sid][0]
        assert row['attended'] == 4
        assert row['cycle_no'] == 1, 'сводка тоже должна показывать открытый цикл'

        created = manager_client.post(BASE, {'student_id': sid}, format='json').json()
        assert created['cycle_no'] == 1
        assert created['stage_key'] == 'awaiting_renewal'
    finally:
        _cleanup(sid, gid)


@pytest.mark.django_db
def test_mid_cycle_create_keeps_current_cycle(manager_client, make_student, make_direction,
                                              make_teacher, make_payment, make_attendance):
    """Цикл не добит (2 из 4) — сделка того же цикла на стадии прогресса."""
    sid, did, tid = make_student('__un_midcycle__'), make_direction(), make_teacher()
    gid = _make_group_membership(did, tid, sid, name='__un_midcycle_group__')
    try:
        make_payment(sid, did, lessons=8)  # без баланса уехало бы в «Ждём оплату»
        make_attendance(sid, gid, tid, count=2)

        created = manager_client.post(BASE, {'student_id': sid}, format='json').json()
        assert created['cycle_no'] == 1
        assert created['stage_key'] == 'lesson_2'
    finally:
        _cleanup(sid, gid)


@pytest.mark.django_db
def test_create_conflicts_when_open_deal_exists(manager_client, make_student):
    sid = make_student()
    engine.ensure_deal(sid, cycle_no=1)
    resp = manager_client.post(BASE, {'student_id': sid}, format='json')
    assert resp.status_code == 409


@pytest.mark.django_db
def test_create_unknown_student_404(manager_client):
    assert manager_client.post(BASE, {'student_id': 999999999},
                               format='json').status_code == 404


@pytest.mark.django_db
def test_create_refuses_when_closed_cycle_taken_by_returned_student(manager_client,
                                                                    make_student):
    """Вернувшийся после «Ушёл» ученик: расчётный цикл занят закрытой сделкой —
    создание отказывает, вернуть его в воронку можно только переоткрытием.

    Раньше здесь перешагивался номер цикла (сделка заводилась со следующим), и
    тест это закреплял. Правило изменено: перешагивание заводит сделку ВПЕРЕДИ
    посещаемости, её прогресс `attended − (cycle_no−1)×4` уходит в минус — ровно
    так на проде 2026-08-25 появилась сделка «Не было урока» у ученика с 22
    уроками (см. tests/test_reopen_guard.py). Переоткрытие как единственный путь
    для вернувшегося — решение пользователя от 2026-07-27, здесь оно доведено с
    уровня инструкции до уровня кода.
    """
    sid = make_student()
    pipe = RenewalPipeline.objects.get(is_default=True)
    lost = RenewalStage.objects.filter(pipeline=pipe, kind='lost').first()
    RenewalDeal.objects.create(student_id=sid, cycle_no=1, pipeline=pipe,
                               stage=lost, outcome_at=timezone.now())

    resp = manager_client.post(BASE, {'student_id': sid}, format='json')
    assert resp.status_code == 409
    assert 'переоткр' in resp.json()['error'].lower()
    assert not RenewalDeal.objects.filter(student_id=sid, cycle_no=2).exists()
