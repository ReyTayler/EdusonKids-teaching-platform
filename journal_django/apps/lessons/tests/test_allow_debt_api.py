"""
Запись занятия «в долг» из интерфейса: суперадмин снимает блок по
отрицательному балансу (решение пользователя 2026-08-25).

До этого обойти `assert_students_paid` можно было только management-командой на
сервере (`record_lesson_override` / `mark_attendance_override`). Теперь тот же
`skip_balance_check` доступен через admin API, но с двумя ограничениями:

  • ТОЛЬКО роль superadmin — admin и manager получают 403, даже передав флаг
    (молча игнорировать нельзя: клиент решил бы, что запись прошла в долг);
  • ТОЛЬКО явным `allow_debt=true` — без флага блок работает как раньше, чтобы
    суперадмин не записывал долги случайно. Флаг ставит модалка-предупреждение
    после первого отказа.

Занятие остаётся ПЛАТНЫМ: баланс уходит глубже в минус, зарплата преподавателю
начисляется. Это не «бесплатное занятие» (is_free), где с ученика не списывается.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.lessons import services
from apps.lessons.models import Lesson
from apps.lessons.views import UNPAID_ATTENDANCE_BLOCKED

pytestmark = pytest.mark.django_db

BASE_URL = '/api/admin/lessons'


@pytest.fixture
def group_in_debt(group_fixture, student_fixture):
    """Группа с одним учеником без единого оплаченного урока."""
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


def _payload(ctx, teacher_id, **extra):
    return {
        'lesson_date': '2026-06-29',
        'group_id': ctx['group'],
        'teacher_id': teacher_id,
        'lesson_number': 1,
        'lesson_duration_minutes': 60,
        'attendance': [{'student_id': ctx['student'], 'present': True}],
        **extra,
    }


def _absent_lesson(ctx, teacher_id) -> int:
    """Записанное занятие, где ученик отмечен отсутствующим (баланс не нужен)."""
    result = services.record_lesson(
        lesson_date='2026-06-29', teacher_id=teacher_id, group_id=ctx['group'],
        original_teacher_id=None, lesson_number=Decimal('1'),
        lesson_duration_minutes=60, lesson_type='regular', record_url=None,
        submitted_by_token='__allow_debt_test__', submit_date='2026-06-29',
        attendance=[{'student_id': ctx['student'], 'present': False}],
    )
    return result['lesson_id']


# --- запись занятия целиком -------------------------------------------------

def test_superadmin_records_lesson_in_debt(superadmin_client, group_in_debt,
                                           teacher_id_fixture):
    resp = superadmin_client.post(
        BASE_URL, _payload(group_in_debt, teacher_id_fixture, allow_debt=True),
        format='json')

    assert resp.status_code == 201, resp.json()
    assert Lesson.objects.filter(group_id=group_in_debt['group']).count() == 1


def test_superadmin_without_flag_still_blocked(superadmin_client, group_in_debt,
                                               teacher_id_fixture):
    """Флаг обязан быть явным — иначе долги записывались бы случайно."""
    resp = superadmin_client.post(
        BASE_URL, _payload(group_in_debt, teacher_id_fixture), format='json')

    assert resp.status_code == 400
    assert resp.json()['code'] == UNPAID_ATTENDANCE_BLOCKED, 'фронт по коду поднимает модалку'
    assert not Lesson.objects.filter(group_id=group_in_debt['group']).exists()


def test_admin_cannot_record_in_debt(admin_client, group_in_debt, teacher_id_fixture):
    """Обычному админу флаг недоступен — 403, а не тихое игнорирование."""
    resp = admin_client.post(
        BASE_URL, _payload(group_in_debt, teacher_id_fixture, allow_debt=True),
        format='json')

    assert resp.status_code == 403
    assert not Lesson.objects.filter(group_id=group_in_debt['group']).exists()


# --- точечная правка ячейки посещаемости ------------------------------------

def test_superadmin_marks_cell_in_debt(superadmin_client, group_in_debt,
                                       teacher_id_fixture):
    lesson_id = _absent_lesson(group_in_debt, teacher_id_fixture)
    url = f"{BASE_URL}/{lesson_id}/attendance/{group_in_debt['student']}"

    resp = superadmin_client.patch(url, {'present': True, 'allow_debt': True},
                                   format='json')

    assert resp.status_code == 200, resp.json()
    with connection.cursor() as cur:
        cur.execute('SELECT present FROM lesson_attendance WHERE lesson_id = %s '
                    'AND student_id = %s', [lesson_id, group_in_debt['student']])
        assert cur.fetchone()[0] is True


def test_cell_without_flag_still_blocked(superadmin_client, group_in_debt,
                                         teacher_id_fixture):
    lesson_id = _absent_lesson(group_in_debt, teacher_id_fixture)
    url = f"{BASE_URL}/{lesson_id}/attendance/{group_in_debt['student']}"

    resp = superadmin_client.patch(url, {'present': True}, format='json')

    assert resp.status_code == 400
    assert resp.json()['code'] == UNPAID_ATTENDANCE_BLOCKED


def test_admin_cannot_mark_cell_in_debt(admin_client, group_in_debt,
                                        teacher_id_fixture):
    lesson_id = _absent_lesson(group_in_debt, teacher_id_fixture)
    url = f"{BASE_URL}/{lesson_id}/attendance/{group_in_debt['student']}"

    resp = admin_client.patch(url, {'present': True, 'allow_debt': True}, format='json')

    assert resp.status_code == 403

