"""
Гард оплат под флагом settings.PAYMENT_BALANCE_GUARD.

KOTOKOD: ученика с балансом (куплено − пройдено) <= 0 нельзя отметить
присутствующим — `assert_students_paid` бросает UnpaidAttendanceBlocked.
Eduson оплаты в платформе не ведёт (их ноль), поэтому с гардом преподаватель не
записал бы ни одного урока. Флаг по умолчанию выключен.

Выключенный гард — это НЕ «бесплатное занятие» и НЕ «запись в долг» с пометкой:
занятие остаётся обычным платным присутствием (баланс уходит в минус, зарплата
начисляется), просто запрет не срабатывает. Включённый — прежнее поведение.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.finances.repository import balance_for_student
from apps.lessons import repository, services
from apps.lessons.exceptions import UnpaidAttendanceBlocked
from apps.lessons.models import Lesson, LessonAttendance
from apps.lessons.views import UNPAID_ATTENDANCE_BLOCKED
from apps.payroll.models import Payroll

pytestmark = pytest.mark.django_db

BASE_URL = '/api/admin/lessons'


@pytest.fixture
def debtor(group_fixture, student_fixture):
    """Группа с одним учеником без единого оплаченного урока (баланс 0)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s,%s,0,true) RETURNING id', [group_fixture, student_fixture])
        membership_id = cur.fetchone()[0]
    yield {'group': group_fixture, 'student': student_fixture}
    with connection.cursor() as cur:
        for lid in list(Lesson.objects.filter(group_id=group_fixture)
                        .values_list('id', flat=True)):
            cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [lid])
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [membership_id])


def _record(ctx, teacher_id, present=True):
    return services.record_lesson(
        lesson_date='2026-06-29', teacher_id=teacher_id, group_id=ctx['group'],
        original_teacher_id=None, lesson_number=Decimal('1'),
        lesson_duration_minutes=60, lesson_type='regular', record_url=None,
        submitted_by_token='__guard_flag_test__', submit_date='2026-06-29',
        attendance=[{'student_id': ctx['student'], 'present': present}],
    )


def test_default_is_off():
    """Значение по умолчанию задаёт схема django-environ в base.py: без
    переменной окружения гард выключен (режим Eduson)."""
    from config.settings import base
    assert base.env.scheme['PAYMENT_BALANCE_GUARD'] == (bool, False)


# --- выключенный гард (Eduson) ---------------------------------------------

@pytest.mark.usefixtures('payment_balance_guard_off')
class TestGuardOff:

    def test_assert_students_paid_is_noop(self, debtor):
        assert balance_for_student(debtor['student']) <= 0
        repository.assert_students_paid([debtor['student']])  # не бросает

    def test_record_lesson_marks_debtor_as_ordinary_paid_presence(
            self, debtor, teacher_id_fixture):
        result = _record(debtor, teacher_id_fixture)

        att = LessonAttendance.objects.get(lesson_id=result['lesson_id'],
                                           student_id=debtor['student'])
        # Обычное присутствие: не «бесплатное» и не «неоплачиваемый пропуск».
        assert (att.present, att.is_free, att.unpaid_skip) == (True, False, False)
        # Списание с баланса идёт как у любого платного урока — флаг не трогает расчёт.
        assert balance_for_student(debtor['student']) == -1
        # Зарплата начислена как за обычный урок.
        payroll = Payroll.objects.get(lesson_id=result['lesson_id'])
        assert payroll.present_count == 1
        assert result['payment'] > 0

    def test_toggle_cell_to_present_allowed(self, debtor, teacher_id_fixture):
        lesson_id = _record(debtor, teacher_id_fixture, present=False)['lesson_id']

        assert services.update_attendance_cell(lesson_id, debtor['student'], True)

        assert LessonAttendance.objects.get(
            lesson_id=lesson_id, student_id=debtor['student']).present is True

    def test_admin_api_records_without_allow_debt(self, admin_client, debtor,
                                                  teacher_id_fixture):
        """Обычный admin (не суперадмин, без allow_debt) записывает урок с
        должником — модалка «записать в долг» не нужна вовсе."""
        resp = admin_client.post(BASE_URL, {
            'lesson_date': '2026-06-29', 'group_id': debtor['group'],
            'teacher_id': teacher_id_fixture, 'lesson_number': 1,
            'lesson_duration_minutes': 60,
            'attendance': [{'student_id': debtor['student'], 'present': True}],
        }, format='json')

        assert resp.status_code == 201, resp.json()


# --- включённый гард (KOTOKOD) ----------------------------------------------

@pytest.mark.usefixtures('payment_balance_guard_on')
class TestGuardOn:

    def test_record_lesson_blocked(self, debtor, teacher_id_fixture):
        with pytest.raises(UnpaidAttendanceBlocked):
            _record(debtor, teacher_id_fixture)
        assert not Lesson.objects.filter(group_id=debtor['group']).exists()

    def test_admin_api_blocked(self, admin_client, debtor, teacher_id_fixture):
        resp = admin_client.post(BASE_URL, {
            'lesson_date': '2026-06-29', 'group_id': debtor['group'],
            'teacher_id': teacher_id_fixture, 'lesson_number': 1,
            'lesson_duration_minutes': 60,
            'attendance': [{'student_id': debtor['student'], 'present': True}],
        }, format='json')

        assert resp.status_code == 400
        assert resp.json()['code'] == UNPAID_ATTENDANCE_BLOCKED
