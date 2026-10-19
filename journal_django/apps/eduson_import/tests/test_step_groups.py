"""Шаг «группы»: группы, слоты, ученики (склейка), заметки, членства и пройденное
до учёта (спека §5.1–5.2 с поправками §5.4)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.tests.factory import EdusonDB
from apps.groups.models import Group, GroupScheduleSlot
from apps.memberships.models import GroupMembership
from apps.students.models import Student, StudentComment

pytestmark = pytest.mark.django_db

AS_OF = dt.date(2026, 10, 18)


def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.teacher(2, 'Борис Тестов')
    return db


def _run(db: EdusonDB) -> ImportContext:
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
        import_groups(src, ctx)
    return ctx


def _group(ctx, eduson_id) -> Group:
    return Group.objects.get(pk=ctx.group_by_eduson[eduson_id])


def _slots(group) -> list[tuple[int, dt.time]]:
    return sorted(GroupScheduleSlot.objects.filter(group=group).values_list('day_of_week', 'start_time'))


def _comments(student) -> list[str]:
    return sorted(StudentComment.objects.filter(student=student).values_list('body', flat=True))


# ── группы и слоты ──────────────────────────────────────────────────────────

def test_group_fields(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='individual', package=48, start='2026-06-03',
             created_at='2026-06-01T07:30:00.000Z')
    db.group(6, 'СГ1002', 'groups', 2, fmt='group', package=None, start=None, active=0)
    db.group(7, 'СИ1003', 'scratch', 2, package=0)
    ctx = _run(db)

    g5 = _group(ctx, 5)
    assert (g5.name, g5.direction.name, g5.teacher.name, g5.is_individual) == ('ПИ1001', 'Python', 'Анна Тестова',
                                                                               True)
    assert (g5.lesson_duration_minutes, g5.lessons_total, g5.group_start_date, g5.active) == (
        60, 48, dt.date(2026, 6, 3), True)
    assert g5.created_at == dt.datetime(2026, 6, 1, 7, 30, tzinfo=dt.timezone.utc)
    g6 = _group(ctx, 6)
    assert (g6.direction.name, g6.is_individual, g6.lessons_total, g6.group_start_date, g6.active) == (
        'Группа', False, None, None, False)
    assert _group(ctx, 7).lessons_total is None              # пакет 0 — «как в направлении»
    assert any('СИ1003' in line and 'пакет' in line for line in ctx.report)


def test_slots_from_json_and_from_default_rule(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1,
             slots=[{'dow': 1, 'time': '13:00'}, {'dow': 4, 'time': '9:30'}, {'dow': 1, 'time': '13:00'}])
    db.group(6, 'ПИ1002', 'python', 1, slots=None, dow=0, time='14:40')
    db.group(7, 'ПИ1003', 'python', 1, slots=None, dow=None, time='')
    ctx = _run(db)

    g5 = _group(ctx, 5)
    assert _slots(g5) == [(1, dt.time(13, 0)), (4, dt.time(9, 30))]     # дубль слота схлопнут
    assert g5.lessons_per_week == 2
    g6 = _group(ctx, 6)
    assert (_slots(g6), g6.lessons_per_week) == ([(0, dt.time(14, 40))], 1)
    g7 = _group(ctx, 7)
    assert (_slots(g7), g7.lessons_per_week) == ([], 1)
    assert any('ПИ1003' in line and 'расписан' in line for line in ctx.report)


def test_slots_after_reschedule_are_current_rule(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, slots=[{'dow': 1, 'time': '15:00'}])
    db.session(None, 5, '2026-11-02', status='scheduled', lesson_no=1)
    db.reschedule_group(5, from_date='2026-10-20', slots=[{'dow': 2, 'time': '16:00'}, {'dow': 5, 'time': '17:30'}],
                        teacher_id=2)
    ctx = _run(db)
    g5 = _group(ctx, 5)
    assert _slots(g5) == [(2, dt.time(16, 0)), (5, dt.time(17, 30))]
    assert g5.teacher.name == 'Борис Тестов'                  # текущий преподаватель группы


@pytest.mark.parametrize('kwargs, match', [
    ({'fmt': 'mini'}, 'формат'),
    ({'slots': [{'dow': 9, 'time': '15:00'}]}, 'день недели'),
    ({'slots': [{'dow': 1, 'time': '25:99'}]}, 'время'),
    ({'start': '01.06.2026'}, 'дата'),
])
def test_bad_group_data_stops_import(tmp_path, kwargs, match):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, **kwargs)
    with pytest.raises(ImportDataError, match=f'ПИ1001.*{match}'):
        _run(db)


def test_group_without_teacher_stops_import(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', None)
    with pytest.raises(ImportDataError, match='ПИ1001.*преподавател'):
        _run(db)


# ── пройдено до учёта ───────────────────────────────────────────────────────

def test_offset_and_lessons_done(tmp_path):
    db = _db(tmp_path)
    # A: 2 фиктивных held (2027) + 3 настоящих, из них отработка 45 мин
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', done=9)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(5, 'Петя', 'Мама Пети', '79990000002')
    for day in ('2027-06-01', '2027-06-08'):
        db.session(None, 5, day, time='', lesson_no=None)
    db.session(None, 5, '2026-09-01', lesson_no=1)
    db.session(None, 5, '2026-09-08', lesson_no=2)
    db.session(None, 5, '2026-09-10', type='makeup', duration=45)
    db.session(None, 5, '2026-09-15', status='cancelled', lesson_no=3)
    db.session(None, 5, '2026-11-01', status='scheduled', lesson_no=3)
    # B: курс начат до системы — первый учтённый урок №5
    db.group(6, 'ПИ1002', 'python', 1)
    db.participant(6, 'Оля', 'Мама Оли', '79990000003')
    db.session(None, 6, '2026-09-02', lesson_no=5)
    # C: без проведённых
    db.group(7, 'ПИ1003', 'python', 1)
    db.participant(7, 'Коля', 'Мама Коли', '79990000004')
    ctx = _run(db)

    assert _group(ctx, 5).lesson_number_offset == Decimal('2')
    assert sorted(GroupMembership.objects.filter(group=_group(ctx, 5)).values_list('lessons_done', flat=True)) == [
        Decimal('4.5'), Decimal('4.5')]                       # 2 + 1 + 1 + 0.5
    assert _group(ctx, 6).lesson_number_offset == Decimal('4')
    assert GroupMembership.objects.get(group=_group(ctx, 6)).lessons_done == Decimal('5')
    assert _group(ctx, 7).lesson_number_offset == Decimal('0')
    assert GroupMembership.objects.get(group=_group(ctx, 7)).lessons_done == Decimal('0')


def test_fake_held_numbered_inside_gap_not_counted_twice(tmp_path):
    """Фиктивные №1..2, настоящие с №3: N = 2 (правило §5.4), offset и lessons_done
    на нём; на ручную проверку это больше не уходит."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1)
    db.participant(5, 'Миша', 'Мама', '79990000001')
    db.session(None, 5, '2027-06-01', lesson_no=1)
    db.session(None, 5, '2027-06-08', lesson_no=2)
    db.session(None, 5, '2026-09-01', lesson_no=3)
    ctx = _run(db)
    assert _group(ctx, 5).lesson_number_offset == Decimal('2')
    assert GroupMembership.objects.get(group=_group(ctx, 5)).lessons_done == Decimal('3')
    assert ctx.manual_review == []


# ── ученики ─────────────────────────────────────────────────────────────────

def test_student_fields_as_in_eduson(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1)
    db.participant(5, ' Миша ', ' Анна Мама ', '8 (999) 000-00-01', age=12, child_tg='@misha', child_phone='79990000009',
                   child_email='Misha@Example.com', parent_tg='https://t.me/mama', parent_email='mama@example.com',
                   amo_url='https://crm.example/leads/1')
    ctx = _run(db)
    s = Student.objects.get()
    assert (s.full_name, s.parent1_name, s.parent1_phone, s.parent1_email, s.parent1_telegram) == (
        'Миша', 'Анна Мама', '8 (999) 000-00-01', 'mama@example.com', 'https://t.me/mama')
    assert (s.phone, s.telegram, s.email, s.crm_link) == ('79990000009', '@misha', 'Misha@Example.com',
                                                          'https://crm.example/leads/1')
    assert (s.parent2_name, s.birth_date, s.manager_id) == (None, None, None)
    assert _comments(s) == ['Возраст на 18.10.2026 по данным Eduson: 12']
    assert list(ctx.student_by_participant.values()) == [s.id]


def test_same_child_in_two_groups_is_one_student(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, start='2026-06-01')
    db.group(6, 'СИ1002', 'scratch', 2, start='2026-09-01', active=0)
    p1 = db.participant(5, 'Миша', 'Анна Мама', '79990000001', age=11, amo_url='https://crm.example/1')
    p2 = db.participant(6, 'миша', '', '89990000001', age=12, child_tg='@misha', amo_url='https://crm.example/2')
    ctx = _run(db)

    s = Student.objects.get()
    assert ctx.student_by_participant == {p1: s.id, p2: s.id}
    assert (s.parent1_name, s.telegram, s.crm_link) == ('Анна Мама', '@misha', 'https://crm.example/1')
    rows = sorted(GroupMembership.objects.filter(student=s).values_list('group__name', 'active', 'start_date'))
    assert rows == [('ПИ1001', True, dt.date(2026, 6, 1)), ('СИ1002', False, dt.date(2026, 9, 1))]
    assert _comments(s) == ['Возраст на 18.10.2026 по данным Eduson: 11/12']
    # разные ссылки CRM у склеенного ученика — человеку на заметку, без значений
    assert any(f'#{p1}' in line and 'crm_link' in line and 'crm.example' not in line for line in ctx.report)


def test_namesakes_with_conflicting_age_go_to_manual_review(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1)
    db.group(6, 'ПИ1002', 'python', 1)
    p1 = db.participant(5, 'Миша', 'Мама', '79990000001', age=7)
    p2 = db.participant(6, 'Миша', 'Мама', '79990000001', age=12)
    ctx = _run(db)
    assert Student.objects.count() == 2
    assert any(f'#{p1}' in line and f'#{p2}' in line and 'возраст' in line for line in ctx.manual_review)
    assert all('Миша' not in line and '7999' not in line for line in ctx.manual_review + ctx.report)


def test_empty_participant_skipped_and_group_without_students_reported(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'СИ1019', 'scratch', 1, amo_url='https://crm.example/9')
    empty = db.empty_participant(5, amo_url='https://crm.example/9')
    ctx = _run(db)
    assert not Student.objects.exists() and not GroupMembership.objects.exists()
    assert ctx.skipped_participants == [empty]
    assert empty not in ctx.student_by_participant
    assert any(f'#{empty}' in line for line in ctx.report)
    assert any('СИ1019' in line and 'без учеников' in line for line in ctx.report)


def test_child_without_name(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1)
    p = db.participant(5, '', '', '79990000001', age=9)
    ctx = _run(db)
    s = Student.objects.get()
    assert s.full_name == 'Без имени · ПИ1001' and s.parent1_phone == '79990000001'
    assert any(f'#{p}' in line and 'имени' in line for line in ctx.manual_review)


def test_notes_become_comments_with_group_code(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, notes='  ходит с братом  ')
    db.group(6, 'ПИ1002', 'python', 1, notes='заметка группы без учеников')
    db.empty_participant(6)
    db.participant(5, 'Миша', 'Мама', '79990000001', age=None, notes='любит игры')
    ctx = _run(db)
    s = Student.objects.get()
    assert _comments(s) == ['[ПИ1001] Заметка группы: ходит с братом', '[ПИ1001] любит игры']
    assert all(c.author_id is None for c in StudentComment.objects.all())
    assert any('ПИ1002' in line and 'заметк' in line for line in ctx.report)


def test_duplicate_rows_of_one_child_in_one_group_give_one_membership(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group')
    db.participant(5, 'Миша', 'Мама', '79990000001', age=10)
    db.participant(5, 'Миша', '', '79990000001', age=10)
    _run(db)
    assert Student.objects.count() == 1
    assert GroupMembership.objects.count() == 1


def test_two_children_in_active_individual_group_stop_import(tmp_path):
    """В KOTOKOD у индивидуальной группы ≤1 активного ученика
    (apps.memberships.repository._assert_individual_capacity) — импорт его не обходит."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='individual')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(5, 'Петя', 'Мама Пети', '79990000002')
    with pytest.raises(ImportDataError, match='ПИ1001.*индивидуальн'):
        _run(db)


def test_participant_of_missing_group_stops_import(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1)
    db.participant(77, 'Миша', 'Мама', '79990000001')
    with pytest.raises(ImportDataError, match='#77'):
        _run(db)
