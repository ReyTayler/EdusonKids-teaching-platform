"""
Чистые unit-тесты классификации реестра (classify) — без БД.

Границы сигналов детерминированно. Поведение списка (сегмент/поиск/сортировка)
теперь на уровне БД — проверяется в test_registry_api.py на инвариантах.
"""
from __future__ import annotations

import datetime

from apps.dashboard import registry_service as svc

TODAY = datetime.date(2026, 7, 11)
RECENT = datetime.date(2026, 7, 10)   # 1 день назад — не простой
OLD = datetime.date(2026, 6, 1)       # >14 дней — простой


def test_classify_healthy_is_ok():
    f = svc.classify(balance=10, last_date=RECENT, next_date=RECENT, today=TODAY)
    assert f['status'] == 'ok'
    assert not any(f[k] for k in ('closed', 'ending', 'idle', 'no_plan'))


def test_classify_ending_boundary_two_inclusive():
    assert svc.classify(2, RECENT, RECENT, TODAY)['status'] == 'ending'
    assert svc.classify(0.5, RECENT, RECENT, TODAY)['ending'] is True
    assert svc.classify(3, RECENT, RECENT, TODAY)['ending'] is False


def test_classify_closed_at_zero_and_negative():
    assert svc.classify(0, RECENT, RECENT, TODAY)['status'] == 'closed'
    assert svc.classify(-4, RECENT, RECENT, TODAY)['closed'] is True
    assert svc.classify(0, RECENT, RECENT, TODAY)['ending'] is False


def test_classify_idle_cutoff_is_strict_14_days():
    cutoff_day = TODAY - datetime.timedelta(days=svc.IDLE_DAYS)  # ровно 14 дней
    assert svc.classify(10, cutoff_day, RECENT, TODAY)['idle'] is False       # == порог → не простой
    older = cutoff_day - datetime.timedelta(days=1)
    assert svc.classify(10, older, RECENT, TODAY)['idle'] is True


def test_classify_no_plan_when_next_missing():
    assert svc.classify(10, RECENT, None, TODAY)['no_plan'] is True
    assert svc.classify(10, RECENT, RECENT, TODAY)['no_plan'] is False


def test_classify_status_priority_closed_wins_over_idle():
    f = svc.classify(0, OLD, None, TODAY)
    assert f['status'] == 'closed'
    assert f['idle'] is True and f['no_plan'] is True   # сигналы всё равно взводятся


def test_status_rank_matches_priority_order():
    # STATUS_RANK выводится из STATUS_PRIORITY — единый источник (защита от дрейфа).
    assert svc.STATUS_RANK == {s: i for i, s in enumerate(svc.STATUS_PRIORITY)}
    assert svc.STATUS_PRIORITY[0] == 'closed' and svc.STATUS_PRIORITY[-1] == 'ok'
