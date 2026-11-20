"""
Сводка свободных окон преподавателей (спека 2026-11-20-admin-calendar-free-windows-summary-design.md):
окна недельного шаблона минус постоянные уроки (слоты расписания активных групп,
действующие сегодня). Исключения не учитываются, куски < 30 минут отбрасываются.
"""
import datetime as dt

import pytest
from django.utils import timezone

from apps.availability import services
from apps.availability.models import AvailabilityOverride, AvailabilityRule
from apps.directions.models import Direction
from apps.groups.models import Group, GroupScheduleSlot
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

URL = '/api/admin/work-schedule/free-windows'
TODAY = dt.date(2030, 1, 9)  # среда
MON = 1  # конвенция Вс=0


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch):
    monkeypatch.setattr(services, '_today', lambda: TODAY)


@pytest.fixture
def direction(db):
    return Direction.objects.create(name='__fw_dir__', total_lessons=8)


_seq = iter(range(10 ** 6))


def _group(teacher, direction, *, duration=90, active=True):
    return Group.objects.create(
        name=f'__fw_g_{next(_seq)}__', direction=direction, teacher=teacher,
        is_individual=False, lesson_duration_minutes=duration, active=active,
        created_at=timezone.now())


def _slot(group, dow, hhmm, *, eff_from=dt.date(2020, 1, 1), eff_to=None):
    h, m = map(int, hhmm.split(':'))
    return GroupScheduleSlot.objects.create(
        group=group, day_of_week=dow, start_time=dt.time(h, m),
        effective_from=eff_from, effective_to=eff_to)


def _rule(teacher, dow, start, end):
    def mins(s):
        h, m = map(int, s.split(':'))
        return h * 60 + m
    AvailabilityRule.objects.create(teacher=teacher, day_of_week=dow,
                                    start_minute=mins(start), end_minute=mins(end))


def _row(body, teacher):
    rows = [t for t in body['teachers'] if t['id'] == teacher.id]
    assert len(rows) == 1, f'преподаватель {teacher.id} должен быть в сводке ровно один раз'
    return rows[0]


def _days(admin_client, teacher):
    resp = admin_client.get(URL)
    assert resp.status_code == 200, resp.content
    return _row(resp.json(), teacher)['days']


# ── Вычитание уроков ────────────────────────────────────────────────────────

def test_lesson_inside_window_splits_it(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '18:00')
    _slot(_group(teacher, direction), MON, '12:00')
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00'], ['13:30', '18:00']]


def test_lesson_at_window_edge(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '18:00')
    _slot(_group(teacher, direction), MON, '10:00')
    assert _days(admin_client, teacher)[str(MON)] == [['11:30', '18:00']]


def test_lesson_crossing_window_boundaries(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    g = _group(teacher, direction)
    _slot(g, MON, '09:00')   # 09:00–10:30: заходит в окно слева
    _slot(g, MON, '11:30')   # 11:30–13:00: выходит за окно справа
    assert _days(admin_client, teacher)[str(MON)] == [['10:30', '11:30']]


def test_lesson_covering_whole_window(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '11:00')
    _rule(teacher, MON, '15:00', '16:00')
    _slot(_group(teacher, direction), MON, '09:30')  # 09:30–11:00 накрывает окно целиком
    assert _days(admin_client, teacher)[str(MON)] == [['15:00', '16:00']]


def test_half_hour_lesson_uses_group_duration(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction, duration=45), MON, '10:00')
    assert _days(admin_client, teacher)[str(MON)] == [['10:45', '12:00']]


def test_chunks_shorter_than_30_minutes_dropped(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    # 10:15–11:45: слева и справа остаётся по 15 минут — оба куска отброшены.
    _slot(_group(teacher, direction), MON, '10:15')
    assert _days(admin_client, teacher)[str(MON)] == []


def test_chunks_of_exactly_30_minutes_kept(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction, duration=60), MON, '10:30')
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '10:30'], ['11:30', '12:00']]


def test_lesson_past_midnight_clamped_to_end_of_day(admin_client, teacher, direction):
    _rule(teacher, MON, '22:00', '24:00')
    _slot(_group(teacher, direction), MON, '23:30')
    days = _days(admin_client, teacher)
    assert days[str(MON)] == [['22:00', '23:30']]
    assert days['2'] == []  # хвост урока за полночь окна вторника не трогает (их и нет)


def test_lesson_on_other_day_does_not_subtract(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction), 2, '10:00')
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00']]


def test_other_teachers_group_does_not_subtract(admin_client, teacher, other_teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(other_teacher, direction), MON, '10:00')
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00']]


# ── Что считается «постоянным уроком» ───────────────────────────────────────

def test_archived_group_not_subtracted(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction, active=False), MON, '10:00')
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00']]


def test_ended_slot_not_subtracted(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction), MON, '10:00', eff_to=TODAY - dt.timedelta(days=1))
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00']]


def test_future_slot_not_subtracted(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction), MON, '10:00', eff_from=TODAY + dt.timedelta(days=1))
    assert _days(admin_client, teacher)[str(MON)] == [['10:00', '12:00']]


def test_slot_ending_today_still_subtracted(admin_client, teacher, direction):
    _rule(teacher, MON, '10:00', '12:00')
    _slot(_group(teacher, direction), MON, '10:00', eff_from=TODAY, eff_to=TODAY)
    assert _days(admin_client, teacher)[str(MON)] == [['11:30', '12:00']]


def test_overrides_ignored(admin_client, teacher):
    _rule(teacher, MON, '10:00', '12:00')
    # Исключение на ближайший понедельник: «закрыт» — в сводке не учитывается.
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 14))
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 15),
                                        start_minute=9 * 60, end_minute=20 * 60)
    days = _days(admin_client, teacher)
    assert days[str(MON)] == [['10:00', '12:00']]
    assert days['2'] == []


# ── Состав и форма ответа ───────────────────────────────────────────────────

def test_all_seven_days_present(admin_client, teacher):
    _rule(teacher, MON, '10:00', '12:00')
    row = _row(admin_client.get(URL).json(), teacher)
    assert row['has_schedule'] is True
    assert row['name'] == teacher.name
    assert row['days'] == {'0': [], '1': [['10:00', '12:00']], '2': [], '3': [],
                           '4': [], '5': [], '6': []}


def test_teacher_without_schedule_has_null_days(admin_client, teacher):
    row = _row(admin_client.get(URL).json(), teacher)
    assert row['has_schedule'] is False
    assert row['days'] is None


def test_teacher_with_only_overrides_has_null_days(admin_client, teacher):
    # Исключения игнорируются целиком — без шаблона графика нет.
    AvailabilityOverride.objects.create(teacher=teacher, date=dt.date(2030, 1, 14),
                                        start_minute=10 * 60, end_minute=12 * 60)
    row = _row(admin_client.get(URL).json(), teacher)
    assert row['has_schedule'] is False
    assert row['days'] is None


def test_service_and_inactive_teachers_excluded(admin_client, teacher, other_teacher):
    _rule(teacher, MON, '10:00', '12:00')
    _rule(other_teacher, MON, '10:00', '12:00')
    teacher.active = False
    teacher.save(update_fields=['active'])
    other_teacher.is_service = True
    other_teacher.save(update_fields=['is_service'])
    ids = {t['id'] for t in admin_client.get(URL).json()['teachers']}
    assert teacher.id not in ids
    assert other_teacher.id not in ids


def test_teachers_sorted_by_name(admin_client, db):
    now = timezone.now()
    b = Teacher.objects.create(name='__fw_Б__', active=True, created_at=now)
    a = Teacher.objects.create(name='__fw_А__', active=True, created_at=now)
    ids = [t['id'] for t in admin_client.get(URL).json()['teachers']]
    assert ids.index(a.id) < ids.index(b.id)


# ── RBAC ────────────────────────────────────────────────────────────────────

def test_manager_allowed(manager_client):
    assert manager_client.get(URL).status_code == 200


def test_teacher_forbidden(teacher_client):
    assert teacher_client.get(URL).status_code == 403


def test_anonymous_rejected(client):
    assert client.get(URL).status_code in (401, 403)


# ── Производительность ──────────────────────────────────────────────────────

def test_fixed_number_of_queries(django_assert_num_queries, teacher, other_teacher, direction):
    """Преподаватели + правила + слоты — три запроса при любом числе преподавателей/групп."""
    for t in (teacher, other_teacher):
        _rule(t, MON, '10:00', '18:00')
        _rule(t, 2, '10:00', '18:00')
        for dow in (MON, 2):
            _slot(_group(t, direction), dow, '12:00')
    with django_assert_num_queries(3):
        result = services.free_windows_summary()
    by_id = {r['id']: r for r in result}
    assert by_id[teacher.id]['days'][str(MON)] == [['10:00', '12:00'], ['13:30', '18:00']]
    assert by_id[other_teacher.id]['days']['2'] == [['10:00', '12:00'], ['13:30', '18:00']]
