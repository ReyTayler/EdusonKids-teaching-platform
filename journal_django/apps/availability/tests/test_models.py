import datetime as dt

import pytest
from django.db import IntegrityError, transaction

from apps.availability.models import AvailabilityOverride, AvailabilityRule

pytestmark = pytest.mark.django_db


def M(h: int, m: int = 0) -> int:
    """Минута от полуночи — короче и нагляднее литералов 600/630 в тестах."""
    return h * 60 + m


@pytest.mark.parametrize('start,end', [
    (M(10), M(9)),           # start >= end
    (M(10), M(10)),          # пустое окно start == end
    (-30, M(1)),             # раньше 00:00
    (M(23, 30), 1470),       # позже 24:00
    (M(10, 15), M(11)),      # не кратно 30 (начало)
    (M(10), M(11, 15)),      # кратность нарушена только у end
    (M(10, 1), M(11)),       # «почти» получас
])
def test_rule_rejects_bad_interval(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_minute=start, end_minute=end)


def test_rule_rejects_bad_weekday(teacher):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityRule.objects.create(teacher=teacher, day_of_week=7, start_minute=M(10), end_minute=M(11))


def test_rule_rejects_null_minutes(teacher):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_minute=None, end_minute=M(11))


@pytest.mark.parametrize('start,end', [
    (0, 1440),               # сутки целиком: 00:00–24:00
    (M(23, 30), 1440),       # последний получасовой слот
    (0, 30),                 # первый получасовой слот
    (M(7, 30), M(9)),        # раньше бывшей границы 08:00
    (M(21), M(22, 30)),      # позже бывшей границы 22:00
])
def test_rule_accepts_valid_interval(teacher, start, end):
    rule = AvailabilityRule.objects.create(teacher=teacher, day_of_week=0, start_minute=start, end_minute=end)
    assert AvailabilityRule.objects.filter(pk=rule.pk).exists()


@pytest.mark.parametrize('start,end', [
    (M(10), M(9)),           # start >= end
    (-30, M(1)),             # раньше 00:00
    (M(23, 30), 1470),       # позже 24:00
    (M(10, 15), M(11)),      # не кратно 30
    (M(10), M(11, 15)),      # не кратно 30 (конец)
])
def test_override_rejects_bad_interval(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                            start_minute=start, end_minute=end)


def test_override_accepts_full_day(teacher):
    ov = AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                             start_minute=0, end_minute=1440)
    assert AvailabilityOverride.objects.filter(pk=ov.pk, end_minute=1440).exists()


@pytest.mark.parametrize('start,end', [
    (M(10), None),           # задан только start
    (None, M(11)),           # задан только end
])
def test_override_half_null_rejected(teacher, start, end):
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 7),
                                            start_minute=start, end_minute=end)


def test_override_windows_and_closed_day_coexist(teacher):
    """Несколько окон на одну дату допустимы; «закрыт» на другой дате не мешает."""
    d1, d2 = dt.date(2030, 1, 7), dt.date(2030, 1, 8)
    AvailabilityOverride.objects.create(teacher=teacher, date=d1, start_minute=M(9), end_minute=M(12))
    AvailabilityOverride.objects.create(teacher=teacher, date=d1, start_minute=M(14), end_minute=M(18))
    AvailabilityOverride.objects.create(teacher=teacher, date=d2)
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=d1).count() == 2
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=d2, start_minute__isnull=True).count() == 1


def test_override_closed_unique_per_date(teacher):
    d = dt.date(2030, 1, 7)
    AvailabilityOverride.objects.create(teacher=teacher, date=d)
    with pytest.raises(IntegrityError), transaction.atomic():
        AvailabilityOverride.objects.create(teacher=teacher, date=d)
