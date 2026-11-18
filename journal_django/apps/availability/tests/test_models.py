import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from apps.availability.models import AvailabilityOverride, AvailabilityRule

pytestmark = pytest.mark.django_db
T = dt.time


@pytest.mark.parametrize('start,end', [
    (T(10, 0), T(9, 0)),     # start >= end
    (T(7, 30), T(9, 0)),     # раньше 08:00
    (T(21, 0), T(22, 30)),   # позже 22:00
    (T(10, 15), T(11, 0)),   # не кратно 30
    (T(10, 0), T(10, 0)),    # пустое окно start == end
    (T(10, 0), T(11, 15)),   # кратность нарушена только у end
    (T(10, 0, 0, 500000), T(11, 0)),  # доли секунды — не на сетке
])
def test_rule_rejects_bad_interval(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=start, end_time=end)


def test_rule_rejects_bad_weekday(teacher):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityRule.objects.create(teacher=teacher, day_of_week=7, start_time=T(10), end_time=T(11))


@pytest.mark.parametrize('start,end', [
    (T(8), T(22)),           # границы сетки целиком
    (T(21, 30), T(22)),      # последний получасовой слот
])
def test_rule_accepts_valid_interval(teacher, start, end):
    rule = AvailabilityRule.objects.create(teacher=teacher, day_of_week=0, start_time=start, end_time=end)
    assert AvailabilityRule.objects.filter(pk=rule.pk).exists()


@pytest.mark.parametrize('start,end', [
    (T(10, 0), T(9, 0)),     # start >= end
    (T(7, 30), T(9, 0)),     # раньше 08:00
    (T(21, 0), T(22, 30)),   # позже 22:00
    (T(10, 15), T(11, 0)),   # не кратно 30
])
def test_override_rejects_bad_interval(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                            start_time=start, end_time=end)


@pytest.mark.parametrize('start,end', [
    (T(10), None),           # задан только start
    (None, T(11)),           # задан только end
])
def test_override_half_null_rejected(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                            start_time=start, end_time=end)


def test_override_windows_and_closed_day_coexist(teacher):
    """Несколько окон на одну дату допустимы; «закрыт» на другой дате не мешает."""
    d1, d2 = dt.date(2030, 1, 7), dt.date(2030, 1, 8)
    AvailabilityOverride.objects.create(teacher=teacher, date=d1, start_time=T(9), end_time=T(12))
    AvailabilityOverride.objects.create(teacher=teacher, date=d1, start_time=T(14), end_time=T(18))
    AvailabilityOverride.objects.create(teacher=teacher, date=d2)
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=d1).count() == 2
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=d2, start_time__isnull=True).count() == 1


def test_override_closed_unique_per_date(teacher):
    d = dt.date(2030, 1, 7)
    AvailabilityOverride.objects.create(teacher=teacher, date=d)
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=d)
