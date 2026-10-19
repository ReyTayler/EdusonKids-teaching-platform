"""
Регрессия 2026-08-25 (прод): переоткрытие НЕ последней сделки
рвало нумерацию циклов.

Как было. Цикл 5 закрыт «Продлён», цикл 6 — «Ушёл». Менеджер переоткрыл цикл 5
и закрыл его «Продлён» повторно: спавн следующего цикла (repository.move_deal)
перешагнул занятый номер 6 через engine.next_open_cycle_no и создал сделку
цикла 7. Но cycle_no — не свободный счётчик, а функция от посещаемости
(cycle.open_cycle_no): при 22 отработанных уроках прогресс сделки цикла 7 равен
22 − 6×4 = −2, клампится в ноль, и карточка показывала «Не было урока» у ученика
с 22 уроками за плечами.

Инвариант, который держит этот файл: у ОТКРЫТОЙ сделки
`attended >= (cycle_no − 1) × LESSONS_PER_CYCLE`. Обе ручные точки входа,
способные его нарушить — переоткрытие закрытой сделки и ручное создание сделки
из сводки «Без сделок» — обязаны отказать, а не создавать сделку впереди
реальности. Закрыв их, «дыру» в нумерации взять неоткуда: спавн в move_deal
перешагивает только те номера, которые эти две ручки и создавали.
"""
from __future__ import annotations

import pytest
from django.db import connection
from django.utils import timezone

from apps.renewals import engine, repository, services
from apps.renewals.models import RenewalDeal, RenewalPipeline, RenewalStage
from apps.renewals.transitions import InvalidTransition

BASE = '/api/admin/renewals'


def _closed_deal(student_id: int, cycle_no: int, kind: str) -> RenewalDeal:
    """Закрытая сделка нужного вида — имитация уже прожитой истории ученика."""
    pipe = RenewalPipeline.objects.get(is_default=True)
    stage = RenewalStage.objects.filter(pipeline=pipe, kind=kind).first()
    return RenewalDeal.objects.create(
        student_id=student_id, cycle_no=cycle_no, pipeline=pipe,
        stage=stage, outcome_at=timezone.now())


def _make_group_with_membership(direction_id: int, teacher_id: int, student_id: int,
                                name: str = '__reopen_guard_group__') -> int:
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, "
            "created_at, lesson_number_offset) "
            "VALUES (%s, %s, %s, false, true, now(), 0) RETURNING id",
            [name, direction_id, teacher_id])
        group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true)", [group_id, student_id])
    return group_id


def _drop_group(group_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])


@pytest.mark.django_db
def test_reopen_refused_when_later_deal_is_closed(make_student):
    """Боевой сценарий: цикл 6 закрыт «Ушёл» → цикл 5 переоткрывать нельзя."""
    sid = make_student()
    won = _closed_deal(sid, cycle_no=5, kind='won')
    _closed_deal(sid, cycle_no=6, kind='lost')

    with pytest.raises(InvalidTransition):
        engine.reopen_deal(won.id)

    won.refresh_from_db()
    assert won.outcome_at is not None, 'сделка должна остаться закрытой'
    assert not RenewalDeal.objects.filter(student_id=sid, cycle_no=7).exists()


@pytest.mark.django_db
def test_reopen_refused_when_later_open_deal_is_not_next_cycle(make_student):
    """Открытая сделка есть, но это не порождённый цикл N+1 — переоткрытие
    создало бы вторую открытую сделку у одного ученика."""
    sid = make_student()
    won = _closed_deal(sid, cycle_no=5, kind='won')
    engine.ensure_deal(sid, cycle_no=7)

    with pytest.raises(InvalidTransition):
        engine.reopen_deal(won.id)

    won.refresh_from_db()
    assert won.outcome_at is not None


@pytest.mark.django_db
def test_reopen_api_returns_409_for_non_latest_deal(admin_client, make_student):
    sid = make_student()
    won = _closed_deal(sid, cycle_no=5, kind='won')
    _closed_deal(sid, cycle_no=6, kind='lost')

    resp = admin_client.post(f'{BASE}/{won.id}/reopen')
    assert resp.status_code == 409
    assert 'последнюю' in resp.json()['error']


@pytest.mark.django_db
def test_create_deal_refused_when_real_cycle_taken_by_closed_deal(
        make_student, make_direction, make_teacher, make_attendance):
    """Ученик отходил 6 уроков → его реальный цикл 2-й, но цикл 2 закрыт «Ушёл».
    Ручное создание обязано отказать, а не завести сделку цикла 3 (прогресс −2).

    Сценарий НЕ достижим из текущего UI (сводка «Без сделок» показывает только
    учеников без единой сделки), и это не повод удалять ни тест, ни проверку:
    правило сводки живёт в другом запросе, до 27.07.2026 оно было шире, и путь
    был живым. См. предупреждение в докстринге services.create_deal.
    """
    sid = make_student()
    did = make_direction()
    tid = make_teacher()
    gid = _make_group_with_membership(did, tid, sid)
    try:
        make_attendance(sid, gid, tid, count=6)
        _closed_deal(sid, cycle_no=1, kind='won')
        _closed_deal(sid, cycle_no=2, kind='lost')

        result = services.create_deal(sid, author_id=None)

        assert result == 'cycle_taken'
        assert not RenewalDeal.objects.filter(student_id=sid, cycle_no=3).exists()
    finally:
        _drop_group(gid)


@pytest.mark.django_db
def test_create_deal_api_returns_409_when_real_cycle_taken(
        admin_client, make_student, make_direction, make_teacher, make_attendance):
    sid = make_student()
    did = make_direction()
    tid = make_teacher()
    gid = _make_group_with_membership(did, tid, sid, name='__reopen_guard_group2__')
    try:
        make_attendance(sid, gid, tid, count=6)
        _closed_deal(sid, cycle_no=1, kind='won')
        _closed_deal(sid, cycle_no=2, kind='lost')

        resp = admin_client.post(BASE, {'student_id': sid}, format='json')
        assert resp.status_code == 409
        assert 'переоткр' in resp.json()['error'].lower()
    finally:
        _drop_group(gid)


@pytest.mark.django_db
def test_deal_detail_exposes_can_reopen_flag(make_student):
    """Дровер прячет кнопку «Переоткрыть» по этому флагу. Считается тем же
    правилом, что и гард движка, — иначе UI и бэк разъедутся и кнопка начнёт
    отдавать 409."""
    sid = make_student()
    older = _closed_deal(sid, cycle_no=5, kind='won')
    latest = _closed_deal(sid, cycle_no=6, kind='lost')

    assert repository.deal_computed(latest.id)['can_reopen'] is True
    assert repository.deal_computed(older.id)['can_reopen'] is False


@pytest.mark.django_db
def test_open_deal_is_not_reopenable(make_student):
    sid = make_student()
    deal = engine.ensure_deal(sid, cycle_no=1)
    assert repository.deal_computed(deal.id)['can_reopen'] is False
