"""Integration-тесты apps.extra_lessons.services (реальная БД)."""
from __future__ import annotations

from decimal import Decimal

import datetime

import pytest
from django.db import connection

from apps.extra_lessons import repository, services
from apps.extra_lessons.exceptions import (
    AbsentStudentNotRecordable, DuplicateAssignment, MembershipHasScheduledMakeups,
    MissedLessonNotFound, NotTeachersAssignment, StudentNotAbsent,
)
from apps.extra_lessons.models import (
    MAKEUP_DONE, MAKEUP_SCHEDULED, PENDING, AbsenceResolution,
)
from apps.lessons.exceptions import UnpaidAttendanceBlocked
from apps.lessons.models import Lesson, LessonAttendance
from apps.payroll.models import Payroll

pytestmark = pytest.mark.django_db


def _cleanup_fact(lesson_id):
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def _delete_resolutions(missed_lesson_id):
    """Сносит резолюции пропуска (снимает DB-level FK fact_lesson → lessons ДО
    удаления факт-урока в _cleanup_fact)."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = %s', [missed_lesson_id])


@pytest.fixture
def resolution_cleanup(missed_lesson_fixture):
    """Сносит созданные тестом резолюции ДО teardown missed_lesson_fixture.

    absence_resolutions.missed_lesson — реальный DB-level FK на lessons, а
    teardown missed_lesson_fixture делает raw `DELETE FROM lessons` и НЕ чистит
    absence_resolutions. Фикстура зависит от missed_lesson_fixture, поэтому её
    teardown отрабатывает раньше — снимает FK-блокировку удаления урока."""
    yield missed_lesson_fixture
    _delete_resolutions(missed_lesson_fixture)


@pytest.fixture
def extra_missed_lessons(group_fixture, teacher_fixture):
    """Две дополнительные строки lessons (той же группы) — для сценариев, где
    нужно несколько пропущенных уроков одного ученика (UNIQUE per пропуск×ученик).
    Teardown чистит и резолюции на этих уроках, и сами уроки (FK-порядок)."""
    ids = []
    with connection.cursor() as cur:
        for i, date in enumerate(('2026-04-10', '2026-04-11'), start=1):
            cur.execute(
                """
                INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number,
                                     lesson_duration_minutes, lesson_type, submitted_at,
                                     submitted_by_token)
                VALUES (%s, %s, %s, %s, 60, 'regular', now(), %s) RETURNING id
                """,
                [group_fixture, teacher_fixture, date, i, f'__el_svc_token_{i}__'],
            )
            ids.append(cur.fetchone()[0])
    yield ids
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id = ANY(%s)', [ids])
        cur.execute('DELETE FROM lessons WHERE id = ANY(%s)', [ids])


class _FakeRequest:
    """Минимальная заглушка request для log_event (без HTTP-контекста)."""
    META = {}
    user = None


def test_create_assignment_happy_path(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Пропуск без авто-pending (фикстура не вызывает record_lesson) → назначение
    идёт через create_scheduled_direct и сразу в статусе makeup_scheduled."""
    result = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture,
            'teacher_id': teacher_fixture,
            'student_ids': [student_fixture],
            'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00',
            'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    assert result['created'] == 1
    rid = result['resolution_ids'][0]
    full = repository.get_resolution_full(rid)
    assert full['status'] == MAKEUP_SCHEDULED
    assert full['missed_lesson_id'] == missed_lesson_fixture


def test_create_assignment_transitions_existing_pending(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Если pending-резолюция уже авто-создана (при записи урока), назначение
    переводит ИМЕННО ЭТУ строку в makeup_scheduled — не создаёт вторую (UNIQUE)."""
    repository.autocreate_pending(missed_lesson_fixture, [student_fixture])

    result = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture,
            'teacher_id': teacher_fixture,
            'student_ids': [student_fixture],
            'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00',
            'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    assert result['created'] == 1

    rows = list(AbsenceResolution.objects.filter(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
    ))
    assert len(rows) == 1
    assert rows[0].status == MAKEUP_SCHEDULED
    assert rows[0].id == result['resolution_ids'][0]


def test_create_assignment_raises_if_missed_lesson_not_found(teacher_fixture, student_fixture):
    with pytest.raises(MissedLessonNotFound):
        services.create_assignment(
            {
                'missed_lesson_id': 999_999_999,
                'teacher_id': teacher_fixture,
                'student_ids': [student_fixture],
                'scheduled_date': '2026-04-05',
                'scheduled_time': '15:00',
                'duration_minutes': 45,
            },
            _FakeRequest(),
        )


def test_create_assignment_raises_on_duplicate(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Повторное назначение того же пропуска тому же ученику (уже makeup_scheduled)
    → DuplicateAssignment."""
    data = {
        'missed_lesson_id': missed_lesson_fixture,
        'teacher_id': teacher_fixture,
        'student_ids': [student_fixture],
        'scheduled_date': '2026-04-05',
        'scheduled_time': '15:00',
        'duration_minutes': 45,
    }
    services.create_assignment(data, _FakeRequest())
    with pytest.raises(DuplicateAssignment):
        services.create_assignment(data, _FakeRequest())


def test_create_assignment_raises_if_student_not_absent(
    teacher_fixture, missed_lesson_fixture, resolution_cleanup,
):
    """Ученик, отмеченный present=true (или вообще посторонний) на пропущенном
    уроке, не должен получить доп.урок — иначе преподавателю платится зарплата
    за компенсацию несуществующего пропуска."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) "
            "VALUES ('__el_present_student__') RETURNING id"
        )
        present_student_id = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) VALUES (%s, %s, true)',
            [missed_lesson_fixture, present_student_id],
        )
    try:
        with pytest.raises(StudentNotAbsent):
            services.create_assignment(
                {
                    'missed_lesson_id': missed_lesson_fixture,
                    'teacher_id': teacher_fixture,
                    'student_ids': [present_student_id],
                    'scheduled_date': '2026-04-05',
                    'scheduled_time': '15:00',
                    'duration_minutes': 45,
                },
                _FakeRequest(),
            )
        # present_student present=true → авто-pending для него не создавался, и
        # назначение не прошло → у него нет НИКАКОЙ резолюции.
        assert not AbsenceResolution.objects.filter(
            missed_lesson_id=missed_lesson_fixture, student_id=present_student_id,
        ).exists()
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE student_id = %s', [present_student_id])
            cur.execute('DELETE FROM students WHERE id = %s', [present_student_id])


def test_create_assignment_raises_if_student_unpaid(
    teacher_fixture, missed_lesson_unpaid_fixture, unpaid_student_fixture,
):
    """Ученик без оплаченных уроков (balance<=0) — доп.урок ему назначить
    нельзя, ничего не создаётся (см. docs/security-guidelines.md — денежный
    учёт должен блокировать неоплаченное потребление на всех путях)."""
    with pytest.raises(UnpaidAttendanceBlocked):
        services.create_assignment(
            {
                'missed_lesson_id': missed_lesson_unpaid_fixture,
                'teacher_id': teacher_fixture,
                'student_ids': [unpaid_student_fixture],
                'scheduled_date': '2026-04-05',
                'scheduled_time': '15:00',
                'duration_minutes': 45,
            },
            _FakeRequest(),
        )
    # missed_lesson_unpaid_fixture авто-создал pending для unpaid_student. Блок по
    # балансу не должен ПЕРЕВЕСТИ его в makeup_scheduled — резолюция остаётся pending.
    assert not AbsenceResolution.objects.filter(
        missed_lesson_id=missed_lesson_unpaid_fixture, status=MAKEUP_SCHEDULED,
    ).exists()


def test_record_raises_if_student_balance_dropped_since_assignment(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Назначение создано, пока у ученика был баланс; между созданием и
    фактическим проведением доп.урока баланс обнулился (оплата аннулирована/
    урок оплачен другим уроком) — record() обязан блокировать так же, как
    create_assignment, а не полагаться только на проверку в момент назначения."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payments WHERE student_id = %s', [student_fixture])

    with pytest.raises(UnpaidAttendanceBlocked):
        services.record(
            rid, teacher_id=teacher_fixture, present=True,
            record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
            request=_FakeRequest(),
        )
    assert AbsenceResolution.objects.get(id=rid).status == MAKEUP_SCHEDULED


def test_cancel_assignment_returns_to_pending(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Отмена назначенного доп.урока: makeup_scheduled → pending (пропуск снова
    ждёт решения), параметры сброшены."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    result = services.cancel_assignment(rid, _FakeRequest())
    assert result['status'] == PENDING
    assert result['assigned_teacher_id'] is None
    assert result['scheduled_date'] is None


def test_cancel_assignment_raises_if_not_makeup_scheduled(
    missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Отменить можно только назначенный (makeup_scheduled) доп.урок. Резолюция
    в pending (ещё не назначена) → ValueError (view → 409)."""
    repository.autocreate_pending(missed_lesson_fixture, [student_fixture])
    rid = repository.lock_for_assign(missed_lesson_fixture, student_fixture)['id']
    with pytest.raises(ValueError):
        services.cancel_assignment(rid, _FakeRequest())


def test_cancel_assignment_missing_returns_none(missed_lesson_fixture):
    assert services.cancel_assignment(999_999_999, _FakeRequest()) is None


def test_reassign_after_cancel_is_allowed(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Регрессия: после отмены (→pending) повторное назначение того же ученика за
    тот же пропуск обязано работать (переводит ту же строку обратно в
    makeup_scheduled), а не падать на DuplicateAssignment/UNIQUE."""
    payload = {
        'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
        'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
        'scheduled_time': '15:00', 'duration_minutes': 45,
    }
    first = services.create_assignment(payload, _FakeRequest())
    first_rid = first['resolution_ids'][0]
    services.cancel_assignment(first_rid, _FakeRequest())
    assert repository.get_resolution_full(first_rid)['status'] == PENDING

    second = services.create_assignment(payload, _FakeRequest())
    assert second['created'] == 1
    assert repository.get_resolution_full(second['resolution_ids'][0])['status'] == MAKEUP_SCHEDULED
    # UNIQUE(missed_lesson, student) — та же строка, не дубликат.
    assert AbsenceResolution.objects.filter(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
    ).count() == 1


def test_record_consumes_from_extra_keeps_original_absent(
    group_fixture, teacher_fixture, other_teacher_fixture, missed_lesson_fixture,
    student_fixture, lessons_done, resolution_cleanup,
):
    """Новая модель компенсации: потребление идёт от САМОГО факта доп.урока
    (present=true), а ИСХОДНЫЙ пропущенный урок остаётся present=false навсегда.
    lessons_done всё равно двигается на вес исходного урока (60мин → 1)."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': other_teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    assert lessons_done(group_fixture, student_fixture) == Decimal('0')

    result = services.record(
        rid,
        teacher_id=other_teacher_fixture,
        present=True,
        record_url=None,
        submitted_by_token='acct:1',
        submit_date='2026-04-05',
        request=_FakeRequest(),
    )
    try:
        assert result['payment'] == 200
        assert result['penalty'] == 0

        fact = Lesson.objects.get(id=result['lesson_id'])
        assert fact.lesson_type == 'extra'
        assert fact.teacher_id == other_teacher_fixture
        # Факт доп.урока несёт длительность ИСХОДНОГО урока (60), не резолюции (45).
        assert fact.lesson_duration_minutes == 60
        assert Payroll.objects.get(lesson_id=fact.id).payment == 200

        assert repository.get_resolution_full(rid)['status'] == MAKEUP_DONE

        # Потребление — на самом факте доп.урока (present=true)…
        fact_att = LessonAttendance.objects.get(lesson_id=fact.id, student_id=student_fixture)
        assert fact_att.present is True
        # …а ИСХОДНЫЙ пропущенный урок остаётся present=false (не флипается).
        att = LessonAttendance.objects.get(lesson_id=missed_lesson_fixture, student_id=student_fixture)
        assert att.present is False
        # lessons_done двигается на вес исходного урока (60мин → 1 полный урок).
        assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
    finally:
        # Резолюцию (fact_lesson FK) сносим ДО факт-урока, иначе DB-level FK.
        _delete_resolutions(missed_lesson_fixture)
        _cleanup_fact(result['lesson_id'])


def test_record_rejects_wrong_teacher(
    teacher_fixture, other_teacher_fixture, missed_lesson_fixture, student_fixture,
    resolution_cleanup,
):
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    with pytest.raises(NotTeachersAssignment):
        services.record(
            rid, teacher_id=other_teacher_fixture, present=True,
            record_url=None, submitted_by_token='acct:2', submit_date='2026-04-05',
            request=_FakeRequest(),
        )


def test_record_rejects_absent_student(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """record(present=False) → AbsentStudentNotRecordable, ничего не пишется:
    неявку оформляют «Отменой», резолюция остаётся makeup_scheduled."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    with pytest.raises(AbsentStudentNotRecordable):
        services.record(
            rid, teacher_id=teacher_fixture, present=False,
            record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
            request=_FakeRequest(),
        )
    full = repository.get_resolution_full(rid)
    assert full['status'] == MAKEUP_SCHEDULED
    assert full['fact_lesson_id'] is None


def test_record_second_call_on_same_assignment_raises_value_error(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """
    Регрессия (code review, race condition): после успешного record() резолюция
    в статусе makeup_done — повторный вызов record() на том же resolution_id
    обязан упасть ValueError'ом. Проверяет именно авторитетную проверку статуса
    под select_for_update() внутри atomic() (repository.lock_for_record), а не
    только неблокирующую предпроверку до atomic() — иначе гонка двух
    параллельных record() создала бы два Lesson-факта + Payroll (задвоенная
    зарплата), а второй mark_makeup_done() молча перезаписал бы fact_lesson_id
    первого.
    """
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    result = services.record(
        rid, teacher_id=teacher_fixture, present=True,
        record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
        request=_FakeRequest(),
    )
    try:
        with pytest.raises(ValueError):
            services.record(
                rid, teacher_id=teacher_fixture, present=True,
                record_url=None, submitted_by_token='acct:2', submit_date='2026-04-05',
                request=_FakeRequest(),
            )
    finally:
        _delete_resolutions(missed_lesson_fixture)
        _cleanup_fact(result['lesson_id'])


def test_delete_fact_reverses_consumption(
    group_fixture, teacher_fixture, missed_lesson_fixture, student_fixture, lessons_done,
    resolution_cleanup,
):
    """Откат факта доп.урока симметрично списывает lessons_done обратно (на вес
    факта) и удаляет Lesson+Payroll. Исходный пропуск всё время present=false —
    новая модель компенсации, ретроактивной отметки исходного урока нет."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    result = services.record(
        rid, teacher_id=teacher_fixture, present=True,
        record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
        request=_FakeRequest(),
    )
    assert lessons_done(group_fixture, student_fixture) == Decimal('1.0')
    # Исходный пропущенный урок остался present=false (потребление — на факте).
    orig = LessonAttendance.objects.get(lesson_id=missed_lesson_fixture, student_id=student_fixture)
    assert orig.present is False

    ok = services.delete_fact(rid, _FakeRequest())
    assert ok is True
    assert lessons_done(group_fixture, student_fixture) == Decimal('0')
    assert not Lesson.objects.filter(id=result['lesson_id']).exists()
    assert not Payroll.objects.filter(lesson_id=result['lesson_id']).exists()
    # Исходный урок по-прежнему present=false.
    orig = LessonAttendance.objects.get(lesson_id=missed_lesson_fixture, student_id=student_fixture)
    assert orig.present is False

    full = repository.get_resolution_full(rid)
    assert full['status'] == PENDING
    assert full['fact_lesson_id'] is None


def test_delete_fact_second_call_raises_value_error(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """
    Регрессия (audit, race condition): после успешного delete_fact() резолюция
    вернулась в status=pending — повторный вызов delete_fact() на том же
    resolution_id обязан упасть ValueError'ом. Проверяет авторитетную проверку
    статуса под select_for_update() внутри atomic() (repository.lock_for_delete)
    — тот же паттерн, что record()/lock_for_record, иначе гонка двух
    параллельных delete_fact() привела бы ко второму Lesson.DoesNotExist вместо
    чистой ошибки.
    """
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    services.record(
        rid, teacher_id=teacher_fixture, present=True,
        record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
        request=_FakeRequest(),
    )
    ok = services.delete_fact(rid, _FakeRequest())
    assert ok is True
    with pytest.raises(ValueError):
        services.delete_fact(rid, _FakeRequest())


def test_autocreate_pending_for_lesson_idempotent(
    missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """autocreate_pending_for_lesson (вызывается из record_lesson) идемпотентен:
    повторный вызов не создаёт дубликат (ON CONFLICT DO NOTHING)."""
    services.autocreate_pending_for_lesson(missed_lesson_fixture, [student_fixture])
    services.autocreate_pending_for_lesson(missed_lesson_fixture, [student_fixture])

    rows = list(AbsenceResolution.objects.filter(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
    ))
    assert len(rows) == 1
    assert rows[0].status == PENDING


def test_enforce_membership_cancellation_deletes_pending_keeps_done(
    group_fixture, teacher_fixture, missed_lesson_fixture, student_fixture,
    extra_missed_lessons, resolution_cleanup,
):
    """Снятие членства без назначенных доп.уроков: pending удаляется, makeup_done
    сохраняется (там уже проведён факт-урок и деньги)."""
    # pending на основном пропуске (в group_fixture).
    repository.autocreate_pending(missed_lesson_fixture, [student_fixture])
    # makeup_done на первом доп.пропуске (в том же group_fixture).
    done_id = repository.create_scheduled_direct(
        missed_lesson_id=extra_missed_lessons[0], student_id=student_fixture,
        assigned_teacher_id=teacher_fixture, scheduled_date=datetime.date(2026, 4, 13),
        scheduled_time=datetime.time(15, 0), duration_minutes=45)
    repository.mark_makeup_done(done_id, fact_lesson_id=extra_missed_lessons[0])

    deleted = services.enforce_membership_cancellation(student_fixture, group_fixture)
    assert deleted == 1

    assert repository.get_resolution_full(done_id)['status'] == MAKEUP_DONE
    assert repository.lock_for_assign(missed_lesson_fixture, student_fixture) is None


def test_enforce_membership_cancellation_blocks_on_scheduled(
    group_fixture, teacher_fixture, missed_lesson_fixture, student_fixture,
    extra_missed_lessons, resolution_cleanup,
):
    """Назначенный (не проведённый) доп.урок в группе блокирует снятие членства:
    MembershipHasScheduledMakeups, pending при этом НЕ удаляется (весь гейт — до
    любых изменений)."""
    repository.autocreate_pending(missed_lesson_fixture, [student_fixture])
    sched_id = repository.create_scheduled_direct(
        missed_lesson_id=extra_missed_lessons[0], student_id=student_fixture,
        assigned_teacher_id=teacher_fixture, scheduled_date=datetime.date(2026, 4, 12),
        scheduled_time=datetime.time(15, 0), duration_minutes=45)

    with pytest.raises(MembershipHasScheduledMakeups):
        services.enforce_membership_cancellation(student_fixture, group_fixture)

    # Ничего не удалено: и pending, и makeup_scheduled на месте.
    assert repository.lock_for_assign(missed_lesson_fixture, student_fixture)['status'] == PENDING
    assert repository.get_resolution_full(sched_id)['status'] == MAKEUP_SCHEDULED


# ---------------------------------------------------------------------------
# Ручной доп.урок СВЕРХ курса (kind='extra') — назначить/провести/отменить/откат
# ---------------------------------------------------------------------------

def _delete_extra_resolutions(group_id):
    """Сносит extra-резолюции группы (group FK = PROTECT) + их факт-уроки ДО
    teardown group_fixture."""
    with connection.cursor() as cur:
        cur.execute('SELECT id, fact_lesson_id FROM absence_resolutions WHERE group_id = %s', [group_id])
        rows = cur.fetchall()
        fact_ids = [r[1] for r in rows if r[1] is not None]
        cur.execute('DELETE FROM absence_resolutions WHERE group_id = %s', [group_id])
        for fid in fact_ids:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [fid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [fid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [fid])


def test_create_extra_assignment_happy_path(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Ручной доп.урок сверх курса: kind='extra', сразу makeup_scheduled, группа и
    «за какой урок» сохранены, пропуск (missed_lesson) не привязан."""
    try:
        result = services.create_extra_assignment({
            'group_id': group_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
            'scheduled_time': '16:00', 'duration_minutes': 60,
            'lesson_number': Decimal('17'),
        }, _FakeRequest())
        assert result['created'] == 1
        res = AbsenceResolution.objects.get(id=result['resolution_ids'][0])
        assert res.kind == 'extra'
        assert res.status == MAKEUP_SCHEDULED
        assert res.missed_lesson_id is None
        assert res.group_id == group_fixture
        assert res.target_lesson_number == Decimal('17.0')
    finally:
        _delete_extra_resolutions(group_fixture)


def test_create_extra_assignment_rejects_non_member(
    group_fixture, teacher_fixture, student_fixture,
):
    """Ученик НЕ состоит в группе → StudentNotInGroup (нет membership_fixture)."""
    from apps.extra_lessons.exceptions import StudentNotInGroup
    with pytest.raises(StudentNotInGroup):
        services.create_extra_assignment({
            'group_id': group_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
            'scheduled_time': '16:00', 'duration_minutes': 60,
        }, _FakeRequest())


def test_record_extra_creates_fact_and_consumes_balance(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Проведение ручного доп.урока: extra-Lesson с выбранным номером, +1 к
    lessons_done, списание с баланса (8→7), флет-выплата 200₽, статус makeup_done."""
    from apps.finances.repository import balance_for_student
    from apps.memberships.models import GroupMembership

    result = services.create_extra_assignment({
        'group_id': group_fixture, 'teacher_id': teacher_fixture,
        'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
        'scheduled_time': '16:00', 'duration_minutes': 60, 'lesson_number': Decimal('17'),
    }, _FakeRequest())
    rid = result['resolution_ids'][0]
    try:
        assert float(balance_for_student(student_fixture)) == 8.0
        rec = services.record(
            rid, teacher_id=teacher_fixture, present=True, record_url=None,
            submitted_by_token='acct:1', submit_date='2026-04-25', request=_FakeRequest())
        assert rec['payment'] == 200
        res = AbsenceResolution.objects.get(id=rid)
        assert res.status == MAKEUP_DONE
        fact = Lesson.objects.get(id=res.fact_lesson_id)
        assert fact.lesson_type == 'extra'
        assert fact.group_id == group_fixture
        assert Decimal(str(fact.lesson_number)) == Decimal('17.0')
        # прогресс +1, баланс списан
        md = GroupMembership.objects.get(group_id=group_fixture, student_id=student_fixture)
        assert md.lessons_done == Decimal('1.0')
        assert float(balance_for_student(student_fixture)) == 7.0
    finally:
        _delete_extra_resolutions(group_fixture)


def test_cancel_scheduled_extra_deletes_resolution(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Отмена назначенного extra (нет pending-пропуска) → удаление резолюции."""
    try:
        result = services.create_extra_assignment({
            'group_id': group_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
            'scheduled_time': '16:00', 'duration_minutes': 60,
        }, _FakeRequest())
        rid = result['resolution_ids'][0]
        out = services.cancel_assignment(rid, _FakeRequest())
        assert out == {'id': rid, 'deleted': True}
        assert not AbsenceResolution.objects.filter(id=rid).exists()
    finally:
        _delete_extra_resolutions(group_fixture)


def test_delete_fact_of_extra_rolls_back_and_removes_resolution(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Откат проведённого extra: удаляет факт+payroll, откатывает lessons_done (1→0)
    и удаляет саму резолюцию (у extra нет pending для возврата)."""
    from apps.memberships.models import GroupMembership

    result = services.create_extra_assignment({
        'group_id': group_fixture, 'teacher_id': teacher_fixture,
        'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
        'scheduled_time': '16:00', 'duration_minutes': 60, 'lesson_number': Decimal('17'),
    }, _FakeRequest())
    rid = result['resolution_ids'][0]
    try:
        services.record(rid, teacher_id=teacher_fixture, present=True, record_url=None,
                        submitted_by_token='acct:1', submit_date='2026-04-25', request=_FakeRequest())
        assert services.delete_fact(rid, _FakeRequest()) is True
        assert not AbsenceResolution.objects.filter(id=rid).exists()
        md = GroupMembership.objects.get(group_id=group_fixture, student_id=student_fixture)
        assert md.lessons_done == Decimal('0.0')
    finally:
        _delete_extra_resolutions(group_fixture)


def test_scheduled_extra_blocks_membership_cancellation(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Назначенный (не проведённый) extra блокирует снятие членства ученика в группе."""
    try:
        services.create_extra_assignment({
            'group_id': group_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
            'scheduled_time': '16:00', 'duration_minutes': 60,
        }, _FakeRequest())
        with pytest.raises(MembershipHasScheduledMakeups):
            services.enforce_membership_cancellation(student_fixture, group_fixture)
    finally:
        _delete_extra_resolutions(group_fixture)


# ---------------------------------------------------------------------------
# Ручной доп.урок с привязкой к РЕАЛЬНОМУ уроку (роутинг → makeup) + гард present
# ---------------------------------------------------------------------------

def test_manual_assign_by_real_lesson_routes_to_makeup_and_reuses_pending(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
    missed_lesson_fixture, resolution_cleanup,
):
    """Ручное назначение с номером уже проведённого урока (missed_lesson_fixture —
    урок №1, ученик отсутствовал → авто-заявка pending уже создана) → идёт как
    MAKEUP, привязанный к этому уроку, и ПЕРЕИСПОЛЬЗУЕТ авто-заявку (без дубля)."""
    result = services.create_extra_assignment({
        'group_id': group_fixture, 'teacher_id': teacher_fixture,
        'student_ids': [student_fixture], 'scheduled_date': '2026-05-01',
        'scheduled_time': '16:00', 'duration_minutes': 60, 'lesson_number': Decimal('1'),
    }, _FakeRequest())
    assert result['kind'] == 'makeup'
    assert result['created'] == 1
    # ровно одна резолюция на (пропуск × ученик) — авто-pending переиспользован
    qs = AbsenceResolution.objects.filter(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture)
    assert qs.count() == 1
    res = qs.get()
    assert res.kind == 'makeup'
    assert res.status == MAKEUP_SCHEDULED
    assert res.missed_lesson_id == missed_lesson_fixture
    assert res.assigned_teacher_id == teacher_fixture


def test_manual_assign_by_real_lesson_blocks_if_student_was_present(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Ручное назначение «за урок №N», где ученик реально ПРИСУТСТВОВАЛ (present=true)
    → StudentWasPresent (доп.урок за посещённый урок нельзя)."""
    from apps.extra_lessons.exceptions import StudentWasPresent
    from apps.lessons import services as lessons_services

    res = lessons_services.create_lesson_full({
        'lesson_date': '2026-05-02', 'group_id': group_fixture, 'teacher_id': teacher_fixture,
        'lesson_number': 2, 'lesson_duration_minutes': 60,
        'attendance': [{'student_id': student_fixture, 'present': True}],
    })
    lesson_id = res['lesson_id']
    try:
        with pytest.raises(StudentWasPresent):
            services.create_extra_assignment({
                'group_id': group_fixture, 'teacher_id': teacher_fixture,
                'student_ids': [student_fixture], 'scheduled_date': '2026-05-03',
                'scheduled_time': '16:00', 'duration_minutes': 60, 'lesson_number': Decimal('2'),
            }, _FakeRequest())
    finally:
        _cleanup_fact(lesson_id)


def test_manual_assign_unknown_number_routes_to_extra(
    group_fixture, teacher_fixture, student_fixture, membership_fixture,
):
    """Номер урока, которого в группе ещё не было (проведённого урока №99 нет) →
    extra сверх курса (не привязан к уроку)."""
    try:
        result = services.create_extra_assignment({
            'group_id': group_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-05-04',
            'scheduled_time': '16:00', 'duration_minutes': 60, 'lesson_number': Decimal('99'),
        }, _FakeRequest())
        assert result['kind'] == 'extra'
        res = AbsenceResolution.objects.get(id=result['resolution_ids'][0])
        assert res.kind == 'extra'
        assert res.missed_lesson_id is None
        assert res.target_lesson_number == Decimal('99.0')
    finally:
        _delete_extra_resolutions(group_fixture)


# ---------------------------------------------------------------------------
# Оплата доп.урока СВЕРХ курса для ИНДИВ-группы: как обычный индив-урок
# (45 мин → 250, полный → 500). Групповой extra и makeup остаются флет-200.
# ---------------------------------------------------------------------------

@pytest.fixture
def individual_group_fixture(direction_fixture, teacher_fixture):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__el_test_indiv_group__', %s, %s, true, 45, true, 0) RETURNING id
            """,
            [direction_fixture, teacher_fixture],
        )
        gid = cur.fetchone()[0]
    yield gid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.fixture
def individual_membership_fixture(individual_group_fixture, student_fixture, direction_fixture):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO group_memberships (group_id, student_id, lessons_done, active)
            VALUES (%s, %s, 0, true) RETURNING id
            """,
            [individual_group_fixture, student_fixture],
        )
        mid = cur.fetchone()[0]
        cur.execute(
            """
            INSERT INTO payments (student_id, direction_id, subscriptions_count, lessons_count,
                                   unit_price, total_amount, paid_at, created_by)
            VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test') RETURNING id
            """,
            [student_fixture, direction_fixture],
        )
        pid = cur.fetchone()[0]
    yield mid
    with connection.cursor() as cur:
        cur.execute('DELETE FROM group_memberships WHERE id = %s', [mid])
        cur.execute('DELETE FROM payments WHERE id = %s', [pid])


@pytest.mark.parametrize('duration, expected', [(45, 250), (90, 500), (60, 500)])
def test_record_extra_individual_pays_by_duration(
    individual_group_fixture, teacher_fixture, student_fixture,
    individual_membership_fixture, duration, expected,
):
    """Доп.урок СВЕРХ курса для ИНДИВ-группы оплачивается как обычный индив-урок:
    45 мин → 250, полный (60/90) → 500. Групповой extra и makeup — флет-200."""
    result = services.create_extra_assignment({
        'group_id': individual_group_fixture, 'teacher_id': teacher_fixture,
        'student_ids': [student_fixture], 'scheduled_date': '2026-04-25',
        'scheduled_time': '16:00', 'duration_minutes': duration, 'lesson_number': Decimal('17'),
    }, _FakeRequest())
    rid = result['resolution_ids'][0]
    try:
        rec = services.record(
            rid, teacher_id=teacher_fixture, present=True, record_url=None,
            submitted_by_token='acct:1', submit_date='2026-04-25', request=_FakeRequest())
        assert rec['payment'] == expected
        assert Payroll.objects.get(lesson_id=rec['lesson_id']).payment == expected
    finally:
        _delete_extra_resolutions(individual_group_fixture)


# --- delete_resolution: удаление заявки «Ждёт решения» ----------------------

def test_delete_resolution_removes_pending_row(
    missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """pending («Ждёт решения») удаляется из БД целиком: за заявкой нет ни
    факта-урока, ни зарплаты, ни списания с баланса — откатывать нечего."""
    rid = AbsenceResolution.objects.get(
        missed_lesson_id=missed_lesson_fixture, student_id=student_fixture,
        status=PENDING).id

    assert services.delete_resolution(rid, _FakeRequest()) is True
    assert not AbsenceResolution.objects.filter(id=rid).exists()
    # Сам пропущенный урок и его посещаемость не тронуты — удалялась заявка,
    # а не факт отсутствия.
    assert Lesson.objects.filter(id=missed_lesson_fixture).exists()
    assert LessonAttendance.objects.filter(
        lesson_id=missed_lesson_fixture, student_id=student_fixture).exists()


def test_delete_resolution_missing_returns_false():
    assert services.delete_resolution(999_999_999, _FakeRequest()) is False


def test_delete_resolution_rejects_scheduled(
    teacher_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Назначенный доп.урок удалению не подлежит: за ним преподаватель и
    забронированное время — снимают его «Отменой» (view → 409)."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    with pytest.raises(ValueError):
        services.delete_resolution(rid, _FakeRequest())
    assert AbsenceResolution.objects.filter(id=rid).exists()


def test_delete_resolution_of_done_falls_back_to_rollback(
    group_fixture, teacher_fixture, missed_lesson_fixture, student_fixture, lessons_done,
    resolution_cleanup,
):
    """Для проведённого доп.урока DELETE остаётся откатом факта (delete_fact):
    строка не удаляется, а возвращается в pending, урок — на баланс."""
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
            'student_ids': [student_fixture], 'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00', 'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    rid = created['resolution_ids'][0]
    result = services.record(
        rid, teacher_id=teacher_fixture, present=True,
        record_url=None, submitted_by_token='acct:1', submit_date='2026-04-05',
        request=_FakeRequest(),
    )

    assert services.delete_resolution(rid, _FakeRequest()) is True
    assert lessons_done(group_fixture, student_fixture) == Decimal('0')
    assert not Lesson.objects.filter(id=result['lesson_id']).exists()
    assert repository.get_resolution_full(rid)['status'] == PENDING


def test_record_two_makeups_same_group_date_and_teacher(
    teacher_fixture, group_fixture, direction_fixture, missed_lesson_fixture,
    student_fixture, resolution_cleanup,
):
    """Регрессия (прод-500 от 2026-08-25): два ученика ОДНОЙ группы пропустили
    ОДИН и тот же урок, доп.уроки обоим назначены на одну дату и проводит их
    один преподаватель. Факт makeup наследует группу и lesson_number
    пропущенного урока, поэтому у обоих фактов совпадают
    (lesson_date, group_id, lesson_number) — если токен ещё и общий
    (`acct:<id>` отправителя), второй INSERT падал на lessons_natural_key
    (IntegrityError → 500). Токен факта обязан уникализироваться резолюцией,
    как это уже сделано для extra/burned."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO students (full_name) VALUES ('__el_test_student_2__') RETURNING id")
        student2 = cur.fetchone()[0]
        cur.execute(
            'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
            'VALUES (%s, %s, 0, true)', [group_fixture, student2])
        cur.execute(
            'INSERT INTO payments (student_id, direction_id, subscriptions_count, '
            'lessons_count, unit_price, total_amount, paid_at, created_by) '
            "VALUES (%s, %s, 2, 8, 1000, 8000, '2026-06-01', 'test')",
            [student2, direction_fixture])
        # Второй ученик тоже отсутствовал на том же уроке.
        cur.execute(
            'INSERT INTO lesson_attendance (lesson_id, student_id, present) '
            'VALUES (%s, %s, false)', [missed_lesson_fixture, student2])

    fact_ids = []
    try:
        created = services.create_assignment(
            {
                'missed_lesson_id': missed_lesson_fixture, 'teacher_id': teacher_fixture,
                'student_ids': [student_fixture, student2],
                'scheduled_date': '2026-04-05', 'scheduled_time': '15:00',
                'duration_minutes': 45,
            },
            _FakeRequest(),
        )
        assert created['created'] == 2

        for rid in created['resolution_ids']:
            result = services.record(
                rid, teacher_id=teacher_fixture, present=True, record_url=None,
                submitted_by_token='acct:36', submit_date='2026-04-05',
                request=_FakeRequest(),
            )
            fact_ids.append(result['lesson_id'])

        assert len(set(fact_ids)) == 2
        facts = Lesson.objects.filter(id__in=fact_ids)
        assert {f.lesson_type for f in facts} == {'extra'}
        # Оба факта — в один день, одной группы, с одним номером: развести их
        # может только токен.
        assert len({f.submitted_by_token for f in facts}) == 2
    finally:
        _delete_resolutions(missed_lesson_fixture)
        for lid in fact_ids:
            _cleanup_fact(lid)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                        [missed_lesson_fixture, student2])
            cur.execute('DELETE FROM group_memberships WHERE student_id = %s', [student2])
            cur.execute('DELETE FROM payments WHERE student_id = %s', [student2])
            cur.execute('DELETE FROM students WHERE id = %s', [student2])
