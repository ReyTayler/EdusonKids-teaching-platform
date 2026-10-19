"""Снимок направлений цикла (RenewalDeal.directions_snapshot).

До него направления сделки читались вживую из активных членств, поэтому у
закрытой сделки они менялись задним числом: ученик перешёл на другой курс —
и архив показывал новый курс, к тому циклу не относившийся.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.renewals import engine, repository as repo
from apps.renewals.models import RenewalDeal, RenewalStage


def _deactivate_memberships(student_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('UPDATE group_memberships SET active = false WHERE student_id = %s',
                    [student_id])


def _make_group(direction_id: int, teacher_id: int, name: str) -> int:
    """Через ORM, а не raw SQL: у `groups` хватает NOT NULL-колонок с дефолтами
    только на уровне модели, и ручной INSERT ломается на каждой новой."""
    from django.utils import timezone
    from apps.groups.models import Group
    return Group.objects.create(
        name=name, direction_id=direction_id, teacher_id=teacher_id,
        active=True, is_individual=False, created_at=timezone.now()).id


def _enroll(student_id: int, group_id: int) -> None:
    with connection.cursor() as cur:
        cur.execute('INSERT INTO group_memberships (student_id, group_id, active) '
                    'VALUES (%s,%s,true)', [student_id, group_id])


@pytest.fixture
def student_on_course(db, make_student, make_direction, make_teacher):
    """Ученик, записанный на одно направление, + открытая сделка на «Урок 1»."""
    sid = make_student('__snapshot_stud__')
    did = make_direction('__snapshot_dir__')
    tid = make_teacher('__snapshot_teacher__')
    gid = _make_group(did, tid, '__snapshot_group__')
    _enroll(sid, gid)
    lesson_1 = RenewalStage.objects.get(pipeline__is_default=True, key='lesson_1')
    deal = RenewalDeal.objects.create(
        student_id=sid, cycle_no=1, pipeline_id=lesson_1.pipeline_id, stage=lesson_1)
    yield {'student_id': sid, 'direction_id': did, 'group_id': gid, 'deal': deal}
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [sid])
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_manual_move_snapshots_directions(student_on_course):
    """Ручной переход снимает направления цикла."""
    deal = student_on_course['deal']
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    repo.move_deal(deal.id, churned.id, None, None)
    deal.refresh_from_db()
    assert deal.directions_snapshot == [student_on_course['direction_id']]


@pytest.mark.django_db
def test_snapshot_survives_leaving_the_group(student_on_course):
    """Главное: после ухода из группы карточка показывает курс цикла, а не пустоту."""
    deal = student_on_course['deal']
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    repo.move_deal(deal.id, churned.id, None, None)

    _deactivate_memberships(student_on_course['student_id'])

    names = [d['name'] for d in repo.deal_computed(deal.id)['directions']]
    assert names == ['__snapshot_dir__']


@pytest.mark.django_db
def test_without_snapshot_directions_stay_live(student_on_course):
    """Пока снимка нет, поведение прежнее — живые активные членства."""
    deal = student_on_course['deal']
    assert deal.directions_snapshot is None
    names = [d['name'] for d in repo.deal_computed(deal.id)['directions']]
    assert names == ['__snapshot_dir__']

    _deactivate_memberships(student_on_course['student_id'])
    assert repo.deal_computed(deal.id)['directions'] == []


@pytest.mark.django_db
def test_snapshot_is_written_once(student_on_course, make_direction, make_teacher):
    """Второй переход снимок НЕ перезаписывает: цикл был про первый курс."""
    deal = student_on_course['deal']
    frozen = RenewalStage.objects.get(pipeline__is_default=True, key='frozen')
    repo.move_deal(deal.id, frozen.id, None, None, frozen_until_month=None)
    deal.refresh_from_db()
    first = deal.directions_snapshot

    # ученика перевели на другой курс...
    _deactivate_memberships(student_on_course['student_id'])
    other = make_direction('__snapshot_dir_2__')
    tid2 = make_teacher('__snapshot_teacher_2__')
    gid2 = _make_group(other, tid2, '__snapshot_group_2__')
    try:
        churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
        repo.move_deal(deal.id, churned.id, None, None)
        deal.refresh_from_db()
        assert deal.directions_snapshot == first
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM groups WHERE id = %s', [gid2])


@pytest.mark.django_db
def test_snapshot_uses_live_names(student_on_course):
    """Имя направления берётся джойном: переименование видно и в снимке."""
    deal = student_on_course['deal']
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    repo.move_deal(deal.id, churned.id, None, None)

    with connection.cursor() as cur:
        cur.execute('UPDATE directions SET name = %s WHERE id = %s',
                    ['__snapshot_dir_renamed__', student_on_course['direction_id']])

    names = [d['name'] for d in repo.deal_computed(deal.id)['directions']]
    assert names == ['__snapshot_dir_renamed__']


@pytest.mark.django_db
def test_board_filter_matches_snapshot(student_on_course):
    """Фильтр по направлению согласован со снимком, а не с живыми членствами."""
    deal = student_on_course['deal']
    frozen = RenewalStage.objects.get(pipeline__is_default=True, key='frozen')
    repo.move_deal(deal.id, frozen.id, None, None, frozen_until_month=None)
    _deactivate_memberships(student_on_course['student_id'])

    data = repo.board({'direction_id': student_on_course['direction_id']})
    found = [c for col in data['columns'] for c in col['cards'] if c['id'] == deal.id]
    assert len(found) == 1


@pytest.mark.django_db
def test_snapshot_from_lessons_after_student_left_group(
        student_on_course, make_attendance, make_payment):
    """Ученика вывели из группы, и только ПОТОМ закрыли сделку.

    Активных членств уже нет, но уроки цикла остались — направление берём из них.
    Прежний источник (членства) в этом сценарии терял курс безвозвратно.
    """
    sid = student_on_course['student_id']
    make_payment(sid, student_on_course['direction_id'], lessons=8)
    make_attendance(sid, student_on_course['group_id'],
                    _teacher_of(student_on_course['group_id']), count=2)

    _deactivate_memberships(sid)
    assert repo.active_direction_ids(sid) == []

    deal = student_on_course['deal']
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    repo.move_deal(deal.id, churned.id, None, None)
    deal.refresh_from_db()
    assert deal.directions_snapshot == [student_on_course['direction_id']]

    names = [d['name'] for d in repo.deal_computed(deal.id)['directions']]
    assert names == ['__snapshot_dir__']


@pytest.mark.django_db
def test_lessons_of_other_cycle_do_not_leak(student_on_course, make_attendance, make_payment):
    """Снимок берёт уроки СВОЕГО цикла: 5-й урок принадлежит уже второму."""
    sid = student_on_course['student_id']
    make_payment(sid, student_on_course['direction_id'], lessons=12)
    make_attendance(sid, student_on_course['group_id'],
                    _teacher_of(student_on_course['group_id']), count=5)

    first = repo.cycle_direction_ids(sid, 1)
    second = repo.cycle_direction_ids(sid, 2)
    assert first == [student_on_course['direction_id']]
    assert second == [student_on_course['direction_id']]
    # третий цикл ещё не начинался — уроков в нём нет
    assert repo.cycle_direction_ids(sid, 3) == []


@pytest.mark.django_db
def test_backfill_command_restores_old_deals(
        student_on_course, make_attendance, make_payment):
    """Команда бэкфила восстанавливает снимок у сделки, закрытой до миграции."""
    from django.core.management import call_command

    sid = student_on_course['student_id']
    make_payment(sid, student_on_course['direction_id'], lessons=8)
    make_attendance(sid, student_on_course['group_id'],
                    _teacher_of(student_on_course['group_id']), count=2)

    # сделка «из прошлого»: закрыта, снимка нет
    deal = student_on_course['deal']
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    repo.move_deal(deal.id, churned.id, None, None)
    RenewalDeal.objects.filter(id=deal.id).update(directions_snapshot=None)
    _deactivate_memberships(sid)

    call_command('backfill_deal_directions', '--apply')

    deal.refresh_from_db()
    assert deal.directions_snapshot == [student_on_course['direction_id']]


@pytest.mark.django_db
def test_rebuild_writes_snapshot_for_closed_cycles(
        student_on_course, make_attendance, make_payment):
    """Пересбор сделок («Синхро») сносит сделки и создаёт заново — снимок он
    обязан проставить сам, иначе каждый запуск стирал бы историю направлений.

    Дёргаем _write_plan напрямую: rebuild_all() чистит ВСЕ сделки в БД, а
    eduson_test общая — тест не имеет права её опустошать.
    """
    from datetime import date as _date
    from apps.renewals.models import RenewalPipeline
    from apps.renewals.rebuild import ClosedCycle, StudentPlan, _write_plan

    sid = student_on_course['student_id']
    make_payment(sid, student_on_course['direction_id'], lessons=8)
    make_attendance(sid, student_on_course['group_id'],
                    _teacher_of(student_on_course['group_id']), count=4)
    _deactivate_memberships(sid)

    pipe = RenewalPipeline.objects.get(is_default=True)
    stages = {s.key: s for s in RenewalStage.objects.filter(pipeline=pipe)}
    plan = StudentPlan(closed=[ClosedCycle(cycle_no=1, kind='renewed',
                                           date=_date(2026, 6, 4))], open=None)

    # cycle_no=1 занят сделкой из фикстуры — освобождаем номер под пересбор
    RenewalDeal.objects.filter(id=student_on_course['deal'].id).delete()
    _write_plan(sid, plan, pipe, stages)

    rebuilt = RenewalDeal.objects.get(student_id=sid, cycle_no=1)
    assert rebuilt.directions_snapshot == [student_on_course['direction_id']]


@pytest.mark.django_db
def test_backfill_command_dry_run_writes_nothing(student_on_course):
    """Без --apply команда ничего не пишет."""
    from django.core.management import call_command

    deal = student_on_course['deal']
    call_command('backfill_deal_directions')
    deal.refresh_from_db()
    assert deal.directions_snapshot is None


@pytest.mark.django_db
def test_maturation_snapshots_directions(student_on_course, make_attendance, make_payment):
    """Автоматическая точка: цикл созрел → снимок снят без участия менеджера."""
    sid = student_on_course['student_id']
    make_payment(sid, student_on_course['direction_id'], lessons=8)
    make_attendance(sid, student_on_course['group_id'],
                    _teacher_of(student_on_course['group_id']), count=4)

    engine.sync_lesson_stage(sid)

    deal = student_on_course['deal']
    deal.refresh_from_db()
    assert deal.due_at is not None
    assert deal.directions_snapshot == [student_on_course['direction_id']]


def _teacher_of(group_id: int) -> int:
    with connection.cursor() as cur:
        cur.execute('SELECT teacher_id FROM groups WHERE id = %s', [group_id])
        return cur.fetchone()[0]
