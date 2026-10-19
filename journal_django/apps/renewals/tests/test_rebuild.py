"""Юнит на чистую раскладку plan_for_student — без БД."""
from datetime import date

from apps.renewals.rebuild import plan_for_student, target_open_stage_key

PROGRESS = ['no_lesson_yet', 'lesson_1', 'lesson_2', 'lesson_3']


def _visits(units_by_day):
    """[(day_offset, units)] → [(date, units)] на базовой дате."""
    return [(date(2026, 1, 1 + off), u) for off, u in units_by_day]


def test_target_stage_key_rules():
    assert target_open_stage_key(0, False, PROGRESS) == 'no_lesson_yet'
    assert target_open_stage_key(1, False, PROGRESS) == 'lesson_1'
    assert target_open_stage_key(3, False, PROGRESS) == 'lesson_3'
    assert target_open_stage_key(4, False, PROGRESS) == 'awaiting_renewal'
    assert target_open_stage_key(2, True, PROGRESS) == 'awaiting_payment'
    # цикл отработан + долг → продление приоритетнее оплаты
    assert target_open_stage_key(4, True, PROGRESS) == 'awaiting_renewal'


def test_active_no_lessons_opens_cycle_1_no_lesson_yet():
    plan = plan_for_student([], is_active=True, balance=10, progress_keys=PROGRESS)
    assert plan.closed == []
    assert plan.open.cycle_no == 1
    assert plan.open.stage_key == 'no_lesson_yet'


def test_active_mid_cycle_opens_progress_stage():
    # 6 уроков: цикл 1 отработан (won), 2 урока во 2-й цикл → lesson_2
    plan = plan_for_student(_visits([(0, 4), (1, 2)]), is_active=True,
                            balance=5, progress_keys=PROGRESS)
    assert [c.cycle_no for c in plan.closed] == [1]
    assert plan.closed[0].kind == 'renewed'
    assert plan.open.cycle_no == 2
    assert plan.open.stage_key == 'lesson_2'


def test_active_exactly_on_boundary_opens_awaiting_renewal():
    # ровно 16 уроков: циклы 1-3 won, цикл 4 открыт на «Ждём продление»
    plan = plan_for_student(_visits([(i, 4) for i in range(4)]), is_active=True,
                            balance=5, progress_keys=PROGRESS)
    assert [c.cycle_no for c in plan.closed] == [1, 2, 3]
    assert plan.open.cycle_no == 4
    assert plan.open.stage_key == 'awaiting_renewal'
    assert plan.open.due_date == date(2026, 1, 4)  # день 4-го урока цикла 4
    assert plan.open.entered == date(2026, 1, 4)   # встал на стадию в тот же день


def test_open_entered_is_last_lesson_for_progress():
    # 6 уроков (0..1 января): цикл 1 won, 2 урока во 2-й цикл → entered = день последнего урока
    plan = plan_for_student(_visits([(0, 4), (1, 2)]), is_active=True,
                            balance=5, progress_keys=PROGRESS)
    assert plan.open.stage_key == 'lesson_2'
    assert plan.open.entered == date(2026, 1, 2)


def test_open_entered_none_when_no_lessons():
    # активный без уроков → «нет урока цикла», исторической даты нет
    plan = plan_for_student([], is_active=True, balance=10, progress_keys=PROGRESS)
    assert plan.open.stage_key == 'no_lesson_yet'
    assert plan.open.entered is None


def test_active_over_boundary_wons_prior_and_opens_new():
    # 18 уроков (4×4 + 2): циклы 1-4 won, цикл 5 открыт на lesson_2
    plan = plan_for_student(_visits([(i, 4) for i in range(4)] + [(4, 2)]),
                            is_active=True, balance=5, progress_keys=PROGRESS)
    assert [c.cycle_no for c in plan.closed] == [1, 2, 3, 4]
    assert all(c.kind == 'renewed' for c in plan.closed)
    assert plan.open.cycle_no == 5
    assert plan.open.stage_key == 'lesson_2'


def test_active_debt_mid_cycle_opens_awaiting_payment():
    plan = plan_for_student(_visits([(0, 4), (1, 1)]), is_active=True,
                            balance=-2, progress_keys=PROGRESS)
    assert plan.open.stage_key == 'awaiting_payment'


def test_churned_partial_last_cycle_is_lost():
    # ушёл: 5 уроков (цикл 1 won, цикл 2 неполный) → цикл 2 «Ушёл»
    plan = plan_for_student(_visits([(0, 4), (1, 1)]), is_active=False,
                            balance=0, progress_keys=PROGRESS)
    assert plan.open is None
    kinds = {c.cycle_no: c.kind for c in plan.closed}
    assert kinds == {1: 'renewed', 2: 'churned'}


def test_inactive_exactly_on_boundary_opens_awaiting_renewal():
    """Ровно на рубеже решение о продлении не принято — ученик остаётся в воронке.

    Раньше все циклы закрывались «Продлён» без открытой сделки, и ученик пропадал
    из сводки: связаться с ним было неоткуда. Правило рубежа теперь одно и для
    активных, и для неактивных (решение 2026-07-30).
    """
    plan = plan_for_student(_visits([(0, 4), (1, 4)]), is_active=False,
                            balance=0, progress_keys=PROGRESS)
    assert [c.kind for c in plan.closed] == ['renewed']
    assert plan.open is not None
    assert plan.open.cycle_no == 2
    assert plan.open.stage_key == 'awaiting_renewal'


# --- Интеграция оркестратора rebuild_all (пишет в БД) ---

import pytest  # noqa: E402
from apps.renewals.models import RenewalDeal  # noqa: E402


def _patch_loaders(monkeypatch, attendance, active, balances):
    monkeypatch.setattr('apps.renewals.rebuild._load_attendance', lambda: attendance)
    monkeypatch.setattr('apps.renewals.rebuild._active_students', lambda: active)
    monkeypatch.setattr('apps.renewals.rebuild.balances_for_students',
                        lambda ids: {i: balances.get(i, 0) for i in ids})


@pytest.mark.django_db
def test_rebuild_dry_run_writes_nothing(monkeypatch, make_student):
    from apps.renewals import rebuild
    sid = make_student()
    _patch_loaders(monkeypatch,
                   attendance={sid: [(date(2026, 1, 1), 4.0), (date(2026, 1, 2), 2.0)]},
                   active={sid}, balances={sid: 5})
    before = RenewalDeal.objects.count()
    res = rebuild.rebuild_all(dry_run=True)
    assert res['dry_run'] is True
    assert res['created_won'] == 1
    assert res['created_open'] == 1
    assert RenewalDeal.objects.count() == before  # ничего не записано


@pytest.mark.django_db
def test_rebuild_apply_writes_expected_deals(monkeypatch, make_student):
    from apps.renewals import rebuild
    sid = make_student()
    _patch_loaders(monkeypatch,
                   attendance={sid: [(date(2026, 1, i + 1), 4.0) for i in range(4)] +
                                    [(date(2026, 1, 5), 2.0)]},  # 18 уроков
                   active={sid}, balances={sid: 5})
    rebuild.rebuild_all(dry_run=False)
    deals = RenewalDeal.objects.filter(student_id=sid).order_by('cycle_no')
    assert [d.cycle_no for d in deals] == [1, 2, 3, 4, 5]
    assert [d.outcome_at is None for d in deals] == [False, False, False, False, True]
    assert deals.get(cycle_no=5).stage.key == 'lesson_2'


@pytest.mark.django_db
def test_rebuild_is_idempotent(monkeypatch, make_student):
    from apps.renewals import rebuild
    sid = make_student()
    _patch_loaders(monkeypatch,
                   attendance={sid: [(date(2026, 1, i + 1), 4.0) for i in range(4)]},
                   active={sid}, balances={sid: 5})
    rebuild.rebuild_all(dry_run=False)
    first = list(RenewalDeal.objects.filter(student_id=sid)
                 .order_by('cycle_no').values_list('cycle_no', 'stage__key'))
    rebuild.rebuild_all(dry_run=False)
    second = list(RenewalDeal.objects.filter(student_id=sid)
                  .order_by('cycle_no').values_list('cycle_no', 'stage__key'))
    assert first == second
    # ровно на рубеже: цикл 4 открыт на awaiting_renewal
    assert second[-1] == (4, 'awaiting_renewal')
