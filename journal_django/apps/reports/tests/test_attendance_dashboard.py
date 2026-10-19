"""
Тесты агрегата дашборда «Посещения учеников» (спека 2026-09-15).

Счётчик везде один: списанные уроки с весом (45 мин = 0.5), это обычные +
доп.уроки + сгорания; бесплатные идут отдельной колонкой и в «Итого» не входят.
Период определяется датой ЗАНЯТИЯ.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.reports import attendance_dashboard as ad

pytestmark = pytest.mark.django_db

PERIOD = ('2026-02-01', '2026-02-28')


@pytest.fixture
def graph():
    created = {'lessons': [], 'students': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__ad_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__ad_teacher__') RETURNING id")
        created['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__ad_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], created['teacher_id']])
        created['group_id'] = cur.fetchone()[0]
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for sid in created['students']:
            cur.execute('DELETE FROM students WHERE id = %s', [sid])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [created['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _student(graph, name):
    with connection.cursor() as cur:
        cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
        sid = cur.fetchone()[0]
    graph['students'].append(sid)
    return sid


def _lesson(graph, sid, *, date='2026-02-10', number=1, duration=60,
            lesson_type='regular', present=True, is_free=False):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token) "
            "VALUES (%s, %s, %s, %s, %s, %s, 't') RETURNING id",
            [graph['group_id'], graph['teacher_id'], date, number, duration, lesson_type])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free) '
            'VALUES (%s, %s, %s, %s)', [lid, sid, present, is_free])
    return lid


def _rows(date_from=PERIOD[0], date_to=PERIOD[1], **kw):
    """Строки API с числами, приведёнными обратно в Decimal — сравнивать удобнее."""
    qs = ad.student_rows_queryset(date_from, date_to, **kw)
    out = []
    for r in ad.serialize_rows(list(qs)):
        row = dict(r)
        for key in ('regular', 'extra', 'burned', 'free', 'billed'):
            row[key] = Decimal(row[key])
        out.append(row)
    return out


def _by_student(rows):
    return {r['student_id']: r for r in rows}


def test_columns_split_by_kind_with_weight(graph):
    """Четыре типа раскладываются по своим колонкам, 45 минут весит 0.5."""
    sid = _student(graph, '__ad_s1__')
    _lesson(graph, sid, number=1)                                  # обычный, 1
    _lesson(graph, sid, number=2, duration=45)                     # обычный, 0.5
    _lesson(graph, sid, number=3, lesson_type='extra')             # доп.урок, 1
    _lesson(graph, sid, number=4, lesson_type='burned')            # сгорание, 1
    _lesson(graph, sid, number=5, is_free=True)                    # бесплатный, 1

    row = _by_student(_rows())[sid]
    assert row['regular'] == Decimal('1.5')
    assert row['extra'] == Decimal('1')
    assert row['burned'] == Decimal('1')
    assert row['free'] == Decimal('1')
    # «Итого» — только списанные: бесплатное занятие с абонемента не снимается.
    assert row['billed'] == Decimal('3.5')


def test_absent_rows_are_ignored(graph):
    """Пропуск — не занятие: ни в одну колонку не идёт."""
    sid = _student(graph, '__ad_s2__')
    _lesson(graph, sid, number=1)
    _lesson(graph, sid, number=2, present=False)

    row = _by_student(_rows())[sid]
    assert row['billed'] == Decimal('1')


def test_period_is_by_lesson_date(graph):
    """Урок вне периода не считается, хотя ученик в списке остаётся."""
    sid = _student(graph, '__ad_s3__')
    _lesson(graph, sid, number=1, date='2026-01-15')
    _lesson(graph, sid, number=2, date='2026-02-10')

    row = _by_student(_rows())[sid]
    assert row['billed'] == Decimal('1')


def test_student_without_lessons_in_period_stays_with_zeros(graph):
    """Видно, кто перестал ходить, — иначе он просто исчезнет из отчёта."""
    sid = _student(graph, '__ad_s4__')
    _lesson(graph, sid, number=1, date='2025-12-01')

    row = _by_student(_rows())[sid]
    assert (row['regular'], row['extra'], row['burned'], row['free'], row['billed']) == (
        Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0'), Decimal('0'))


def test_extra_and_burned_belong_to_period_by_their_own_date(graph):
    """Доп.урок и сгорание относятся к месяцу СВОЕГО проведения, не пропуска."""
    sid = _student(graph, '__ad_s5__')
    _lesson(graph, sid, number=1, date='2026-01-20', lesson_type='extra')
    _lesson(graph, sid, number=2, date='2026-02-05', lesson_type='burned')

    row = _by_student(_rows())[sid]
    assert row['extra'] == Decimal('0')
    assert row['burned'] == Decimal('1')


def test_name_filter_is_substring_case_insensitive(graph):
    sid = _student(graph, '__ad_Иванов__')
    _student(graph, '__ad_Петров__')
    _lesson(graph, sid, number=1)

    rows = _rows(name_query='иванов')
    assert [r['student_id'] for r in rows] == [sid]


def test_sort_by_billed_desc_then_name(graph):
    many = _student(graph, '__ad_zz_many__')
    few = _student(graph, '__ad_aa_few__')
    _lesson(graph, many, number=1)
    _lesson(graph, many, number=2)
    _lesson(graph, few, number=3)

    rows = [r for r in _rows() if r['student_id'] in (many, few)]
    assert [r['student_id'] for r in rows] == [many, few]


def test_sort_by_full_name_asc(graph):
    b = _student(graph, '__ad_bbb__')
    a = _student(graph, '__ad_aaa__')
    _lesson(graph, b, number=1)
    _lesson(graph, a, number=2)

    rows = [r for r in _rows(sort_by='full_name', sort_dir='asc')
            if r['student_id'] in (a, b)]
    assert [r['student_id'] for r in rows] == [a, b]


def test_summary_counts_billed_lessons_only(graph):
    sid = _student(graph, '__ad_s6__')
    _lesson(graph, sid, number=1)                        # 1, в периоде
    _lesson(graph, sid, number=2, duration=45)           # 0.5, в периоде
    _lesson(graph, sid, number=3, is_free=True)          # бесплатный — мимо
    _lesson(graph, sid, number=4, date='2025-11-11')     # 1, вне периода

    summary = ad.summary(*PERIOD)
    rows = _rows()
    # Цифра за период сходится с суммой колонки «Итого» по всем строкам —
    # иначе плитка и таблица на одном экране показывают разное.
    assert Decimal(summary['period_lessons']) == sum(Decimal(r['billed']) for r in rows)
    assert Decimal(summary['period_lessons']) == Decimal('1.5')
    # За всё время — ровно период плюс ноябрьский урок. БД этого приложения
    # своя и свежая (conftest не глушит django_db_setup), поэтому сравниваем
    # точно: неравенство пережило бы почти любую ошибку в счёте.
    assert Decimal(summary['all_time_lessons']) == Decimal('2.5')


def test_half_length_system_lessons_weigh_half(graph):
    """
    Сгорание и доп.урок наследуют длительность пропущенного занятия, поэтому
    45-минутные среди них РЕАЛЬНО бывают. Вес обязан применяться и в этих
    колонках, а не только в «обычных».
    """
    sid = _student(graph, '__ad_s7__')
    _lesson(graph, sid, number=1, duration=45, lesson_type='extra')
    _lesson(graph, sid, number=2, duration=45, lesson_type='burned')

    row = _by_student(_rows())[sid]
    assert row['extra'] == Decimal('0.5')
    assert row['burned'] == Decimal('0.5')
    assert row['billed'] == Decimal('1')


def test_billed_always_equals_sum_of_three_columns(graph):
    """
    «Итого списано» — сумма трёх колонок разбивки, а не «всё кроме бесплатного».
    Иначе новый тип урока попал бы в итог мимо колонок, и таблица перестала бы
    сходиться сама с собой молча.
    """
    sid = _student(graph, '__ad_s8__')
    _lesson(graph, sid, number=1)
    _lesson(graph, sid, number=2, duration=45, lesson_type='extra')
    _lesson(graph, sid, number=3, lesson_type='burned')
    _lesson(graph, sid, number=4, is_free=True)

    row = _by_student(_rows())[sid]
    assert row['billed'] == row['regular'] + row['extra'] + row['burned']
