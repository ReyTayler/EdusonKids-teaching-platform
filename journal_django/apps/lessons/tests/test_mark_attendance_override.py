"""
Команда mark_attendance_override: отметка присутствия вопреки отрицательному балансу.

Ручное решение администратора: занятие было, долг разбирается отдельно. Урок
остаётся платным (в отличие от «бесплатного занятия»). Обычный путь через API
по-прежнему блокируется — послабление живёт только в команде.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection

from apps.lessons import repository, services
from apps.lessons.exceptions import UnpaidAttendanceBlocked

pytestmark = pytest.mark.django_db


@pytest.fixture
def lesson_with_debt(group_fixture, teacher_id_fixture, student_fixture):
    """Проведённый урок, ученик отсутствовал, оплаченных уроков нет (долг)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
            'VALUES (%s,%s,0,true) RETURNING id', [group_fixture, student_fixture])
        membership_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s,%s,'2026-06-07',22,90,'regular',NOW(),'__ovr_test__') RETURNING id",
            [group_fixture, teacher_id_fixture])
        lesson_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
            'VALUES (%s,%s,false)', [lesson_id, student_fixture])
    yield {'lesson': lesson_id, 'student': student_fixture, 'membership': membership_id}
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def _resolution(ctx: dict, status: str):
    """Резолюция пропуска по уроку из фикстуры."""
    from apps.extra_lessons.models import AbsenceResolution
    from apps.lessons.models import Lesson
    group_id = Lesson.objects.filter(id=ctx['lesson']).values_list(
        'group_id', flat=True).first()
    return AbsenceResolution.objects.create(
        missed_lesson_id=ctx['lesson'], student_id=ctx['student'],
        group_id=group_id, status=status)


def _present(lesson_id, student_id) -> bool:
    with connection.cursor() as cur:
        cur.execute('SELECT present FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                    [lesson_id, student_id])
        return cur.fetchone()[0]


def test_api_path_still_blocked_by_debt(lesson_with_debt):
    """Защита на месте: обычная правка ячейки при долге по-прежнему запрещена."""
    with pytest.raises(UnpaidAttendanceBlocked):
        services.update_attendance_cell(
            lesson_with_debt['lesson'], lesson_with_debt['student'], present=True)
    assert _present(lesson_with_debt['lesson'], lesson_with_debt['student']) is False


def test_dry_run_changes_nothing(lesson_with_debt):
    call_command('mark_attendance_override',
                 '--lesson', str(lesson_with_debt['lesson']),
                 '--student', str(lesson_with_debt['student']))
    assert _present(lesson_with_debt['lesson'], lesson_with_debt['student']) is False


def test_apply_marks_present_and_moves_progress(lesson_with_debt):
    """Галочка ставится, прогресс растёт — урок остаётся платным."""
    call_command('mark_attendance_override',
                 '--lesson', str(lesson_with_debt['lesson']),
                 '--student', str(lesson_with_debt['student']), '--apply')

    assert _present(lesson_with_debt['lesson'], lesson_with_debt['student']) is True
    with connection.cursor() as cur:
        cur.execute('SELECT lessons_done, 1 FROM group_memberships WHERE id = %s',
                    [lesson_with_debt['membership']])
        lessons_done, _ = cur.fetchone()
        assert lessons_done == Decimal('1.0')
        cur.execute('SELECT is_free FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                    [lesson_with_debt['lesson'], lesson_with_debt['student']])
        assert cur.fetchone()[0] is False, 'занятие должно остаться платным'


def test_pending_absence_resolution_removed(lesson_with_debt):
    """Нерешённый пропуск снимается: занятие всё-таки состоялось."""
    _resolution(lesson_with_debt, 'pending')

    call_command('mark_attendance_override',
                 '--lesson', str(lesson_with_debt['lesson']),
                 '--student', str(lesson_with_debt['student']), '--apply')

    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM absence_resolutions WHERE missed_lesson_id = %s',
                    [lesson_with_debt['lesson']])
        assert cur.fetchone()[0] == 0


def test_refuses_when_absence_already_resolved(lesson_with_debt):
    """По пропуску уже принято решение (сожжён) — трогать нельзя, спишется дважды."""
    _resolution(lesson_with_debt, 'burned')

    with pytest.raises(CommandError):
        call_command('mark_attendance_override',
                     '--lesson', str(lesson_with_debt['lesson']),
                     '--student', str(lesson_with_debt['student']), '--apply')
    assert _present(lesson_with_debt['lesson'], lesson_with_debt['student']) is False


def test_repository_flag_is_opt_in(lesson_with_debt):
    """Без флага репозиторий блокирует — послабление именно явное."""
    with pytest.raises(UnpaidAttendanceBlocked):
        repository.update_attendance_cell(
            lesson_with_debt['lesson'], lesson_with_debt['student'], present=True)
    assert repository.update_attendance_cell(
        lesson_with_debt['lesson'], lesson_with_debt['student'],
        present=True, skip_balance_check=True) is True
