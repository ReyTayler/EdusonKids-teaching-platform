import pytest
from apps.renewals import cycle


def test_open_cycle_no():
    """Номер ОТКРЫТОГО цикла: ровно на рубеже решение по циклу ещё не принято."""
    assert cycle.open_cycle_no(0) == 1
    assert cycle.open_cycle_no(3.5) == 1
    assert cycle.open_cycle_no(4) == 1      # 4 урока отработаны, продление не решено
    assert cycle.open_cycle_no(4.5) == 2
    assert cycle.open_cycle_no(7.5) == 2
    assert cycle.open_cycle_no(8) == 2
    assert cycle.open_cycle_no(8.5) == 3


def test_open_cycle_no_matches_rebuild_plan():
    """
    Единый механизм: номер открытого цикла обязан совпадать с раскладкой
    пересбора (rebuild.plan_for_student) на всей сетке 0..12 с шагом 0.5 —
    иначе ручное создание сделки и «Синхро → пересобрать» разойдутся.
    """
    from datetime import date
    from apps.renewals import rebuild

    for step in range(0, 25):
        attended = step * 0.5
        visits = [(date(2026, 6, 1), 0.5)] * step
        plan = rebuild.plan_for_student(
            visits, is_active=True, balance=100,
            progress_keys=['no_lesson_yet', 'lesson_1', 'lesson_2', 'lesson_3'])
        assert plan.open is not None
        assert cycle.open_cycle_no(attended) == plan.open.cycle_no, attended


def test_in_renewal_window():
    assert cycle.in_renewal_window(remaining=1, balance=5) is True
    assert cycle.in_renewal_window(remaining=3, balance=0) is True
    assert cycle.in_renewal_window(remaining=3, balance=5) is False
