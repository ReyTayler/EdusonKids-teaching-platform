"""
Функции переноса availability.0002 (TimeField → минуты от полуночи).

Имя модуля миграции начинается с цифры — грузим через importlib (как
apps/payments/tests/test_payments_constraints.py). Сам перенос на живых
данных проверен прогоном migrate вперёд/назад; здесь — его правила.
"""
import datetime as dt
import importlib

import pytest
from django.apps import apps as global_apps

from apps.availability.models import AvailabilityOverride, AvailabilityRule

mig = importlib.import_module('apps.availability.migrations.0002_minutes_from_midnight')


@pytest.mark.parametrize('t,minute', [
    (dt.time(0, 0), 0),
    (dt.time(8, 30), 510),
    (dt.time(22, 0), 1320),
    (dt.time(23, 30), 1410),
    (None, None),          # «день закрыт» у исключения
])
def test_time_to_minute(t, minute):
    assert mig.time_to_minute(t) == minute


def test_round_trip_every_slot():
    for minute in range(0, 1440, 30):
        assert mig.time_to_minute(mig.minute_to_time(minute)) == minute
    assert mig.minute_to_time(None) is None


@pytest.mark.parametrize('minute', [1440, 1470, -30])
def test_minute_to_time_rejects_unrepresentable(minute):
    with pytest.raises(ValueError, match='TimeField'):
        mig.minute_to_time(minute)


@pytest.mark.django_db
def test_ensure_reversible_blocks_end_of_day(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_minute=1380, end_minute=1440)
    with pytest.raises(RuntimeError, match='08:00–22:00') as exc:
        mig.ensure_reversible(global_apps)
    msg = str(exc.value)
    # Видно, где именно мешающие строки: и сама модель, и её история.
    assert 'AvailabilityRule: 1' in msg
    assert 'AvailabilityRuleEvent: 1' in msg and '24:00' in msg


@pytest.mark.django_db
@pytest.mark.parametrize('start,end', [(7 * 60, 8 * 60), (21 * 60, 22 * 60 + 30)])
def test_ensure_reversible_blocks_outside_legacy_grid(teacher, start, end):
    """Откат вернёт CHECK 08:00–22:00 из 0001 — такие окна его бы уронили."""
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_minute=start, end_minute=end)
    with pytest.raises(RuntimeError, match='в пределах 08:00–22:00') as exc:
        mig.ensure_reversible(global_apps)
    assert 'AvailabilityRule: 1' in str(exc.value)
    assert 'Event' not in str(exc.value)   # история с такими окнами откату не мешает


@pytest.mark.django_db
def test_ensure_reversible_blocks_override_out_of_grid(teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                        start_minute=0, end_minute=1440)
    with pytest.raises(RuntimeError, match='AvailabilityOverride: 1'):
        mig.ensure_reversible(global_apps)


@pytest.mark.django_db
def test_ensure_reversible_history_only_says_so(teacher):
    """Окно до 24:00 удалено — в графике чисто, мешает только история."""
    rule = AvailabilityRule.objects.create(teacher=teacher, day_of_week=1,
                                           start_minute=1380, end_minute=1440)
    rule.delete()
    with pytest.raises(RuntimeError) as exc:
        mig.ensure_reversible(global_apps)
    msg = str(exc.value)
    assert 'только история журнала изменений' in msg
    assert 'AvailabilityRuleEvent: 2' in msg    # insert + delete
    assert 'вне 08:00–22:00' not in msg


@pytest.mark.django_db
def test_ensure_reversible_allows_regular_windows(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_minute=8 * 60, end_minute=22 * 60)
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7))
    mig.ensure_reversible(global_apps)
