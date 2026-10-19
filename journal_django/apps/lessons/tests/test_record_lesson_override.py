"""
Команда record_lesson_override: запись занятия вопреки отрицательному балансу.

Занятие остаётся платным; номер считается тем же правилом, что в кабинете
преподавателя. Обычный путь записи по-прежнему блокируется долгом.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.lessons import services
from apps.lessons.exceptions import UnpaidAttendanceBlocked
from apps.lessons.models import Lesson

pytestmark = pytest.mark.django_db


@pytest.fixture
def group_in_debt(group_fixture, teacher_id_fixture, student_fixture):
    """Группа с одним учеником без оплаченных уроков."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s,%s,0,true) RETURNING id', [group_fixture, student_fixture])
        membership_id = cur.fetchone()[0]
    yield {'group': group_fixture, 'student': student_fixture, 'membership': membership_id}
    with connection.cursor() as cur:
        lesson_ids = list(Lesson.objects.filter(group_id=group_fixture).values_list('id', flat=True))
        for lid in lesson_ids:
            cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def test_normal_path_still_blocked(group_in_debt):
    """Защита на месте: обычная запись при долге по-прежнему запрещена."""
    with pytest.raises(UnpaidAttendanceBlocked):
        services.record_lesson(
            lesson_date='2026-06-29', teacher_id=None, group_id=group_in_debt['group'],
            original_teacher_id=None, lesson_number=Decimal('1'),
            lesson_duration_minutes=60, lesson_type='regular', record_url=None,
            submitted_by_token='__t__', submit_date='2026-06-29',
            attendance=[{'student_id': group_in_debt['student'], 'present': True}],
        )


def test_dry_run_creates_nothing(group_in_debt):
    call_command('record_lesson_override', '--group', str(group_in_debt['group']),
                 '--date', '2026-06-29')
    assert Lesson.objects.filter(group_id=group_in_debt['group']).count() == 0


def test_apply_records_lesson_and_moves_progress(group_in_debt):
    call_command('record_lesson_override', '--group', str(group_in_debt['group']),
                 '--date', '2026-06-29', '--apply')

    lesson = Lesson.objects.filter(group_id=group_in_debt['group']).values(
        'id', 'lesson_number', 'lesson_date', 'lesson_type').first()
    assert lesson is not None
    assert lesson['lesson_number'] == Decimal('1.0'), 'пройдено 0 + шаг 1'
    assert lesson['lesson_type'] == 'regular'
    with connection.cursor() as cur:
        cur.execute('SELECT lessons_done FROM group_memberships WHERE id = %s',
                    [group_in_debt['membership']])
        assert cur.fetchone()[0] == Decimal('1.0')
        cur.execute('SELECT present, is_free FROM lesson_attendance WHERE lesson_id = %s',
                    [lesson['id']])
        present, is_free = cur.fetchone()
        assert present is True
        assert is_free is False, 'занятие должно остаться платным'


def test_refuses_when_lesson_already_exists(group_in_debt):
    call_command('record_lesson_override', '--group', str(group_in_debt['group']),
                 '--date', '2026-06-29', '--apply')
    with pytest.raises(CommandError):
        call_command('record_lesson_override', '--group', str(group_in_debt['group']),
                     '--date', '2026-06-29', '--apply')
    assert Lesson.objects.filter(group_id=group_in_debt['group']).count() == 1


def test_absent_student_marked_absent(group_in_debt):
    call_command('record_lesson_override', '--group', str(group_in_debt['group']),
                 '--date', '2026-06-29', '--absent', str(group_in_debt['student']), '--apply')
    lesson_id = Lesson.objects.filter(group_id=group_in_debt['group']).values_list(
        'id', flat=True).first()
    with connection.cursor() as cur:
        cur.execute('SELECT present FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        assert cur.fetchone()[0] is False
        cur.execute('SELECT lessons_done FROM group_memberships WHERE id = %s',
                    [group_in_debt['membership']])
        assert cur.fetchone()[0] == Decimal('0.0'), 'отсутствовавшему прогресс не идёт'


def test_unknown_group_fails(group_in_debt):
    with pytest.raises(CommandError):
        call_command('record_lesson_override', '--group', '999999999', '--date', '2026-06-29')
