"""
Запись урока и правка посещаемости → начисление движком «Мотивации»
(спека docs/specs/2026-10-20-eduson-motivation-payroll-design.md, §1, §3
«Поводы пересчёта»).

Ставка — фикс по формату группы, число пришедших на сумму не влияет; штраф за
поздний отчёт — только уроку, записанному преподавателем из кабинета
(submitted_by_token='acct:…'). Урок без присутствующих не записывается ни из
кабинета, ни из админки (§1.2). Данные выдуманные, всё в транзакции теста.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.core.utils.dates import MSK
from apps.lessons import repository, services
from apps.lessons.exceptions import LastPresentStudentRemoval, LessonWithoutPresentStudents
from apps.lessons.models import Lesson, LessonAttendance, LessonSkip
from apps.payroll.models import Payroll

pytestmark = pytest.mark.django_db

D = Decimal
BASE_URL = '/api/admin/lessons'
NO_PRESENT = ('Нельзя сохранить урок без присутствующих учеников — '
              'отметьте хотя бы одного пришедшего.')
LAST_PRESENT = ('Нельзя снять отметку с последнего пришедшего — урок без присутствующих '
                'не существует; если урок не состоялся — удалите его и отмените занятие.')


@pytest.fixture
def school(db):
    from apps.directions.models import Direction
    from apps.groups.models import Group
    from apps.memberships.models import GroupMembership
    from apps.students.models import Student
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction = Direction.objects.create(name='Тест Запись Направление', total_lessons=36)
    teacher = Teacher.objects.create(name='Тест Запись Преподаватель', created_at=now)
    group = Group.objects.create(
        name='Тест Запись Группа', direction=direction, teacher=teacher, is_individual=False,
        lesson_duration_minutes=90, active=True, created_at=now)
    students = [Student.objects.create(full_name=f'Тест Запись Ученик {i}', created_at=now)
                for i in range(3)]
    for s in students:
        GroupMembership.objects.create(group=group, student=s, lessons_done=0, active=True)
    return {'group': group, 'teacher': teacher, 'students': students}


def _record(school, *, present, token='admin-imported', date='2026-03-10', number=1):
    return services.record_lesson(
        lesson_date=date, teacher_id=school['teacher'].id, group_id=school['group'].id,
        original_teacher_id=None, lesson_number=number, lesson_duration_minutes=90,
        lesson_type='regular', record_url=None, submitted_by_token=token,
        attendance=[{'student_id': s.id, 'present': flag}
                    for s, flag in zip(school['students'], present)],
    )


# ---------------------------------------------------------------------------
# Блокер «урок без присутствующих» (§1.2)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('present', [(False, False, False), ()])
def test_record_lesson_without_present_is_rejected(school, present):
    with pytest.raises(LessonWithoutPresentStudents) as exc:
        _record(school, present=present)
    assert str(exc.value) == NO_PRESENT
    assert not Lesson.objects.filter(group=school['group']).exists()


def test_record_lesson_marked_skip_leaves_no_present(school):
    """Пришедший, но заранее помеченный «неоплачиваемый пропуск» на этот слот,
    присутствующим не считается — урок без иных пришедших не записывается."""
    repository.set_lesson_skip(school['group'].id, school['students'][0].id, 1, True)
    with pytest.raises(LessonWithoutPresentStudents):
        _record(school, present=(True, False, False))


def test_nonexistent_present_student_does_not_count(school):
    """«Пришедший» с несуществующим id в урок не попадёт (insert_attendance его
    отбросит) — блокер считает по тем, кто реально запишется."""
    with pytest.raises(LessonWithoutPresentStudents):
        services.record_lesson(
            lesson_date='2026-03-10', teacher_id=school['teacher'].id,
            group_id=school['group'].id, original_teacher_id=None, lesson_number=1,
            lesson_duration_minutes=90, lesson_type='regular', record_url=None,
            submitted_by_token='admin-imported',
            attendance=[{'student_id': school['students'][0].id, 'present': False},
                        {'student_id': 999_999_999, 'present': True}],
        )
    assert not Lesson.objects.filter(group=school['group']).exists()


def test_admin_api_without_present_returns_400(school, admin_client):
    resp = admin_client.post(BASE_URL, {
        'lesson_date': '2026-03-11', 'group_id': school['group'].id,
        'teacher_id': school['teacher'].id, 'lesson_number': 1,
        'lesson_duration_minutes': 90,
        'attendance': [{'student_id': s.id, 'present': False} for s in school['students']],
    }, format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == NO_PRESENT
    assert not Lesson.objects.filter(group=school['group']).exists()


def test_admin_api_rejects_cabinet_token(school, admin_client):
    """Админская запись не может выдать себя за кабинетную ('acct:…'): такие
    уроки движок штрафует за поздний отчёт (§1.4)."""
    resp = admin_client.post(BASE_URL, {
        'lesson_date': '2026-03-12', 'group_id': school['group'].id,
        'teacher_id': school['teacher'].id, 'lesson_number': 1,
        'lesson_duration_minutes': 90, 'submitted_by_token': 'acct:1',
        'attendance': [{'student_id': school['students'][0].id, 'present': True}],
    }, format='json')
    assert resp.status_code == 400
    assert 'submitted_by_token' in resp.json()['details']
    assert not Lesson.objects.filter(group=school['group']).exists()


# ---------------------------------------------------------------------------
# Начисление при записи — из движка
# ---------------------------------------------------------------------------

@pytest.mark.usefixtures('payroll_rates')
def test_record_lesson_pays_flat_group_rate(school):
    """Пришёл один из трёх — всё равно ставка группового урока (500), а не
    «за каждого пришедшего»; ответ ядра совпадает со строкой Payroll."""
    result = _record(school, present=(True, False, False))
    row = Payroll.objects.get(lesson_id=result['lesson_id'])
    assert (row.total_students, row.present_count) == (3, 1)
    assert row.payment == row.calc_payment == D('500.00')
    assert row.penalty == D('0.00')
    assert row.teacher_id == school['teacher'].id
    assert 'Групповой урок' in row.rule_note
    assert (result['payment'], result['penalty']) == (D('500.00'), D('0.00'))


@pytest.mark.usefixtures('payroll_rates')
def test_record_lesson_individual_rate(school):
    school['group'].is_individual = True
    school['group'].save(update_fields=['is_individual'])
    result = _record(school, present=(True,))
    assert Payroll.objects.get(lesson_id=result['lesson_id']).payment == D('700.00')


def test_record_lesson_without_rate_version_pays_zero(school):
    from apps.payroll.models import PayRateVersion
    PayRateVersion.objects.all().delete()
    result = _record(school, present=(True, True, False))
    row = Payroll.objects.get(lesson_id=result['lesson_id'])
    assert row.payment == D('0.00')
    assert row.rule_note == 'Нет ставки на 10.03.2026'


def test_late_report_from_cabinet_is_penalized(school, payroll_rates):
    """Урок прошлого дня, записанный из кабинета (acct:) сейчас, — отчёт не в
    день урока: штраф по версии. submitted_at урока — момент отправки."""
    payroll_rates.penalty_enabled = True
    payroll_rates.penalty_mode = 'per_student'
    payroll_rates.penalty_amount = D('40')
    payroll_rates.save()
    before = timezone.now()
    result = _record(school, present=(True, True, False), token='acct:7')
    lesson = Lesson.objects.get(id=result['lesson_id'])
    assert lesson.submitted_by_token == 'acct:7'
    assert lesson.submitted_at >= before - datetime.timedelta(seconds=5)
    row = Payroll.objects.get(lesson=lesson)
    assert (row.payment, row.penalty) == (D('500.00'), D('80.00'))
    assert result['penalty'] == D('80.00')
    assert 'Штраф' in row.rule_note


def test_admin_record_is_never_penalized(school, payroll_rates):
    payroll_rates.penalty_enabled = True
    payroll_rates.penalty_amount = D('100')
    payroll_rates.save()
    result = _record(school, present=(True, False, False))
    assert Payroll.objects.get(lesson_id=result['lesson_id']).penalty == D('0.00')


# ---------------------------------------------------------------------------
# Правка посещаемости и неоплачиваемый пропуск → пересчёт этого урока
# ---------------------------------------------------------------------------

def test_attendance_cell_recalculates_headcount_and_penalty(school, payroll_rates):
    payroll_rates.penalty_enabled = True
    payroll_rates.penalty_mode = 'per_student'
    payroll_rates.penalty_amount = D('40')
    payroll_rates.save()
    result = _record(school, present=(True, False, False), token='acct:7')
    lesson_id = result['lesson_id']
    assert Payroll.objects.get(lesson_id=lesson_id).penalty == D('40.00')

    assert repository.update_attendance_cell(lesson_id, school['students'][1].id, True)
    row = Payroll.objects.get(lesson_id=lesson_id)
    assert (row.total_students, row.present_count) == (3, 2)
    # Ставка — фикс; от числа пришедших зависит только штраф «за ученика».
    assert (row.payment, row.penalty) == (D('500.00'), D('80.00'))


@pytest.mark.usefixtures('payroll_rates')
def test_attendance_cell_keeps_manual_amount(school):
    result = _record(school, present=(True, False, False))
    Payroll.objects.filter(lesson_id=result['lesson_id']).update(
        is_manual=True, payment=D('123.00'))
    repository.update_attendance_cell(result['lesson_id'], school['students'][2].id, True)
    row = Payroll.objects.get(lesson_id=result['lesson_id'])
    assert row.present_count == 2
    assert row.payment == D('123.00')
    assert row.calc_payment == D('500.00')


@pytest.mark.usefixtures('payroll_rates')
def test_unpaid_skip_recalculates_headcount(school):
    result = _record(school, present=(True, False, False))
    assert repository.set_unpaid_skip(result['lesson_id'], school['students'][2].id, True)
    row = Payroll.objects.get(lesson_id=result['lesson_id'])
    assert (row.total_students, row.present_count) == (2, 1)
    assert row.payment == D('500.00')


@pytest.mark.usefixtures('payroll_rates')
def test_attendance_cell_creates_missing_payroll(school):
    """Урок без строки зарплаты (перенесён из старой системы) получает её при
    первой правке посещаемости — история считается вся (§1.8)."""
    lesson = Lesson.objects.create(
        group=school['group'], teacher=school['teacher'], lesson_date=datetime.date(2026, 3, 5),
        lesson_number=D('1'), lesson_duration_minutes=90, lesson_type='regular',
        submitted_at=timezone.now(), submitted_by_token='eduson-import')
    repository.update_attendance_cell(lesson.id, school['students'][0].id, True)
    row = Payroll.objects.get(lesson=lesson)
    assert (row.present_count, row.payment) == (1, D('500.00'))


@pytest.mark.usefixtures('payroll_rates')
def test_lesson_patch_recalculates_teacher_and_rate(school):
    """Смена преподавателя урока в админке переносит начисление на него."""
    from apps.teachers.models import Teacher
    result = _record(school, present=(True, False, False))
    other = Teacher.objects.create(name='Тест Запись Замена', created_at=timezone.now())
    services.update_lesson(result['lesson_id'], {'teacher_id': other.id})
    assert Payroll.objects.get(lesson_id=result['lesson_id']).teacher_id == other.id


# ---------------------------------------------------------------------------
# Правило §1.2 и для правок: последнего пришедшего не снять
# ---------------------------------------------------------------------------

def _present(lesson_id, student):
    return LessonAttendance.objects.get(lesson_id=lesson_id, student=student).present


def test_unmark_last_present_cell_is_rejected(school):
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    first = school['students'][0]
    with pytest.raises(LastPresentStudentRemoval) as exc:
        repository.update_attendance_cell(lesson_id, first.id, False)
    assert str(exc.value) == LAST_PRESENT
    assert isinstance(exc.value, LessonWithoutPresentStudents)
    assert _present(lesson_id, first) is True


def test_unmark_present_cell_allowed_while_others_present(school):
    lesson_id = _record(school, present=(True, True, False))['lesson_id']
    assert repository.update_attendance_cell(lesson_id, school['students'][0].id, False)
    # Последнего оставшегося — уже нельзя.
    with pytest.raises(LastPresentStudentRemoval):
        repository.update_attendance_cell(lesson_id, school['students'][1].id, False)


def test_last_present_may_switch_to_free(school):
    """«Бесплатно» — тоже присутствие: отметка «был» не снимается."""
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    assert repository.update_attendance_cell(
        lesson_id, school['students'][0].id, True, is_free=True)


def test_api_unmark_last_present_returns_400(school, admin_client):
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    first = school['students'][0]
    resp = admin_client.patch(f'{BASE_URL}/{lesson_id}/attendance/{first.id}',
                              {'present': False}, format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == LAST_PRESENT
    assert _present(lesson_id, first) is True


def test_unpaid_skip_on_last_present_is_rejected(school, admin_client):
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    first = school['students'][0]
    with pytest.raises(LastPresentStudentRemoval):
        repository.set_unpaid_skip(lesson_id, first.id, True)
    resp = admin_client.patch(f'{BASE_URL}/{lesson_id}/unpaid-skip/{first.id}',
                              {'value': True}, format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == LAST_PRESENT
    assert _present(lesson_id, first) is True
    # Отсутствовавшему пометку ставить можно — пришедших не убавится.
    assert repository.set_unpaid_skip(lesson_id, school['students'][1].id, True)


def test_slot_skip_on_last_present_is_rejected_and_not_kept(school, admin_client):
    """Пометка слота материализуется на проведённом уроке — для последнего
    пришедшего это отказ, и сама пометка слота откатывается."""
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    group, first = school['group'], school['students'][0]
    with pytest.raises(LastPresentStudentRemoval):
        services.set_lesson_skip(group.id, first.id, 1, True)
    resp = admin_client.patch(f'/api/admin/groups/{group.id}/lesson-skips',
                              {'student_id': first.id, 'lesson_number': 1, 'value': True},
                              format='json')
    assert resp.status_code == 400
    assert resp.json()['error'] == LAST_PRESENT
    assert not LessonSkip.objects.filter(group=group, student=first).exists()
    assert _present(lesson_id, first) is True


# ---------------------------------------------------------------------------
# Ручная правка суммы (PATCH /api/admin/payroll/<id>)
# ---------------------------------------------------------------------------

def test_manual_amount_survives_attendance_edit(school, payroll_rates, superadmin_client):
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    row = Payroll.objects.get(lesson_id=lesson_id)
    resp = superadmin_client.patch(f'/api/admin/payroll/{row.id}', {'payment': 432},
                                   format='json')
    assert resp.status_code == 200, resp.json()
    row.refresh_from_db()
    assert row.is_manual is True
    assert row.payment == D('432.00')
    # Правка не трогает расчётную сумму и пояснение.
    assert row.calc_payment == D('500.00')
    assert 'Групповой урок' in row.rule_note

    # Ставка изменилась, затем правят посещаемость: сумма — ручная, расчёт — новый.
    payroll_rates.group_rate = D('650')
    payroll_rates.save()
    repository.update_attendance_cell(lesson_id, school['students'][1].id, True)
    row.refresh_from_db()
    assert row.payment == D('432.00')
    assert row.calc_payment == D('650.00')
    assert row.present_count == 2


# ---------------------------------------------------------------------------
# Правка даты урока: версия ставок и штраф — по новой дате
# ---------------------------------------------------------------------------

def test_lesson_date_patch_switches_rate_version(school, payroll_rates):
    from apps.payroll.models import PayRateVersion
    PayRateVersion.objects.create(
        effective_from=datetime.date(2026, 3, 15), group_rate=D('650'),
        individual_rate=D('900'), cancel_mode='percent', cancel_value=D('50'))
    lesson_id = _record(school, present=(True, False, False))['lesson_id']
    assert Payroll.objects.get(lesson_id=lesson_id).payment == D('500.00')

    services.update_lesson(lesson_id, {'lesson_date': datetime.date(2026, 3, 20)})

    row = Payroll.objects.get(lesson_id=lesson_id)
    assert row.payment == D('650.00')
    assert 'с 15.03.2026' in row.rule_note


def test_lesson_date_patch_recomputes_cabinet_penalty(school, payroll_rates):
    """Штраф кабинетного урока — «отчёт отправлен позже даты урока». Дату урока
    правят — штраф пересчитывается от новой даты (момент отправки не меняется).
    Это правило, а не побочный эффект: ошибочную дату исправили на день
    отправки — отчёт сдан в день урока, штрафа нет."""
    payroll_rates.penalty_enabled = True
    payroll_rates.penalty_amount = D('100')
    payroll_rates.save()
    lesson_id = _record(school, present=(True, False, False), token='acct:7')['lesson_id']
    assert Payroll.objects.get(lesson_id=lesson_id).penalty == D('100.00')

    submitted_day = Lesson.objects.get(id=lesson_id).submitted_at.astimezone(MSK).date()
    services.update_lesson(lesson_id, {'lesson_date': submitted_day})

    assert Payroll.objects.get(lesson_id=lesson_id).penalty == D('0.00')
