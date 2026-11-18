import datetime as dt

import pytest

from apps.availability import services
from apps.availability.models import AvailabilityOverride, AvailabilityRule

pytestmark = pytest.mark.django_db
T = dt.time
MON = dt.date(2030, 1, 7)          # понедельник (JS day 1)
SUN = dt.date(2030, 1, 13)         # воскресенье (JS day 0)


@pytest.fixture(autouse=True)
def frozen_today(monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: dt.date(2030, 1, 1))


def test_normalize_merges_overlapping_and_adjacent():
    got = services.normalize([(T(12), T(13)), (T(10), T(11)), (T(11), T(12)), (T(15), T(16)), (T(15, 30), T(17))])
    assert got == [(T(10), T(13)), (T(15), T(17))]


def test_effective_windows_template_only(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    w = services.get_effective_windows(teacher.id, MON, SUN)
    assert w[MON] == [(T(10), T(14))]
    assert w[SUN] == []
    assert len(w) == 7


def test_override_replaces_day_and_closed_day(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=2, start_time=T(10), end_time=T(14))
    AvailabilityOverride.objects.create(teacher=teacher, date=MON, start_time=T(16), end_time=T(18))
    AvailabilityOverride.objects.create(teacher=teacher, date=MON + dt.timedelta(days=1))  # Вт закрыт
    w = services.get_effective_windows(teacher.id, MON, SUN)
    assert w[MON] == [(T(16), T(18))]
    assert w[MON + dt.timedelta(days=1)] == []


def test_has_schedule(teacher):
    assert services.has_schedule(teacher.id) is False
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    assert services.has_schedule(teacher.id) is True


def test_replace_template_normalizes_and_skips_unchanged_days(teacher):
    services.replace_template(teacher.id, {1: [(T(10), T(11)), (T(11), T(12))], 3: [(T(15), T(16))]})
    assert services.get_template(teacher.id) == {1: [(T(10), T(12))], 3: [(T(15), T(16))]}
    wed_id = AvailabilityRule.objects.get(teacher=teacher, day_of_week=3).id
    services.replace_template(teacher.id, {1: [], 3: [(T(15), T(16))]})
    assert services.get_template(teacher.id) == {3: [(T(15), T(16))]}
    # Неизменённый день не переписывался (нет лишних событий журнала).
    assert AvailabilityRule.objects.get(teacher=teacher, day_of_week=3).id == wed_id


def test_replace_week_writes_only_diff_from_template(teacher):
    services.replace_template(teacher.id, {1: [(T(10), T(14))], 2: [(T(10), T(14))]})
    services.replace_week(teacher.id, MON, {
        MON: [(T(10), T(14))],                          # = шаблон → исключения нет
        MON + dt.timedelta(days=1): [],                 # закрыт
        MON + dt.timedelta(days=2): [(T(9), T(10))],    # открыт сверх шаблона
    })
    rows = list(AvailabilityOverride.objects.filter(teacher=teacher).order_by('date')
                .values_list('date', 'start_time', 'end_time'))
    assert rows == [
        (MON + dt.timedelta(days=1), None, None),
        (MON + dt.timedelta(days=2), T(9), T(10)),
    ]


def test_replace_week_resets_day_back_to_template(teacher):
    services.replace_template(teacher.id, {1: [(T(10), T(14))]})
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    services.replace_week(teacher.id, MON, {MON: [(T(10), T(14))]})
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_replace_week_closed_then_open_never_mixes(teacher):
    """Инвариант: на дату либо одна строка «закрыт», либо интервалы — не вместе."""
    # Шаблон нужен: без него «закрыт» совпадает с шаблоном и исключение не пишется.
    services.replace_template(teacher.id, {1: [(T(8), T(9))]})
    services.replace_week(teacher.id, MON, {MON: []})
    assert list(AvailabilityOverride.objects.filter(teacher=teacher, date=MON)
                .values_list('start_time', 'end_time')) == [(None, None)]
    services.replace_week(teacher.id, MON, {MON: [(T(10), T(12))]})
    rows = list(AvailabilityOverride.objects.filter(teacher=teacher, date=MON)
                .values_list('start_time', 'end_time'))
    assert rows == [(T(10), T(12))]
    services.replace_week(teacher.id, MON, {MON: [(T(10), T(12)), (T(15), T(16))]})
    services.replace_week(teacher.id, MON, {MON: []})
    rows = list(AvailabilityOverride.objects.filter(teacher=teacher, date=MON)
                .values_list('start_time', 'end_time'))
    assert rows == [(None, None)]


def test_replace_week_unchanged_override_not_rewritten(teacher):
    services.replace_week(teacher.id, MON, {MON: [(T(9), T(10))]})
    oid = AvailabilityOverride.objects.get(teacher=teacher, date=MON).id
    services.replace_week(teacher.id, MON, {MON: [(T(9), T(10))]})
    assert AvailabilityOverride.objects.get(teacher=teacher, date=MON).id == oid


def test_replace_week_unchanged_closed_day_not_rewritten(teacher):
    services.replace_template(teacher.id, {1: [(T(10), T(14))]})
    services.replace_week(teacher.id, MON, {MON: []})
    oid = AvailabilityOverride.objects.get(teacher=teacher, date=MON).id
    services.replace_week(teacher.id, MON, {MON: []})
    assert AvailabilityOverride.objects.get(teacher=teacher, date=MON).id == oid


def test_replace_week_closed_day_without_template_is_not_override(teacher):
    """Шаблона нет → день и так закрыт; «закрыт» совпадает с шаблоном → исключения нет."""
    services.replace_week(teacher.id, MON, {MON: []})
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_replace_week_day_absent_from_payload_untouched(teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    services.replace_week(teacher.id, MON, {SUN: [(T(10), T(11))]})
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=MON, start_time__isnull=True).exists()


def test_replace_week_rejects_past_dates(teacher, monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: dt.date(2030, 1, 9))
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON, {MON: []})


def test_replace_week_rejects_date_outside_week(teacher):
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON, {SUN + dt.timedelta(days=1): []})


def test_replace_week_rejects_non_monday(teacher):
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON + dt.timedelta(days=1), {})


def test_clear_override(teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    services.clear_override(teacher.id, MON)
    assert not AvailabilityOverride.objects.exists()


def test_clear_override_past_rejected(teacher, monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: dt.date(2030, 2, 1))
    with pytest.raises(services.ScheduleError):
        services.clear_override(teacher.id, MON)


def test_archived_teacher_cannot_be_edited(teacher):
    teacher.active = False
    teacher.save(update_fields=['active'])
    with pytest.raises(services.ScheduleError):
        services.replace_template(teacher.id, {})
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON, {MON: []})
    with pytest.raises(services.ScheduleError):
        services.clear_override(teacher.id, MON)


def test_upcoming_overrides(teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2029, 12, 1))  # прошлое
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    AvailabilityOverride.objects.create(teacher=teacher, date=SUN, start_time=T(10), end_time=T(11))
    assert services.upcoming_overrides(teacher.id) == [
        {'date': MON, 'intervals': []},
        {'date': SUN, 'intervals': [(T(10), T(11))]},
    ]


def test_windows_payload_none_without_schedule(teacher):
    assert services.windows_payload(teacher.id, MON, SUN) is None
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    payload = services.windows_payload(teacher.id, MON, SUN)
    assert payload['2030-01-07'] == [['10:00', '14:00']]
    assert payload['2030-01-13'] == []


def test_other_teacher_untouched(teacher, other_teacher):
    AvailabilityRule.objects.create(teacher=other_teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    services.replace_template(teacher.id, {1: [(T(9), T(10))]})
    assert services.get_template(other_teacher.id) == {1: [(T(10), T(14))]}


# --- Валидация интервалов в доменном слое ---------------------------------

BAD_INTERVALS = [
    pytest.param((T(11), T(10)), 'раньше конца', id='reversed'),
    pytest.param((T(10), T(10)), 'раньше конца', id='zero'),
    pytest.param((T(7, 30), T(9)), 'пределы', id='before-grid'),
    pytest.param((T(21), T(22, 30)), 'пределы', id='after-grid'),
    pytest.param((T(10, 15), T(11)), '30 минутам', id='not-half-hour'),
    pytest.param((T(10, 0, 0, 500), T(11)), '30 минутам', id='microseconds'),
    pytest.param((T(10, 0, 1), T(11)), '30 минутам', id='seconds'),
]


@pytest.mark.parametrize('interval,msg', BAD_INTERVALS)
def test_replace_template_rejects_bad_interval(teacher, interval, msg):
    with pytest.raises(services.ScheduleError, match=msg):
        services.replace_template(teacher.id, {1: [interval]})
    assert not AvailabilityRule.objects.filter(teacher=teacher).exists()


@pytest.mark.parametrize('interval,msg', BAD_INTERVALS)
def test_replace_week_rejects_bad_interval(teacher, interval, msg):
    with pytest.raises(services.ScheduleError, match=msg):
        services.replace_week(teacher.id, MON, {MON: [interval]})
    assert not AvailabilityOverride.objects.filter(teacher=teacher).exists()


def test_interval_error_message_is_readable(teacher):
    with pytest.raises(services.ScheduleError) as exc:
        services.replace_template(teacher.id, {1: [(T(11), T(10))]})
    assert str(exc.value) == 'Окно 11:00–10:00: начало должно быть раньше конца.'


def test_grid_bounds_are_inclusive(teacher):
    services.replace_template(teacher.id, {1: [(T(8), T(22))]})
    assert services.get_template(teacher.id) == {1: [(T(8), T(22))]}


def test_replace_template_bad_second_day_leaves_first_day_intact(teacher):
    services.replace_template(teacher.id, {1: [(T(10), T(14))]})
    with pytest.raises(services.ScheduleError):
        services.replace_template(teacher.id, {1: [(T(9), T(10))], 2: [(T(11), T(10))]})
    assert services.get_template(teacher.id) == {1: [(T(10), T(14))]}


def test_replace_week_bad_second_day_leaves_first_day_intact(teacher):
    services.replace_week(teacher.id, MON, {MON: [(T(9), T(10))]})
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON, {
            MON: [(T(15), T(16))],
            MON + dt.timedelta(days=1): [(T(10, 15), T(11))],
        })
    rows = list(AvailabilityOverride.objects.filter(teacher=teacher)
                .values_list('date', 'start_time', 'end_time'))
    assert rows == [(MON, T(9), T(10))]


@pytest.mark.parametrize('dow', [7, -1, True, '1', None])
def test_replace_template_rejects_bad_day_key(teacher, dow):
    with pytest.raises(services.ScheduleError):
        services.replace_template(teacher.id, {dow: [(T(10), T(11))]})
    assert not AvailabilityRule.objects.filter(teacher=teacher).exists()


# --- Контракт текущей недели ------------------------------------------------

def test_replace_week_current_week_from_today(teacher, monkeypatch):
    wed = dt.date(2030, 1, 9)
    monkeypatch.setattr(services, '_today', lambda: wed)
    services.replace_week(teacher.id, MON, {
        wed + dt.timedelta(days=i): [(T(10), T(11))] for i in range(5)  # 09..13
    })
    dates = sorted(AvailabilityOverride.objects.filter(teacher=teacher).values_list('date', flat=True))
    assert dates == [wed + dt.timedelta(days=i) for i in range(5)]


def test_replace_week_today_is_allowed_yesterday_is_not(teacher, monkeypatch):
    wed = dt.date(2030, 1, 9)
    monkeypatch.setattr(services, '_today', lambda: wed)
    services.replace_week(teacher.id, MON, {wed: []})  # шаблона нет → совпало, но без ошибки
    services.replace_week(teacher.id, MON, {wed: [(T(10), T(11))]})
    assert AvailabilityOverride.objects.filter(teacher=teacher, date=wed).exists()
    with pytest.raises(services.ScheduleError):
        services.replace_week(teacher.id, MON, {wed - dt.timedelta(days=1): []})


# --- Прочее -----------------------------------------------------------------

def test_normalize_empty():
    assert services.normalize([]) == []


def test_replace_week_empty_payload_changes_nothing(teacher):
    AvailabilityOverride.objects.create(teacher=teacher, date=MON, start_time=T(9), end_time=T(10))
    before = list(AvailabilityOverride.objects.filter(teacher=teacher).values_list('id', 'date', 'start_time'))
    services.replace_week(teacher.id, MON, {})
    after = list(AvailabilityOverride.objects.filter(teacher=teacher).values_list('id', 'date', 'start_time'))
    assert after == before


def test_has_schedule_via_rule(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=3, start_time=T(10), end_time=T(11))
    assert services.has_schedule(teacher.id) is True


def test_closed_row_wins_over_stray_intervals(teacher):
    """Смесь «закрыт» + интервалы (не через сервис) читается как закрытый день."""
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    AvailabilityOverride.objects.create(teacher=teacher, date=MON)
    AvailabilityOverride.objects.create(teacher=teacher, date=MON, start_time=T(9), end_time=T(10))
    assert services.get_effective_windows(teacher.id, MON, MON) == {MON: []}


def test_effective_windows_returns_copies(teacher):
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=1, start_time=T(10), end_time=T(14))
    w = services.get_effective_windows(teacher.id, MON, MON + dt.timedelta(days=7))
    w[MON].append((T(20), T(21)))
    assert w[MON + dt.timedelta(days=7)] == [(T(10), T(14))]


@pytest.mark.parametrize('call', [
    lambda tid: services.replace_template(tid, {}),
    lambda tid: services.replace_week(tid, MON, {MON: []}),
    lambda tid: services.clear_override(tid, MON),
], ids=['replace_template', 'replace_week', 'clear_override'])
def test_missing_teacher_raises_does_not_exist(db, call):
    from apps.teachers.models import Teacher
    with pytest.raises(Teacher.DoesNotExist):
        call(2_000_000_000)
