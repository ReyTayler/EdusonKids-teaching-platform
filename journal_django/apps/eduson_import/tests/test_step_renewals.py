"""Шаг «продления»: штатная пересборка KOTOKOD по посещаемости + статусы Eduson
через сервисы renewals (спека §5.4 «Продления»).

Оплат импорт не создаёт (решение владельца) — баланс у всех учеников ≤ 0, поэтому
сценарии проверяют и то, что ворота воронки KOTOKOD соблюдены, а не обойдены.

Режим Eduson — гард оплат выключен (settings.PAYMENT_BALANCE_GUARD=False, фикстура
закрепляет его независимо от окружения): долг не уводит сделку в «Ждём оплату»,
открытая сделка стоит на прогресс-стадии по числу уроков цикла."""
from __future__ import annotations

import datetime as dt

import pytest

from apps.core.utils.dates import MSK
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.steps.renewals import import_renewals
from apps.eduson_import.tests.factory import EdusonDB
from apps.renewals import engine
from apps.renewals.models import RenewalActivity, RenewalDeal

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures('payment_balance_guard_off')]

AS_OF = dt.date(2026, 10, 18)
PAST = ['2026-07-06', '2026-07-13', '2026-07-20', '2026-07-27', '2026-08-03']


def _group(db: EdusonDB, gid: int, held: int, *, active: int = 1, status: str | None = None,
           note: str = '', updated_at: str = '2026-09-10T08:00:00.000Z') -> None:
    """Индивидуальная группа с одним учеником и held проведёнными уроками."""
    db.group(gid, f'ПИ1{gid:03d}', 'python', 1, active=active)
    db.participant(gid, f'Ребёнок{gid}', f'Родитель{gid}', f'7999000{gid:04d}')
    for i in range(held):
        db.session(gid * 100 + i, gid, PAST[i])
    if status is not None:
        db.renewal(gid, status, note=note, updated_at=updated_at)


def _run(db: EdusonDB) -> ImportContext:
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
        import_renewals(src, ctx)
    return ctx


def _student(ctx: ImportContext, gid: int) -> int:
    from apps.memberships.models import GroupMembership
    return GroupMembership.objects.get(group_id=ctx.group_by_eduson[gid]).student_id


def _deals(student_id: int) -> list[RenewalDeal]:
    return list(RenewalDeal.objects.filter(student_id=student_id).select_related('stage').order_by('cycle_no'))


def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    return db


# ── пять статусов на завершённом цикле (4 урока — сделка на «Ждём продление») ──

def test_all_statuses_mapped_to_kotokod_stages(tmp_path):
    db = _db(tmp_path)
    for gid, status in enumerate(['new', 'contacted', 'paused', 'renewed', 'declined'], start=1):
        _group(db, gid, held=4, status=status)
    ctx = _run(db)

    def last_deal(gid):
        return _deals(_student(ctx, gid))

    new, contacted, paused = last_deal(1), last_deal(2), last_deal(3)
    renewed, declined = last_deal(4), last_deal(5)
    # new: пересборка уже поставила «Ждём продление» — переводить некуда.
    assert [(d.cycle_no, d.stage.key, d.outcome_at is None) for d in new] == [(1, 'awaiting_renewal', True)]
    assert [(d.stage.key, d.outcome_at) for d in contacted] == [('thinking', None)]
    assert [(d.stage.key, d.outcome_at, d.frozen_until_month) for d in paused] == [('frozen', None, None)]
    # renewed: закрыта «Продлён» + сервис открыл следующий цикл (как ручной перевод в UI).
    # Новый цикл: в нём ещё 0 уроков → «Не было урока» (без гарда долг не маршрутизирует).
    assert [(d.cycle_no, d.stage.key) for d in renewed] == [(1, 'renewed'), (2, 'no_lesson_yet')]
    assert renewed[0].outcome_at is not None and renewed[1].outcome_at is None
    assert [(d.stage.key, d.outcome_at is not None) for d in declined] == [('churned', True)]
    # Переход записан сервисом — активность смены стадии без автора («Система»).
    moved = RenewalActivity.objects.filter(kind='stage_change', deal__student_id=_student(ctx, 2)).get()
    assert moved.to_stage.key == 'thinking' and moved.author_id is None
    assert not ctx.manual_review


def test_outcome_date_is_eduson_updated_at(tmp_path):
    db = _db(tmp_path)
    # 21:30 UTC 9 сентября = 00:30 МСК 10 сентября: день берётся по Москве.
    _group(db, 1, held=4, status='renewed', updated_at='2026-09-09T21:30:00.000Z')
    _group(db, 2, held=4, status='declined', updated_at='2026-08-15T09:00:00.000Z')
    ctx = _run(db)
    won = _deals(_student(ctx, 1))[0]
    lost = _deals(_student(ctx, 2))[0]
    assert won.outcome_at == dt.datetime(2026, 9, 10, 12, 0, tzinfo=MSK)
    assert lost.outcome_at == dt.datetime(2026, 8, 15, 12, 0, tzinfo=MSK)


def test_note_becomes_deal_comment(tmp_path):
    db = _db(tmp_path)
    _group(db, 1, held=4, status='contacted', note='  перезвонить после каникул ')
    _group(db, 2, held=4, status='renewed', note='оплатят в октябре')
    _group(db, 3, held=4, status='new')
    ctx = _run(db)
    c1 = RenewalActivity.objects.get(kind='comment', deal__student_id=_student(ctx, 1))
    assert c1.body == 'Заметка Eduson: перезвонить после каникул' and c1.author_id is None
    assert c1.deal.stage.key == 'thinking'
    # У закрытой «Продлён» заметка остаётся на закрытой сделке, не на новом цикле.
    c2 = RenewalActivity.objects.get(kind='comment', deal__student_id=_student(ctx, 2))
    assert (c2.deal.cycle_no, c2.deal.stage.key) == (1, 'renewed')
    assert not RenewalActivity.objects.filter(kind='comment', deal__student_id=_student(ctx, 3)).exists()


def test_awaiting_renewal_not_moved_by_engine(tmp_path):
    """«Ждём продление» — авто-стадия: движок не перебрасывает её сразу (into = 4)."""
    db = _db(tmp_path)
    _group(db, 1, held=4, status='new')
    ctx = _run(db)
    sid = _student(ctx, 1)
    engine.sync_lesson_stage(sid)
    [deal] = _deals(sid)
    assert (deal.cycle_no, deal.stage.key, deal.due_at) == (1, 'awaiting_renewal', dt.date(2026, 7, 27))


# ── ворота воронки KOTOKOD ──────────────────────────────────────────────────

def test_mid_cycle_status_blocked_by_kotokod_rules_goes_to_review(tmp_path):
    """3 урока из 4: «Думает» и «Продлён» KOTOKOD ставит только по завершённому циклу —
    статус не применяется (ворота не обходятся), заметка не теряется, человеку в отчёт."""
    db = _db(tmp_path)
    _group(db, 1, held=3, status='contacted', note='думают')
    _group(db, 2, held=3, status='new')
    _group(db, 3, held=3, status='paused')          # пауза разрешена посреди цикла
    _group(db, 4, held=3, status='declined')        # «Ушёл» — всегда
    ctx = _run(db)
    assert [d.stage.key for d in _deals(_student(ctx, 1))] == ['lesson_3']
    assert RenewalActivity.objects.get(kind='comment', deal__student_id=_student(ctx, 1)).body == 'Заметка Eduson: думают'
    assert [d.stage.key for d in _deals(_student(ctx, 2))] == ['lesson_3']
    assert [d.stage.key for d in _deals(_student(ctx, 3))] == ['frozen']
    assert [d.stage.key for d in _deals(_student(ctx, 4))] == ['churned']
    review = '\n'.join(ctx.manual_review)
    assert 'ПИ1001' in review and 'contacted' in review
    assert 'ПИ1002' in review and 'new' in review
    assert 'ПИ1003' not in review and 'ПИ1004' not in review
    assert 'думают' not in review                     # текст заметки (ПДн) в отчёт не попадает


def test_student_without_open_deal(tmp_path):
    """Архивная группа: пересборка закрыла сделку «Ушёл». declined совпадает — дата и
    заметка ложатся на неё; renewed — противоречит, на ручную проверку без создания сделки."""
    db = _db(tmp_path)
    _group(db, 1, held=3, active=0, status='declined', note='ушли в другую школу',
           updated_at='2026-08-20T09:00:00.000Z')
    _group(db, 2, held=3, active=0, status='renewed')
    _group(db, 3, held=0, active=0, status='contacted', note='нет уроков')
    ctx = _run(db)
    [lost] = _deals(_student(ctx, 1))
    assert lost.stage.key == 'churned'
    assert lost.outcome_at == dt.datetime(2026, 8, 20, 12, 0, tzinfo=MSK)
    assert RenewalActivity.objects.filter(kind='comment', deal=lost).count() == 1
    assert [d.stage.key for d in _deals(_student(ctx, 2))] == ['churned']
    assert _deals(_student(ctx, 3)) == []          # ни посещений, ни активного членства — сделок нет
    review = '\n'.join(ctx.manual_review)
    assert 'ПИ1002' in review and 'ПИ1003' in review and 'ПИ1001' not in review


def test_students_without_eduson_renewal_get_deals_from_rebuild(tmp_path):
    db = _db(tmp_path)
    _group(db, 1, held=2)                           # активный, без записи renewals
    _group(db, 2, held=5)                           # перешагнул рубеж: цикл 1 продлён по факту
    _group(db, 3, held=0)                           # активный без уроков
    ctx = _run(db)
    assert [(d.cycle_no, d.stage.key) for d in _deals(_student(ctx, 1))] == [(1, 'lesson_2')]
    assert [(d.cycle_no, d.stage.key) for d in _deals(_student(ctx, 2))] == [(1, 'renewed'), (2, 'lesson_1')]
    assert [(d.cycle_no, d.stage.key) for d in _deals(_student(ctx, 3))] == [(1, 'no_lesson_yet')]
    # Без гарда оплат ни одна сделка импорта не уходит в «Ждём оплату».
    assert not RenewalDeal.objects.filter(stage__key='awaiting_payment').exists()
    assert any('пересборка' in line for line in ctx.report)


def test_group_without_students_goes_to_review(tmp_path):
    db = _db(tmp_path)
    db.group(1, 'СИ1019', 'scratch', 1)
    db.empty_participant(1)
    db.renewal(1, 'contacted', note='x')
    ctx = _run(db)
    assert RenewalDeal.objects.count() == 0
    assert any('СИ1019' in line for line in ctx.manual_review)


def test_unknown_status_stops_import(tmp_path):
    db = _db(tmp_path)
    _group(db, 1, held=4, status='lost-forever')
    with pytest.raises(ImportDataError, match='статус'):
        _run(db)


def test_renewal_of_missing_group_stops_import(tmp_path):
    db = _db(tmp_path)
    _group(db, 1, held=4)
    db.renewal(99, 'new')
    with pytest.raises(ImportDataError, match='#99'):
        _run(db)


def test_one_student_in_two_groups_gets_first_status_only(tmp_path):
    """Склеенный ученик в двух группах с разными статусами: применяется первая
    запись (по group_id), вторая — на проверку; группа считается применённой, только
    если статус встал всем её ученикам."""
    db = _db(tmp_path)
    _group(db, 1, held=4, status='paused')
    db.group(2, 'ПИ1002', 'python', 1)
    db.participant(2, 'Ребёнок1', 'Родитель1', '79990000001')          # тот же ребёнок
    db.renewal(2, 'declined')
    ctx = _run(db)
    sid = _student(ctx, 1)
    assert RenewalDeal.objects.filter(student_id=sid).count() == 1
    assert [d.stage.key for d in _deals(sid)] == ['frozen']
    assert any('ПИ1002' in line and 'ПИ1001' in line for line in ctx.manual_review)
    assert ctx.renewals_applied == 1


@pytest.mark.parametrize('updated_at', ['2026-06-01T09:00:00.000Z', '2026-11-01T09:00:00.000Z'])
def test_outcome_date_outside_cycle_not_set(tmp_path, updated_at):
    """Дата Eduson раньше первого урока цикла или позже дня импорта — не ставится."""
    db = _db(tmp_path)
    _group(db, 1, held=4, status='declined', updated_at=updated_at)
    ctx = _run(db)
    [lost] = _deals(_student(ctx, 1))
    assert lost.stage.key == 'churned'
    assert lost.outcome_at.astimezone(MSK).date() not in (dt.date(2026, 6, 1), dt.date(2026, 11, 1))
    assert any('ПИ1001' in line and 'вне цикла' in line for line in ctx.manual_review)
