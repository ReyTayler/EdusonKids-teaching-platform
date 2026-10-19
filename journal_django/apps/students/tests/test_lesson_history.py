"""
Тесты строк вкладки «Уроки» карточки ученика (спека 2026-09-14).

Проверяют состав строк (пропуски не попадают), порядок (по убыванию даты
сохранения урока), выводимый тип и склейку с деньгами.
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.students import lesson_history

pytestmark = pytest.mark.django_db


@pytest.fixture
def graph():
    """
    Направление → группа → преподаватель → два ученика; чистится в teardown.

    Учеников двое намеренно: с одним пустая тестовая БД пропустила бы выборку
    вообще без фильтра по ученику.
    """
    created = {}
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__lh_dir__', 16, true) RETURNING id")
        created['direction_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__lh_teacher__') RETURNING id")
        created['teacher_id'] = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, active, lesson_number_offset) "
            "VALUES ('__lh_group__', %s, %s, false, 60, true, 0) RETURNING id",
            [created['direction_id'], created['teacher_id']])
        created['group_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__lh_student__') RETURNING id")
        created['student_id'] = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__lh_other__') RETURNING id")
        created['other_student_id'] = cur.fetchone()[0]
    created['lessons'] = []
    created['payments'] = []
    yield created
    with connection.cursor() as cur:
        for lid in created['lessons']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for pid in created['payments']:
            cur.execute('DELETE FROM payments WHERE id = %s', [pid])
        cur.execute('DELETE FROM students WHERE id IN (%s, %s)',
                    [created['student_id'], created['other_student_id']])
        cur.execute('DELETE FROM groups WHERE id = %s', [created['group_id']])
        cur.execute('DELETE FROM teachers WHERE id = %s', [created['teacher_id']])
        cur.execute('DELETE FROM directions WHERE id = %s', [created['direction_id']])


def _add_lesson(graph, *, date='2026-02-10', number=1, duration=60,
                lesson_type='regular', present=True, is_free=False,
                unpaid_skip=False, submitted_at='2026-02-10 18:00:00+03',
                student_id=None):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token, submitted_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, 't', %s) RETURNING id",
            [graph['group_id'], graph['teacher_id'], date, number, duration,
             lesson_type, submitted_at])
        lid = cur.fetchone()[0]
        graph['lessons'].append(lid)
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free, '
            'unpaid_skip) VALUES (%s, %s, %s, %s, %s)',
            [lid, student_id or graph['student_id'], present, is_free, unpaid_skip])
    return lid


def _add_payment(graph, lessons, total, student_id=None):
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO payments (student_id, direction_id, subscriptions_count, "
            "lessons_count, kind, unit_price, total_amount, paid_at, created_by) "
            "VALUES (%s,%s,1,%s,'purchase',0,%s,'2026-01-01','t') RETURNING id",
            [student_id or graph['student_id'], graph['direction_id'], lessons, total])
        graph['payments'].append(cur.fetchone()[0])


def _rows(graph, **sort):
    qs = lesson_history.lesson_rows_queryset(graph['student_id'], **sort)
    return lesson_history.serialize_rows(list(qs), graph['student_id'])


def test_absent_rows_are_excluded(graph):
    """Пропуски (present=false) во вкладку не попадают — ни обычные, ни неоплачиваемые."""
    kept = _add_lesson(graph, number=1)
    _add_lesson(graph, number=2, present=False)
    _add_lesson(graph, number=3, present=False, unpaid_skip=True)

    assert [r['lesson_id'] for r in _rows(graph)] == [kept]


def test_rows_ordered_by_submitted_at_desc(graph):
    """Порядок — по убыванию даты СОХРАНЕНИЯ, а не даты занятия."""
    early_submit = _add_lesson(graph, number=1, date='2026-02-20',
                               submitted_at='2026-02-20 18:00:00+03')
    late_submit = _add_lesson(graph, number=2, date='2026-02-10',
                              submitted_at='2026-03-01 12:00:00+03')

    assert [r['lesson_id'] for r in _rows(graph)] == [late_submit, early_submit]


def test_kind_is_derived_for_all_four_cases(graph):
    regular = _add_lesson(graph, number=1)
    free = _add_lesson(graph, number=2, is_free=True)
    extra = _add_lesson(graph, number=3, lesson_type='extra')
    burned = _add_lesson(graph, number=4, lesson_type='burned')

    kinds = {r['lesson_id']: r['kind'] for r in _rows(graph)}
    assert kinds == {regular: 'regular', free: 'free', extra: 'extra', burned: 'burned'}


def test_lesson_type_wins_over_free_flag(graph):
    """Сгорание и доп.урок бесплатными не бывают — флаг is_free на них не смотрим."""
    burned = _add_lesson(graph, number=1, lesson_type='burned', is_free=True)

    assert {r['lesson_id']: r['kind'] for r in _rows(graph)} == {burned: 'burned'}


def test_row_carries_group_teacher_direction_and_duration(graph):
    lid = _add_lesson(graph, number=1, duration=45)

    row = _rows(graph)[0]
    assert row['lesson_id'] == lid
    assert row['duration_minutes'] == 45
    assert row['group_id'] == graph['group_id']
    assert row['group_name'] == '__lh_group__'
    assert row['teacher_id'] == graph['teacher_id']
    assert row['teacher_name'] == '__lh_teacher__'
    assert row['direction_id'] == graph['direction_id']
    assert row['direction_name'] == '__lh_dir__'
    assert row['lesson_date'] == '2026-02-10'
    # Дата сохранения — отдельное поле строки: без этой проверки её можно
    # переименовать, и колонка «Сохранён» опустеет при зелёных тестах
    # (порядок строк её переименование не заметит).
    assert row['submitted_at'].startswith('2026-02-10')


def test_recognized_amount_and_debt_flag(graph):
    """Оплачен 1 урок, проведено 2: первый признан, второй — в долг."""
    _add_payment(graph, 1, 1000)
    first = _add_lesson(graph, number=1, date='2026-02-10',
                        submitted_at='2026-02-10 18:00:00+03')
    second = _add_lesson(graph, number=2, date='2026-02-11',
                         submitted_at='2026-02-11 18:00:00+03')

    by_id = {r['lesson_id']: r for r in _rows(graph)}
    assert by_id[first]['recognized_amount'] == '1000.00'
    assert by_id[first]['is_debt'] is False
    assert by_id[second]['recognized_amount'] == '0.00'
    assert by_id[second]['is_debt'] is True


def test_free_lesson_recognizes_zero(graph):
    _add_payment(graph, 4, 4000)
    lid = _add_lesson(graph, number=1, is_free=True)

    row = _rows(graph)[0]
    assert row['lesson_id'] == lid
    assert row['recognized_amount'] == '0.00'
    assert row['is_debt'] is False


def test_half_lesson_recognizes_half_price(graph):
    """45 минут весят 0.5 урока — и в деньгах тоже: 4000/4 урока → 500 ₽."""
    _add_payment(graph, 4, 4000)
    lid = _add_lesson(graph, number=1, duration=45)

    row = _rows(graph)[0]
    assert row['lesson_id'] == lid
    assert row['recognized_amount'] == '500.00'
    assert row['is_debt'] is False


def test_only_this_students_rows_and_money(graph):
    """Чужой урок в выдачу не попадает, и чужая оплата в цену не подмешивается."""
    _add_payment(graph, 1, 1000)
    _add_payment(graph, 1, 5000, student_id=graph['other_student_id'])
    mine = _add_lesson(graph, number=1)
    alien = _add_lesson(graph, number=2, student_id=graph['other_student_id'])

    rows = _rows(graph)
    assert [r['lesson_id'] for r in rows] == [mine]
    assert alien not in {r['lesson_id'] for r in rows}
    assert rows[0]['recognized_amount'] == '1000.00'


def test_serialize_rows_rejects_foreign_student_rows(graph):
    """Строки одного ученика + student_id другого = чужие деньги наружу; ловим сразу."""
    _add_lesson(graph, number=1)
    mine = list(lesson_history.lesson_rows_queryset(graph['student_id']))

    with pytest.raises(ValueError):
        lesson_history.serialize_rows(mine, graph['other_student_id'])


def test_money_accounts_for_lessons_outside_the_page(graph):
    """
    Карта денег строится по ВСЕЙ истории, а не по переданному срезу.

    Оплачен 1 урок, проведено 3. Если отдать одну лишь третью запись (вторая
    страница списка), она обязана остаться долгом: партию съели первые два
    урока, которых в срезе нет.
    """
    _add_payment(graph, 1, 1000)
    _add_lesson(graph, number=1, date='2026-02-10', submitted_at='2026-02-10 18:00:00+03')
    _add_lesson(graph, number=2, date='2026-02-11', submitted_at='2026-02-11 18:00:00+03')
    third = _add_lesson(graph, number=3, date='2026-02-12',
                        submitted_at='2026-02-12 18:00:00+03')

    qs = lesson_history.lesson_rows_queryset(graph['student_id'])
    page = [att for att in qs if att.lesson_id == third]
    rows = lesson_history.serialize_rows(page, graph['student_id'])

    assert [r['lesson_id'] for r in rows] == [third]
    assert rows[0]['recognized_amount'] == '0.00'
    assert rows[0]['is_debt'] is True


def test_ties_on_submitted_at_break_by_lesson_id_desc(graph):
    """
    При равном submitted_at порядок задаёт lesson_id по убыванию.

    Без тай-брейка PostgreSQL волен вернуть равные строки в любом порядке, и
    вторая страница повторила бы строку с первой.
    """
    first = _add_lesson(graph, number=1, submitted_at='2026-02-10 18:00:00+03')
    second = _add_lesson(graph, number=2, submitted_at='2026-02-10 18:00:00+03')

    assert [r['lesson_id'] for r in _rows(graph)] == [second, first]
    assert second > first


def test_empty_page_returns_no_rows(graph):
    """Пустой срез — пустой результат, без похода в FIFO."""
    assert lesson_history.serialize_rows([], graph['student_id']) == []


# ---------------------------------------------------------------------------
# Сортировка по дате занятия и номер урока
# ---------------------------------------------------------------------------

def test_sort_by_lesson_date_desc(graph):
    """Сортировка по дате ЗАНЯТИЯ, а не сохранения: даты нарочно перекрещены."""
    early = _add_lesson(graph, number=1, date='2026-02-01',
                        submitted_at='2026-03-01 12:00:00+03')
    late = _add_lesson(graph, number=2, date='2026-02-20',
                       submitted_at='2026-02-20 18:00:00+03')

    rows = _rows(graph, sort_by='lesson_date', sort_dir='desc')
    assert [r['lesson_id'] for r in rows] == [late, early]


def test_sort_by_lesson_date_asc(graph):
    early = _add_lesson(graph, number=1, date='2026-02-01',
                        submitted_at='2026-03-01 12:00:00+03')
    late = _add_lesson(graph, number=2, date='2026-02-20',
                       submitted_at='2026-02-20 18:00:00+03')

    rows = _rows(graph, sort_by='lesson_date', sort_dir='asc')
    assert [r['lesson_id'] for r in rows] == [early, late]


def test_sort_asc_breaks_ties_by_lesson_id_asc(graph):
    """Тай-брейк идёт в ту же сторону, что и основной ключ, — иначе страницы поедут."""
    first = _add_lesson(graph, number=1, date='2026-02-10')
    second = _add_lesson(graph, number=2, date='2026-02-10')

    rows = _rows(graph, sort_by='lesson_date', sort_dir='asc')
    assert [r['lesson_id'] for r in rows] == [first, second]


def test_unknown_sort_key_falls_back_to_default(graph):
    """Второй рубеж: неизвестный ключ не уезжает в order_by сырой строкой."""
    early_submit = _add_lesson(graph, number=1, date='2026-02-20',
                               submitted_at='2026-02-20 18:00:00+03')
    late_submit = _add_lesson(graph, number=2, date='2026-02-10',
                              submitted_at='2026-03-01 12:00:00+03')

    rows = _rows(graph, sort_by='lesson__group__name', sort_dir='desc')
    assert [r['lesson_id'] for r in rows] == [late_submit, early_submit]


def test_row_carries_lesson_number(graph):
    """Номер урока в плане курса — строкой без потери масштаба numeric(5,1)."""
    _add_lesson(graph, number=12)

    assert _rows(graph)[0]['lesson_number'] == '12.0'


# ---------------------------------------------------------------------------
# Необязательный период (нужен модалке дашборда «Посещения учеников»)
# ---------------------------------------------------------------------------

def test_period_filters_by_lesson_date(graph):
    _add_lesson(graph, number=1, date='2026-01-15')
    inside = _add_lesson(graph, number=2, date='2026-02-10')
    _add_lesson(graph, number=3, date='2026-03-01')

    rows = _rows(graph, date_from='2026-02-01', date_to='2026-02-28')
    assert [r['lesson_id'] for r in rows] == [inside]


def test_period_bounds_are_inclusive(graph):
    first = _add_lesson(graph, number=1, date='2026-02-01')
    last = _add_lesson(graph, number=2, date='2026-02-28')

    rows = _rows(graph, date_from='2026-02-01', date_to='2026-02-28')
    assert {r['lesson_id'] for r in rows} == {first, last}


def test_without_period_whole_history_is_returned(graph):
    """Вкладка карточки период не передаёт — её поведение меняться не должно."""
    old = _add_lesson(graph, number=1, date='2020-01-01')
    new = _add_lesson(graph, number=2, date='2026-02-10')

    assert {r['lesson_id'] for r in _rows(graph)} == {old, new}
