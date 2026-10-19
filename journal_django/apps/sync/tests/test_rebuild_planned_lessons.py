# journal_django/apps/sync/tests/test_rebuild_planned_lessons.py
import pytest
from django.db import connection

from apps.sync.backfills import rebuild_planned_lessons


@pytest.mark.django_db
def test_run_rebuilds_plan_for_group_with_facts_and_slots():
    teacher_name = '__test_rpl_teacher__'
    direction_name = '__test_rpl_direction__'
    group_name = '__test_rpl_group__'
    student_name = '__test_rpl_student__'

    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name) VALUES (%s) RETURNING id", [teacher_name],
        )
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, total_lessons) VALUES (%s, 8) "
            "RETURNING id",
            [direction_name],
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, group_start_date, active, created_at, lesson_number_offset) "
            "VALUES (%s, %s, %s, false, 60, '2026-06-01', true, now(), 0) RETURNING id",
            [group_name, direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        # Monday, per project convention day_of_week: Вс(Sun)=0.
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id, day_of_week, start_time) "
            "VALUES (%s, 1, '10:00:00')",
            [group_id],
        )
        cur.execute(
            "INSERT INTO students (full_name) VALUES (%s) RETURNING id", [student_name],
        )
        student_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, active) VALUES (%s, %s, true)",
            [group_id, student_id],
        )
        cur.execute(
            "INSERT INTO lessons (lesson_date, teacher_id, group_id, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token, submitted_at) "
            "VALUES ('2026-06-01', %s, %s, 1, 60, 'regular', 'test-token', now())",
            [teacher_id, group_id],
        )

    try:
        result = rebuild_planned_lessons.run(dry_run=False)

        assert result['groups_total'] >= 1
        assert result['processed'] >= 1
        assert result['rows_written'] >= 1

        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM planned_lessons WHERE group_id = %s", [group_id])
            written = cur.fetchone()[0]
            assert written == 8
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM planned_lessons WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM lessons WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM group_memberships WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM group_schedule_slots WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM groups WHERE id = %s", [group_id])
            cur.execute("DELETE FROM students WHERE id = %s", [student_id])
            cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])
            cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])


@pytest.mark.django_db
def test_run_dry_run_does_not_write():
    teacher_name = '__test_rpl_dry_teacher__'
    direction_name = '__test_rpl_dry_direction__'
    group_name = '__test_rpl_dry_group__'

    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name) VALUES (%s) RETURNING id", [teacher_name],
        )
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, total_lessons) VALUES (%s, 8) "
            "RETURNING id",
            [direction_name],
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, group_start_date, active, created_at, lesson_number_offset) "
            "VALUES (%s, %s, %s, false, 60, '2026-06-01', true, now(), 0) RETURNING id",
            [group_name, direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id, day_of_week, start_time) "
            "VALUES (%s, 1, '10:00:00')",
            [group_id],
        )
        cur.execute(
            "INSERT INTO lessons (lesson_date, teacher_id, group_id, lesson_number, "
            "lesson_duration_minutes, lesson_type, submitted_by_token, submitted_at) "
            "VALUES ('2026-06-01', %s, %s, 1, 60, 'regular', 'test-token', now())",
            [teacher_id, group_id],
        )

    try:
        result = rebuild_planned_lessons.run(dry_run=True)

        assert result['rows_written'] >= 1

        with connection.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM planned_lessons WHERE group_id = %s", [group_id])
            assert cur.fetchone()[0] == 0
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM planned_lessons WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM lessons WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM group_schedule_slots WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM groups WHERE id = %s", [group_id])
            cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])
            cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])


@pytest.mark.django_db
def test_run_skips_group_without_start_date():
    teacher_name = '__test_rpl_nostart_teacher__'
    direction_name = '__test_rpl_nostart_direction__'
    group_name = '__test_rpl_nostart_group__'

    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO teachers (name) VALUES (%s) RETURNING id", [teacher_name],
        )
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name, total_lessons) VALUES (%s, 8) "
            "RETURNING id",
            [direction_name],
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, "
            "lesson_duration_minutes, group_start_date, active, created_at, lesson_number_offset) "
            "VALUES (%s, %s, %s, false, 60, NULL, true, now(), 0) RETURNING id",
            [group_name, direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]

    try:
        result = rebuild_planned_lessons.run(dry_run=False)

        assert result['skipped'] >= 1
        matching = [d for d in result['skipped_details'] if d['group'] == group_name]
        assert matching, 'expected group in skipped_details'
        assert matching[0]['reason'] == 'no_start_date'
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM planned_lessons WHERE group_id = %s", [group_id])
            cur.execute("DELETE FROM groups WHERE id = %s", [group_id])
            cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])
            cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])
