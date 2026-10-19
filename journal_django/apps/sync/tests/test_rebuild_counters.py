# journal_django/apps/sync/tests/test_rebuild_counters.py
import pytest
from django.db import connection


from apps.sync.backfills import rebuild_counters


@pytest.mark.django_db
def test_run_fixes_drifted_counter():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__test_sync_teacher_rc__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM directions LIMIT 1")
        direction_row = cur.fetchone()
        created_direction = False
        if direction_row is None:
            cur.execute(
                "INSERT INTO directions (name) VALUES ('__test_sync_direction_rc__') RETURNING id"
            )
            direction_row = cur.fetchone()
            created_direction = True
        direction_id = direction_row[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, lesson_duration_minutes, "
            "lessons_per_week, lesson_number_offset) "
            "VALUES ('__test_sync_group_rc__', %s, %s, false, 90, 1, 0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__test_sync_student_rc__') RETURNING id")
        student_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true) RETURNING id",
            [group_id, student_id],
        )
        membership_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lessons (lesson_date, teacher_id, group_id, lesson_number, lesson_duration_minutes, "
            "lesson_type, submitted_by_token, submitted_at) "
            "VALUES ('2026-07-13', %s, %s, 1, 90, 'regular', 'TOKRC', now()) RETURNING id",
            [teacher_id, group_id],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)",
            [lesson_id, student_id],
        )

    try:
        result = rebuild_counters.run(dry_run=False)
        assert result['updated'] >= 1
        with connection.cursor() as cur:
            cur.execute("SELECT lessons_done FROM group_memberships WHERE id = %s", [membership_id])
            assert float(cur.fetchone()[0]) == 1.0
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM lesson_attendance WHERE lesson_id = %s", [lesson_id])
            cur.execute("DELETE FROM lessons WHERE id = %s", [lesson_id])
            cur.execute("DELETE FROM group_memberships WHERE id = %s", [membership_id])
            cur.execute("DELETE FROM students WHERE id = %s", [student_id])
            cur.execute("DELETE FROM groups WHERE id = %s", [group_id])
            cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])
            if created_direction:
                cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])


@pytest.mark.django_db
def test_run_dry_run_reports_drift_without_writing():
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name) VALUES ('__test_sync_teacher_rcd__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute("SELECT id FROM directions LIMIT 1")
        direction_row = cur.fetchone()
        created_direction = False
        if direction_row is None:
            cur.execute(
                "INSERT INTO directions (name) VALUES ('__test_sync_direction_rcd__') RETURNING id"
            )
            direction_row = cur.fetchone()
            created_direction = True
        direction_id = direction_row[0]
        cur.execute(
            "INSERT INTO groups (name, direction_id, teacher_id, is_individual, lesson_duration_minutes, "
            "lessons_per_week, lesson_number_offset) "
            "VALUES ('__test_sync_group_rcd__', %s, %s, false, 45, 1, 0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
        cur.execute("INSERT INTO students (full_name) VALUES ('__test_sync_student_rcd__') RETURNING id")
        student_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            "VALUES (%s, %s, 0, true) RETURNING id",
            [group_id, student_id],
        )
        membership_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lessons (lesson_date, teacher_id, group_id, lesson_number, lesson_duration_minutes, "
            "lesson_type, submitted_by_token, submitted_at) "
            "VALUES ('2026-07-13', %s, %s, 1, 45, 'regular', 'TOKRCD', now()) RETURNING id",
            [teacher_id, group_id],
        )
        lesson_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)",
            [lesson_id, student_id],
        )

    try:
        result = rebuild_counters.run(dry_run=True)
        assert result['updated'] == 0
        assert any(d['membership_id'] == membership_id and d['delta'] == 0.5 for d in result['top_drifts'])
        with connection.cursor() as cur:
            cur.execute("SELECT lessons_done FROM group_memberships WHERE id = %s", [membership_id])
            assert float(cur.fetchone()[0]) == 0.0
    finally:
        with connection.cursor() as cur:
            cur.execute("DELETE FROM lesson_attendance WHERE lesson_id = %s", [lesson_id])
            cur.execute("DELETE FROM lessons WHERE id = %s", [lesson_id])
            cur.execute("DELETE FROM group_memberships WHERE id = %s", [membership_id])
            cur.execute("DELETE FROM students WHERE id = %s", [student_id])
            cur.execute("DELETE FROM groups WHERE id = %s", [group_id])
            cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])
            if created_direction:
                cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])
