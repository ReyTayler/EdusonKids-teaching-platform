"""
План группы с ручной длиной курса (groups.lessons_total).

Направление курса — 8 уроков; группа с lessons_total=2 должна планировать
2 занятия, а не 8. Половинный формат (45 мин) — 2 урока = 4 занятия.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.scheduling.models import PlannedLesson


def _make_group(cur, direction_id, teacher_id, *, name, duration, lessons_total):
    cur.execute(
        "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
        "lesson_duration_minutes, lessons_per_week, group_start_date, active, "
        "lesson_number_offset, lessons_total) "
        "VALUES (%s, %s, %s, true, %s, 1, DATE '2026-08-03', true, 0, %s) RETURNING id",
        [name, direction_id, teacher_id, duration, lessons_total],
    )
    group_id = cur.fetchone()[0]
    # Пн 10:00 (конвенция Вс=0 → понедельник = 1)
    cur.execute(
        "INSERT INTO group_schedule_slots (group_id, day_of_week, start_time, effective_from) "
        "VALUES (%s, 1, TIME '10:00', DATE '2000-01-01')",
        [group_id],
    )
    return group_id


@pytest.fixture
def plan_setup(db):
    """Направление (8 уроков) + преподаватель. Чистит за собой всё созданное."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, color, active) "
            "VALUES ('__plt_dir__', 8, '#4F59F9', true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__plt_tch__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
    yield direction_id, teacher_id
    with connection.cursor() as cur:
        cur.execute(
            "DELETE FROM planned_lessons WHERE group_id IN "
            "(SELECT id FROM groups WHERE direction_id = %s)", [direction_id])
        cur.execute(
            "DELETE FROM group_schedule_slots WHERE group_id IN "
            "(SELECT id FROM groups WHERE direction_id = %s)", [direction_id])
        cur.execute("DELETE FROM groups WHERE direction_id = %s", [direction_id])
        cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])
        cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])


@pytest.mark.django_db
def test_generate_uses_group_lessons_total(plan_setup):
    """lessons_total=2 при курсе направления 8 → в плане 2 занятия."""
    from apps.scheduling.repository import generate_for_group
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_short__', duration=90, lessons_total=2)

    generate_for_group(group_id)

    rows = list(PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False)
                .order_by('seq').values_list('seq', 'lesson_number', 'scheduled_date'))
    assert [r[0] for r in rows] == [1, 2]
    assert [float(r[1]) for r in rows] == [1.0, 2.0]
    assert rows[0][2] == datetime.date(2026, 8, 3)


@pytest.mark.django_db
def test_generate_half_lesson_group(plan_setup):
    """45 мин: 2 урока = 4 занятия с номерами 0.5 … 2.0."""
    from apps.scheduling.repository import generate_for_group
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_half__', duration=45, lessons_total=2)

    generate_for_group(group_id)

    numbers = [float(n) for n in PlannedLesson.objects
               .filter(group_id=group_id, seq__isnull=False)
               .order_by('seq').values_list('lesson_number', flat=True)]
    assert numbers == [0.5, 1.0, 1.5, 2.0]


@pytest.mark.django_db
def test_generate_falls_back_to_direction(plan_setup):
    """Пустое поле — длина курса берётся из направления (регресс)."""
    from apps.scheduling.repository import generate_for_group
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_full__', duration=90, lessons_total=None)

    generate_for_group(group_id)

    assert PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False).count() == 8


@pytest.mark.django_db
def test_permanent_change_keeps_group_lessons_total(plan_setup):
    """Смена расписания группе-остатку не раздувает курс до длины направления."""
    from apps.scheduling.repository import generate_for_group, permanent_change
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_permchange__', duration=90, lessons_total=2)
    generate_for_group(group_id)
    assert PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False).count() == 2

    # Переносим расписание с первого занятия на вторник 10:00 с 2026-08-04.
    permanent_change(
        group_id,
        from_seq=1,
        effective_from=datetime.date(2026, 8, 4),
        new_slots=[{'day_of_week': 2, 'start_time': '10:00'}],
    )

    rows = list(PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False)
                .order_by('seq').values_list('seq', 'scheduled_date'))
    assert [r[0] for r in rows] == [1, 2], 'курс раздулся до длины направления'
    assert rows[0][1] == datetime.date(2026, 8, 4)


@pytest.mark.django_db
def test_resize_plan_shrinks_tail(plan_setup):
    """Уменьшили длину — лишние непроведённые занятия удалены с конца."""
    from apps.scheduling.repository import generate_for_group, resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_shrink__', duration=90, lessons_total=None)
    generate_for_group(group_id)
    assert PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False).count() == 8

    with connection.cursor() as cur:
        cur.execute("UPDATE groups SET lessons_total = 3 WHERE id = %s", [group_id])
    resize_plan(group_id)

    seqs = list(PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False)
                .order_by('seq').values_list('seq', flat=True))
    assert seqs == [1, 2, 3]


@pytest.mark.django_db
def test_resize_plan_extends_tail(plan_setup):
    """Увеличили длину — недостающие занятия дописаны после последней даты."""
    from apps.scheduling.repository import generate_for_group, resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_extend__', duration=90, lessons_total=2)
    generate_for_group(group_id)
    assert PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False).count() == 2

    with connection.cursor() as cur:
        cur.execute("UPDATE groups SET lessons_total = 4 WHERE id = %s", [group_id])
    resize_plan(group_id)

    rows = list(PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False)
                .order_by('seq').values_list('seq', 'lesson_number', 'scheduled_date'))
    assert [r[0] for r in rows] == [1, 2, 3, 4]
    assert [float(r[1]) for r in rows] == [1.0, 2.0, 3.0, 4.0]
    # Слот — понедельник, старт 03.08 → 3-е и 4-е занятия на следующих понедельниках.
    assert rows[2][2] == datetime.date(2026, 8, 17)
    assert rows[3][2] == datetime.date(2026, 8, 24)


@pytest.mark.django_db
def test_resize_plan_refuses_to_cut_recorded(plan_setup):
    """Нельзя урезать план короче уже проведённых занятий."""
    from apps.scheduling.exceptions import PlanHasRecordedLessons
    from apps.scheduling.repository import generate_for_group, resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_recorded__', duration=90, lessons_total=None)
    generate_for_group(group_id)
    # Первые три занятия «проведены».
    PlannedLesson.objects.filter(group_id=group_id, seq__in=[1, 2, 3]).update(status='done')

    with connection.cursor() as cur:
        cur.execute("UPDATE groups SET lessons_total = 2 WHERE id = %s", [group_id])
    with pytest.raises(PlanHasRecordedLessons):
        resize_plan(group_id)

    # План не пострадал.
    assert PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False).count() == 8


def _course_rows(group_id):
    return list(PlannedLesson.objects.filter(group_id=group_id, seq__isnull=False)
                .order_by('seq').values_list('seq', 'lesson_number'))


@pytest.mark.django_db
def test_resize_plan_extend_respects_offset(plan_setup):
    """Группа-продолжение (офсет 3): план идёт с урока 4. Длина курса 6 → 8:
    дописываются уроки 7–8, а не позиции до seq=8 (это дало бы уроки 9–11
    сверх курса)."""
    from apps.scheduling.repository import generate_for_group, resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_off_ext__', duration=90, lessons_total=6)
        cur.execute("UPDATE groups SET lesson_number_offset = 3 WHERE id = %s", [group_id])
    generate_for_group(group_id)
    assert [float(n) for _, n in _course_rows(group_id)] == [4.0, 5.0, 6.0]

    with connection.cursor() as cur:
        cur.execute("UPDATE groups SET lessons_total = NULL WHERE id = %s", [group_id])
    resize_plan(group_id)  # → длина направления, 8

    rows = _course_rows(group_id)
    assert [s for s, _ in rows] == [1, 2, 3, 4, 5]
    assert [float(n) for _, n in rows] == [4.0, 5.0, 6.0, 7.0, 8.0]


@pytest.mark.django_db
def test_resize_plan_shrink_respects_offset(plan_setup):
    """Офсет 3, длина 8 → 6: удаляются уроки 7–8 (номер > длины курса), хотя
    их seq (4–5) не превышает новую длину."""
    from apps.scheduling.repository import generate_for_group, resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_off_shr__', duration=90, lessons_total=None)
        cur.execute("UPDATE groups SET lesson_number_offset = 3 WHERE id = %s", [group_id])
    generate_for_group(group_id)
    assert [float(n) for _, n in _course_rows(group_id)] == [4.0, 5.0, 6.0, 7.0, 8.0]

    with connection.cursor() as cur:
        cur.execute("UPDATE groups SET lessons_total = 6 WHERE id = %s", [group_id])
    resize_plan(group_id)

    assert [float(n) for _, n in _course_rows(group_id)] == [4.0, 5.0, 6.0]


@pytest.mark.django_db
def test_resize_plan_from_empty_starts_after_offset(plan_setup):
    """Плана нет, офсет 3: resize строит план с урока 4, как generate_for_group."""
    from apps.scheduling.repository import resize_plan
    direction_id, teacher_id = plan_setup
    with connection.cursor() as cur:
        group_id = _make_group(cur, direction_id, teacher_id,
                               name='__plt_off_new__', duration=90, lessons_total=6)
        cur.execute("UPDATE groups SET lesson_number_offset = 3 WHERE id = %s", [group_id])
    resize_plan(group_id)

    assert [float(n) for _, n in _course_rows(group_id)] == [4.0, 5.0, 6.0]
