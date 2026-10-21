"""
Тесты агрегатов карточки преподавателя (apps.teachers.stats).

eduson_test общая для всех worktree — каждая фикстура чистит за собой DELETE.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from apps.teachers import stats


# ---------------------------------------------------------------------------
# month_bounds
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('month,expected_last', [
    ('2024-02', '2024-02-29'),  # високосный
    ('2023-02', '2023-02-28'),
    ('2026-12', '2026-12-31'),  # переход через год
    ('2026-01', '2026-01-31'),
])
def test_month_bounds_last_day(month, expected_last):
    """Регрессия: month_bounds считает последний день от «первого числа
    следующего месяца минус день», а не хардкодит длины месяцев — эта функция
    не трогает БД, поэтому без django_db."""
    first, last = stats.month_bounds(month)
    assert first == f'{month}-01'
    assert last == expected_last


@pytest.mark.django_db
def test_month_totals_count_lessons_and_minutes(stats_teacher, make_group, make_lesson):
    group = make_group('__stats_g90__', duration=90)
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-13')

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert result['total']['sessions'] == 2
    assert result['total']['minutes'] == 180


@pytest.mark.django_db
def test_extra_and_burned_lessons_excluded(stats_teacher, make_group, make_lesson):
    """Три курсовых типа (regular/substitution/reschedule) считаются, доп.урок и
    сгорание — не нагрузка курса, в счёт не идут."""
    group = make_group('__stats_g_sys__')
    make_lesson(group, '2026-07-05', lesson_type='regular')
    make_lesson(group, '2026-07-06', lesson_type='reschedule')
    make_lesson(group, '2026-07-07', lesson_type='extra')
    make_lesson(group, '2026-07-08', lesson_type='burned')

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert result['total']['sessions'] == 2


@pytest.mark.django_db
def test_substitution_counted_and_flagged(stats_teacher, make_group, make_lesson):
    """Признак замены — original_teacher_id IS NOT NULL, а НЕ lesson_type.

    Два regular-урока с указанным original_teacher_id (реальные замены) и один
    substitution-урок БЕЗ original_teacher_id (не считается заменой, только
    исторический тип). Числа намеренно не совпадают (2 против 1), чтобы
    реализация, ошибочно считающая по lesson_type=='substitution', давала
    другой итог и тест её ловил."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__stats_orig_teacher__') RETURNING id")
        other_id = cur.fetchone()[0]

    group = make_group('__stats_g_sub__')
    make_lesson(group, '2026-07-06', lesson_type='regular', original_teacher_id=other_id)
    make_lesson(group, '2026-07-08', lesson_type='regular', original_teacher_id=other_id)
    make_lesson(group, '2026-07-07', lesson_type='substitution')

    try:
        result = stats.month_breakdown(stats_teacher, '2026-07')

        assert result['total']['sessions'] == 3
        assert result['total']['substitutions'] == 2
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE original_teacher_id = %s', [other_id])
            cur.execute('DELETE FROM teachers WHERE id = %s', [other_id])


@pytest.mark.django_db
def test_minutes_use_lesson_duration_not_group(stats_teacher, make_group, make_lesson):
    """Длительность берётся с УРОКА: у 90-мин группы бывает 45-мин занятие."""
    group = make_group('__stats_g_mixed__', duration=90)
    make_lesson(group, '2026-07-06', duration=90)
    make_lesson(group, '2026-07-07', duration=45)

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert result['total']['minutes'] == 135
    by_duration = {r['minutes']: r['sessions'] for r in result['by_duration']}
    assert by_duration == {90: 1, 45: 1}


@pytest.mark.django_db
def test_by_duration_sorted_by_minutes_desc(stats_teacher, make_group, make_lesson):
    """Порядок, а не только состав: длинные занятия сверху."""
    group = make_group('__stats_g_dur_sort__', duration=90)
    make_lesson(group, '2026-07-06', duration=45)
    make_lesson(group, '2026-07-07', duration=60)
    make_lesson(group, '2026-07-08', duration=90)

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert [r['minutes'] for r in result['by_duration']] == [90, 60, 45]


@pytest.mark.django_db
def test_other_months_not_counted(stats_teacher, make_group, make_lesson):
    group = make_group('__stats_g_bounds__')
    make_lesson(group, '2026-06-30')
    make_lesson(group, '2026-07-01')
    make_lesson(group, '2026-07-31')
    make_lesson(group, '2026-08-01')

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert result['total']['sessions'] == 2


@pytest.mark.django_db
def test_breakdown_by_direction(stats_teacher, stats_direction, make_group, make_lesson):
    group = make_group('__stats_g_dir__')
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-13')

    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert len(result['by_direction']) == 1
    row = result['by_direction'][0]
    assert row['direction_id'] == stats_direction
    assert row['name'] == '__stats_dir__'
    assert row['color'] == '#f0b429'
    assert row['sessions'] == 2
    assert row['minutes'] == 180


@pytest.mark.django_db
def test_by_direction_sorted_by_sessions_desc(stats_teacher, stats_direction,
                                              make_group, make_lesson):
    """Сверху — направление, где преподаватель работает больше всего.

    `stats_direction` (фикстура) создаётся ПЕРВЫМ, `second_dir` — ВТОРЫМ, и
    именно `stats_direction` получает БОЛЬШЕ занятий, а `second_dir` —
    меньше. Это специально инвертировано относительно порядка создания:
    реализация, ошибочно сортирующая по `-direction_id` (или просто
    возвращающая направления в обратном порядке вставки без учёта числа
    занятий), поставила бы `second_dir` первым — и тест бы это поймал. Прежняя
    версия теста давала БОЛЬШЕ занятий направлению с бОльшим id, из-за чего
    сортировка по `-sessions` и сортировка по `-direction_id` давали один и
    тот же (ошибочно «верный») результат и не различались."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__stats_dir_2__', 24, true) RETURNING id"
        )
        second_dir = cur.fetchone()[0]

    many = few = None
    try:
        many = make_group('__stats_g_many__')
        few = make_group('__stats_g_few__', direction_id=second_dir)
        make_lesson(many, '2026-07-06')
        make_lesson(many, '2026-07-07')
        make_lesson(few, '2026-07-08')

        result = stats.month_breakdown(stats_teacher, '2026-07')

        assert [r['name'] for r in result['by_direction']] == \
            ['__stats_dir__', '__stats_dir_2__']
        assert [r['sessions'] for r in result['by_direction']] == [2, 1]
    finally:
        # Направление удаляем ПОСЛЕ группы/уроков, ссылающихся на него (FK
        # groups.direction_id — NO ACTION), иначе DELETE FROM directions падает.
        # make_group чистит группы в своём teardown, но тот выполнится позже
        # (после этого finally) — поэтому чистим group/few вручную здесь же.
        with connection.cursor() as cur:
            if few is not None:
                cur.execute('DELETE FROM lessons WHERE group_id = %s', [few])
                cur.execute('DELETE FROM groups WHERE id = %s', [few])
            cur.execute('DELETE FROM directions WHERE id = %s', [second_dir])


@pytest.mark.django_db
def test_other_teacher_lessons_not_counted(stats_teacher, make_group, make_lesson):
    """Урок, который в этой же группе провёл кто-то другой, в счёт не идёт."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__stats_other__') RETURNING id")
        other_id = cur.fetchone()[0]
    group = make_group('__stats_g_other__')
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-07', teacher_id=other_id)
    try:
        result = stats.month_breakdown(stats_teacher, '2026-07')
        assert result['total']['sessions'] == 1
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE teacher_id = %s', [other_id])
            cur.execute('DELETE FROM teachers WHERE id = %s', [other_id])


@pytest.mark.django_db
def test_empty_month_returns_zeros(stats_teacher):
    result = stats.month_breakdown(stats_teacher, '2026-07')

    assert result['total'] == {'sessions': 0, 'minutes': 0, 'substitutions': 0}
    assert result['by_direction'] == []
    assert result['by_duration'] == []


# ---------------------------------------------------------------------------
# year_series / last_lesson_date
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_year_series_is_january_to_december(stats_teacher):
    """Календарный год, а не скользящее окно: ось неподвижна, иначе при
    переключении месяца уезжают и данные, и подписи, и сравнить нечего."""
    series = stats.year_series(stats_teacher, 2026)

    assert len(series) == 12
    assert series[0]['month'] == '2026-01'
    assert series[-1]['month'] == '2026-12'
    assert all(point['sessions'] == 0 for point in series)


@pytest.mark.django_db
def test_year_series_counts_per_month(stats_teacher, make_group, make_lesson):
    group = make_group('__stats_g_series__')
    make_lesson(group, '2026-06-10')
    make_lesson(group, '2026-07-10')
    make_lesson(group, '2026-07-17')

    series = {p['month']: p['sessions'] for p in stats.year_series(stats_teacher, 2026)}

    assert series['2026-06'] == 1
    assert series['2026-07'] == 2
    assert series['2026-05'] == 0


@pytest.mark.django_db
def test_year_series_excludes_system_lessons(stats_teacher, make_group, make_lesson):
    group = make_group('__stats_g_series_sys__')
    make_lesson(group, '2026-07-10', lesson_type='regular')
    make_lesson(group, '2026-07-11', lesson_type='extra')

    series = {p['month']: p['sessions'] for p in stats.year_series(stats_teacher, 2026)}

    assert series['2026-07'] == 1


@pytest.mark.django_db
def test_year_series_ignores_neighbouring_years(stats_teacher, make_group, make_lesson):
    """Ровно этот год: декабрь прошлого и январь следующего в ряд не попадают —
    иначе «год» перестаёт быть годом."""
    group = make_group('__stats_g_series_ny__')
    make_lesson(group, '2025-12-31')
    make_lesson(group, '2026-01-01')
    make_lesson(group, '2027-01-01')

    series = stats.year_series(stats_teacher, 2026)

    assert sum(p['sessions'] for p in series) == 1
    assert series[0]['month'] == '2026-01'
    assert series[0]['sessions'] == 1


@pytest.mark.django_db
def test_last_lesson_date_ignores_selected_month(stats_teacher, make_group, make_lesson):
    """Отвечает на вопрос «преподаватель ещё работает», поэтому месяцем не ограничен."""
    group = make_group('__stats_g_last__')
    make_lesson(group, '2026-07-10')
    make_lesson(group, '2026-08-02')

    assert stats.last_lesson_date(stats_teacher) == '2026-08-02'


@pytest.mark.django_db
def test_last_lesson_date_none_when_never_taught(stats_teacher):
    assert stats.last_lesson_date(stats_teacher) is None


@pytest.mark.django_db
def test_last_lesson_date_ignores_system_lessons(stats_teacher, make_group, make_lesson):
    """Сгорание — не проведённое занятие, «работает ли он» по нему судить нельзя."""
    group = make_group('__stats_g_last_sys__')
    make_lesson(group, '2026-07-10', lesson_type='regular')
    make_lesson(group, '2026-09-01', lesson_type='burned')

    assert stats.last_lesson_date(stats_teacher) == '2026-07-10'


# ---------------------------------------------------------------------------
# group_progress
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_group_progress_counts_course_lessons(stats_teacher, make_group, make_lesson):
    group = make_group('__stats_g_prog__', duration=90)
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-13')
    make_lesson(group, '2026-07-20', lesson_type='extra')  # сверх курса

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert float(rows[group]['lessons_done']) == 2.0
    assert rows[group]['lessons_total'] == 36  # из направления


@pytest.mark.django_db
def test_group_progress_applies_half_lesson_weight(stats_teacher, make_group, make_lesson):
    """Прогресс курса меряется в УРОКАХ: 45-мин занятие = 0.5 урока."""
    group = make_group('__stats_g_half__', duration=45)
    make_lesson(group, '2026-07-06', duration=45)
    make_lesson(group, '2026-07-08', duration=45)
    make_lesson(group, '2026-07-10', duration=45)

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert float(rows[group]['lessons_done']) == 1.5


@pytest.mark.django_db
def test_group_progress_prefers_manual_lessons_total(stats_teacher, make_group):
    """groups.lessons_total перекрывает directions.total_lessons."""
    manual = make_group('__stats_g_manual__', lessons_total=12)
    inherited = make_group('__stats_g_inherit__')

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert rows[manual]['lessons_total'] == 12
    assert rows[inherited]['lessons_total'] == 36


@pytest.mark.django_db
def test_group_progress_zero_for_group_without_lessons(stats_teacher, make_group):
    group = make_group('__stats_g_empty__')

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert float(rows[group]['lessons_done']) == 0.0


@pytest.mark.django_db
def test_group_progress_includes_archived_groups(stats_teacher, make_group):
    archived = make_group('__stats_g_arch__', active=False)

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert archived in rows


@pytest.mark.django_db
def test_group_progress_counts_lessons_of_other_teachers_in_group(
        stats_teacher, make_group, make_lesson):
    """Прогресс — свойство ГРУППЫ: занятие, которое провёл коллега на замене,
    из прогресса курса не выпадает."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__stats_sub_teacher__') RETURNING id")
        other_id = cur.fetchone()[0]
    group = make_group('__stats_g_shared__')
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-13', teacher_id=other_id)
    try:
        rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}
        assert float(rows[group]['lessons_done']) == 2.0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE teacher_id = %s', [other_id])
            cur.execute('DELETE FROM teachers WHERE id = %s', [other_id])


@pytest.mark.django_db
def test_group_progress_not_inflated_by_members(stats_teacher, stats_direction,
                                                make_group, make_lesson):
    """
    Регрессия на классическую ловушку Django ORM: два Count по разным связям
    в одном annotate дают декартово произведение. Группа с 3 учениками и
    2 уроками обязана отдать 2 урока, а не 6.
    """
    from django.db import connection
    group = make_group('__stats_g_cartesian__')
    make_lesson(group, '2026-07-06')
    make_lesson(group, '2026-07-13')

    student_ids = []
    with connection.cursor() as cur:
        for i in range(3):
            cur.execute(
                "INSERT INTO students (full_name) VALUES (%s) RETURNING id",
                [f'__stats_student_{i}__'],
            )
            student_id = cur.fetchone()[0]
            student_ids.append(student_id)
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, 0, true)',
                [group, student_id],
            )
    try:
        rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}
        assert float(rows[group]['lessons_done']) == 2.0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [group])
            for student_id in student_ids:
                cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.mark.django_db
def test_group_progress_scale_is_stable(stats_teacher, make_group, make_lesson):
    """
    Масштаб numeric фиксирован Cast'ом: без него SUM(CASE 0.5/1) отдаёт scale
    операндов — «2» для целых занятий и «1.5» для половинок, и формат на проводе
    прыгает. Проверяем СЫРОЕ значение: float() уравнял бы оба варианта и ничего
    бы не поймал (именно поэтому старые тесты этого не видели).
    """
    whole = make_group('__stats_g_scale_whole__', duration=90)
    make_lesson(whole, '2026-07-06', duration=90)
    make_lesson(whole, '2026-07-13', duration=90)

    half = make_group('__stats_g_scale_half__', duration=45)
    make_lesson(half, '2026-07-06', duration=45)

    rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}

    assert str(rows[whole]['lessons_done']) == '2.0'
    assert str(rows[half]['lessons_done']) == '0.5'


@pytest.mark.django_db
def test_group_progress_lessons_total_may_be_null_or_zero(stats_teacher, make_group):
    """
    directions.total_lessons nullable и допускает 0 (CHECK >= 0) — значит оба
    значения долетают до фронта, и оба означают «длины курса нет».
    """
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__stats_dir_null__', NULL, true) RETURNING id"
        )
        dir_null = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, total_lessons, active) "
            "VALUES ('__stats_dir_zero__', 0, true) RETURNING id"
        )
        dir_zero = cur.fetchone()[0]
    g_null = make_group('__stats_g_total_null__', direction_id=dir_null)
    g_zero = make_group('__stats_g_total_zero__', direction_id=dir_zero)
    try:
        rows = {r['group_id']: r for r in stats.group_progress(stats_teacher)}
        assert rows[g_null]['lessons_total'] is None
        assert rows[g_zero]['lessons_total'] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lessons WHERE group_id IN (%s, %s)', [g_null, g_zero])
            cur.execute('DELETE FROM groups WHERE id IN (%s, %s)', [g_null, g_zero])
            cur.execute('DELETE FROM directions WHERE id IN (%s, %s)', [dir_null, dir_zero])


# ---------------------------------------------------------------------------
# Производительность
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_stats_uses_fixed_number_of_queries(django_assert_num_queries, stats_teacher,
                                            make_group, make_lesson):
    """
    Число запросов постоянно и НЕ зависит от числа групп и уроков.

    Само значение (11 без зарплаты, +2 с ней) не догма — агрегатов может
    стать больше. Догма — постоянство: тест ниже гоняет ту же выборку на
    3 и на 6 группах и требует одинакового числа запросов. Именно это ловит
    N+1 (дозапрос направления/слотов на каждую группу), а не конкретная цифра.

    Проверяется на СЕРВИСЕ, а не через API: в замер вьюхи попали бы ещё
    запросы аутентификации, и тест ломался бы от любой правки в auth.
    """
    from apps.teachers import services

    for i in range(3):
        group = make_group(f'__stats_g_nplus1_{i}__')
        make_lesson(group, '2026-07-06')
        make_lesson(group, '2026-07-13')

    with django_assert_num_queries(11):
        services.get_teacher_stats(stats_teacher, '2026-07')

    # Зарплата — ровно два дополнительных запроса: агрегат уроков и агрегат отмен.
    with django_assert_num_queries(13):
        services.get_teacher_stats(stats_teacher, '2026-07', with_payroll=True)


@pytest.mark.django_db
def test_stats_query_count_does_not_grow_with_groups(django_assert_num_queries,
                                                     stats_teacher, make_group,
                                                     make_lesson):
    """Удвоение числа групп не добавляет ни одного запроса — это и есть
    отсутствие N+1, в отличие от конкретного значения счётчика."""
    from apps.teachers import services

    for i in range(6):
        group = make_group(f'__stats_g_scale_{i}__')
        make_lesson(group, '2026-07-06')
        make_lesson(group, '2026-07-13')

    with django_assert_num_queries(11):
        services.get_teacher_stats(stats_teacher, '2026-07')


# ---------------------------------------------------------------------------
# attendance / weekday_load / absences / payroll
# ---------------------------------------------------------------------------

def _mark(lesson_id: int, student_id: int, *, present: bool,
          is_free: bool = False, unpaid_skip: bool = False) -> None:
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present, is_free, unpaid_skip) '
            'VALUES (%s, %s, %s, %s, %s)',
            [lesson_id, student_id, present, is_free, unpaid_skip],
        )


@pytest.fixture
def make_student():
    """Ученик под тесты посещаемости. Чистится DELETE — eduson_test общая."""
    from django.db import connection
    created = []

    def _make(name: str):
        with connection.cursor() as cur:
            cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id', [name])
            student_id = cur.fetchone()[0]
        created.append(student_id)
        return student_id

    yield _make
    with connection.cursor() as cur:
        for student_id in created:
            # Порядок важен: students ссылаются отовсюду, и FK — NO ACTION.
            # Сделки продления чистит своя фикстура (make_deal), она отработает
            # раньше этой, потому что запрашивается тестом позже.
            cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [student_id])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [student_id])
            cur.execute('DELETE FROM students WHERE id = %s', [student_id])


@pytest.mark.django_db
def test_attendance_counts_present_share(stats_teacher, make_group, make_lesson, make_student):
    group = make_group('__stats_g_att__')
    lesson = make_lesson(group, '2026-07-06')
    _mark(lesson, make_student('__att_a__'), present=True)
    _mark(lesson, make_student('__att_b__'), present=True)
    _mark(lesson, make_student('__att_c__'), present=False)

    assert stats.attendance(stats_teacher, '2026-07') == {
        'present': 2, 'counted': 3, 'pct': 67,
    }


@pytest.mark.django_db
def test_attendance_excludes_unpaid_skip_from_denominator(
        stats_teacher, make_group, make_lesson, make_student):
    """unpaid_skip — ученик этот слот не посещает по устройству курса. Он не
    «отсутствовал у преподавателя», и в знаменатель попадать не должен."""
    group = make_group('__stats_g_att_skip__')
    lesson = make_lesson(group, '2026-07-06')
    _mark(lesson, make_student('__att_present__'), present=True)
    _mark(lesson, make_student('__att_skip__'), present=False, unpaid_skip=True)

    assert stats.attendance(stats_teacher, '2026-07') == {
        'present': 1, 'counted': 1, 'pct': 100,
    }


@pytest.mark.django_db
def test_attendance_keeps_free_lessons_in_denominator(
        stats_teacher, make_group, make_lesson, make_student):
    """is_free — про деньги, а не про посещаемость: ученик на занятии был."""
    group = make_group('__stats_g_att_free__')
    lesson = make_lesson(group, '2026-07-06')
    _mark(lesson, make_student('__att_free__'), present=True, is_free=True)

    assert stats.attendance(stats_teacher, '2026-07')['counted'] == 1


@pytest.mark.django_db
def test_attendance_pct_is_none_without_data(stats_teacher):
    """None, а не 0: «занятий не было» и «никто не пришёл» — разные вещи."""
    assert stats.attendance(stats_teacher, '2026-07')['pct'] is None


@pytest.mark.django_db
def test_weekday_load_uses_sunday_zero_convention(stats_teacher, make_group, make_lesson):
    """Вс=0, как в group_schedule_slots.day_of_week и во фронтовом DOW."""
    group = make_group('__stats_g_dow__')
    make_lesson(group, '2026-07-05')   # воскресенье
    make_lesson(group, '2026-07-06')   # понедельник
    make_lesson(group, '2026-07-13')   # понедельник

    load = {row['day']: row['sessions'] for row in stats.weekday_load(stats_teacher, '2026-07')}

    assert load[0] == 1   # Вс
    assert load[1] == 2   # Пн
    assert load[6] == 0   # Сб


@pytest.mark.django_db
def test_weekday_load_always_has_seven_days(stats_teacher):
    load = stats.weekday_load(stats_teacher, '2026-07')

    assert [row['day'] for row in load] == list(range(7))
    assert all(row['sessions'] == 0 for row in load)


@pytest.mark.django_db
def test_payroll_for_month_sums_payment_and_penalty(
        stats_teacher, make_group, make_lesson):
    from django.db import connection
    group = make_group('__stats_g_pay__')
    lesson = make_lesson(group, '2026-07-06')
    other = make_lesson(group, '2026-08-06')
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, '
            'payment, penalty) VALUES (%s, %s, 3, 3, 600, 40), (%s, %s, 3, 3, 900, 0)',
            [lesson, stats_teacher, other, stats_teacher],
        )
    try:
        result = stats.payroll_for_month(stats_teacher, '2026-07')
        # Августовский урок в июльскую сумму не попадает.
        assert result == {'payment': Decimal('600'), 'penalty': Decimal('40')}
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE teacher_id = %s', [stats_teacher])


@pytest.mark.django_db
def test_payroll_for_month_includes_cancellation_pay(
        stats_teacher, make_group, make_lesson):
    """
    «Начислено» = уроки + отмены (как sum_payment в /api/admin/payroll/summary).
    Отмена относится к месяцу по своей lesson_date; штраф у отмены не бывает.
    """
    from datetime import date, time

    from django.db import connection

    from apps.payroll.models import CancellationPay

    group = make_group('__stats_g_cancel__')
    lesson = make_lesson(group, '2026-07-06')
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO payroll (lesson_id, teacher_id, total_students, present_count, '
            'payment, penalty) VALUES (%s, %s, 3, 3, 600.10, 40)',
            [lesson, stats_teacher],
        )
    CancellationPay.objects.bulk_create([
        CancellationPay(teacher_id=stats_teacher, group_id=group,
                        lesson_date=date(2026, 7, 13), scheduled_time=time(10, 0),
                        payment=Decimal('300.25')),
        # Августовская отмена в июль не попадает.
        CancellationPay(teacher_id=stats_teacher, group_id=group,
                        lesson_date=date(2026, 8, 3), scheduled_time=time(10, 0),
                        payment=Decimal('999')),
    ])
    try:
        result = stats.payroll_for_month(stats_teacher, '2026-07')
        assert result == {'payment': Decimal('900.35'), 'penalty': Decimal('40')}

        # Месяц только с отменами — тоже не ноль.
        only_cancel = stats.payroll_for_month(stats_teacher, '2026-08')
        assert only_cancel == {'payment': Decimal('999'), 'penalty': Decimal('0')}
    finally:
        CancellationPay.objects.filter(teacher_id=stats_teacher).delete()
        with connection.cursor() as cur:
            cur.execute('DELETE FROM payroll WHERE teacher_id = %s', [stats_teacher])
