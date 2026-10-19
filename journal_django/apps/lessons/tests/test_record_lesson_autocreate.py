"""record_lesson обычного урока авто-создаёт pending-резолюции по отсутствовавшим
(apps.extra_lessons.AbsenceResolution). extra/burned уроки — не порождают."""
from __future__ import annotations

import pytest
from django.db import connection

from apps.lessons import services
from apps.lessons.exceptions import (
    AttendanceCompensatedElsewhere, AttendanceHasScheduledMakeup, LessonHasMakeupResolutions,
)

pytestmark = pytest.mark.django_db


def _pending_students(missed_lesson_id):
    with connection.cursor() as cur:
        cur.execute(
            "SELECT student_id FROM absence_resolutions "
            "WHERE missed_lesson_id=%s AND status='pending' ORDER BY student_id",
            [missed_lesson_id])
        return [r[0] for r in cur.fetchall()]


def _cleanup(lesson_id):
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE missed_lesson_id=%s', [lesson_id])
        cur.execute('DELETE FROM payroll WHERE lesson_id=%s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id=%s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id=%s', [lesson_id])


def test_regular_lesson_autocreates_pending_for_absent(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    res = services.record_lesson(
        lesson_date='2026-05-01', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-01',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == [student_fixture]
        # Идемпотентность: повторный autocreate по тому же уроку не дублирует.
        from apps.extra_lessons import services as el
        el.autocreate_pending_for_lesson(lesson_id, [student_fixture])
        assert _pending_students(lesson_id) == [student_fixture]
    finally:
        _cleanup(lesson_id)


def test_present_student_gets_no_pending(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    res = services.record_lesson(
        lesson_date='2026-05-03', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-03',
        attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == []
    finally:
        _cleanup(lesson_id)


def test_delete_lesson_blocked_when_makeup_done(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Обычный урок с проведённым доп.уроком по его пропуску (makeup_done) удалить
    нельзя (409/LessonHasMakeupResolutions) — иначе ON DELETE CASCADE осиротил бы
    факт доп.урока + payroll. pending — удалять каскадом можно."""
    res = services.record_lesson(
        lesson_date='2026-05-04', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-04',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        # Симулируем проведённый доп.урок: pending → makeup_done.
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status='makeup_done' "
                "WHERE missed_lesson_id=%s", [lesson_id])
        with pytest.raises(LessonHasMakeupResolutions):
            services.delete_lesson_full(lesson_id)

        # Вернём в pending — тогда удаление проходит (каскад снесёт резолюцию).
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status='pending' WHERE missed_lesson_id=%s",
                [lesson_id])
        assert services.delete_lesson_full(lesson_id) is True
        assert _pending_students(lesson_id) == []  # каскад снёс резолюцию
    finally:
        _cleanup(lesson_id)


@pytest.mark.parametrize('status', ['makeup_done', 'burned'])
def test_attendance_toggle_to_present_blocked_when_compensated(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, status,
):
    """Critical-гард: нельзя вручную флипнуть исходную ячейку пропуска в present=true,
    если пропуск уже компенсирован (makeup_done) или сожжён (burned) — иначе урок
    спишется дважды. Снятие present (в absent) — разрешено."""
    res = services.record_lesson(
        lesson_date='2026-05-06', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-06',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status=%s WHERE missed_lesson_id=%s",
                [status, lesson_id])
        # Флип в present — заблокирован.
        with pytest.raises(AttendanceCompensatedElsewhere):
            services.update_attendance_cell(lesson_id, student_fixture, True)
        # Ячейка не изменилась.
        with connection.cursor() as cur:
            cur.execute(
                'SELECT present FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                [lesson_id, student_fixture])
            assert cur.fetchone()[0] is False
        # Снятие present (в absent) — не гейтится (не создаёт двойного учёта).
        assert services.update_attendance_cell(lesson_id, student_fixture, False) is True
    finally:
        _cleanup(lesson_id)


def test_delete_lesson_blocked_when_burned(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Обычный урок со сгоревшим пропуском (burned) удалить нельзя
    (409/LessonHasMakeupResolutions) — иначе ON DELETE CASCADE осиротил бы
    burned-факт + payroll. pending — удалять каскадом можно."""
    res = services.record_lesson(
        lesson_date='2026-05-05', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-05',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        # Симулируем сгорание: pending → burned.
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status='burned' "
                "WHERE missed_lesson_id=%s", [lesson_id])
        with pytest.raises(LessonHasMakeupResolutions):
            services.delete_lesson_full(lesson_id)

        # Вернём в pending — тогда удаление проходит (каскад снесёт резолюцию).
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status='pending' WHERE missed_lesson_id=%s",
                [lesson_id])
        assert services.delete_lesson_full(lesson_id) is True
        assert _pending_students(lesson_id) == []
    finally:
        _cleanup(lesson_id)


def test_extra_lesson_does_not_autocreate(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    res = services.record_lesson(
        lesson_date='2026-05-02', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='extra', record_url=None, submitted_by_token='t',
        submit_date='2026-05-02',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == []  # extra не порождает pending
    finally:
        _cleanup(lesson_id)


@pytest.mark.parametrize('lesson_type', ['substitution', 'reschedule'])
def test_course_lesson_types_autocreate_pending(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lesson_type,
):
    """Замена и перенос — такие же занятия курса, как regular: пропуск на них
    обязан вставать в очередь «ждёт решения».

    Регрессия: гейт был `lesson_type == 'regular'`, из-за чего на всех замещённых
    и перенесённых занятиях заявки не создавались вовсе. Тип выводит сервер
    (apps.teacher_spa.services), преподаватель его не выбирает, поэтому потеря
    была не видна ни в кабинете, ни в логах."""
    res = services.record_lesson(
        lesson_date='2026-05-07', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type=lesson_type, record_url=None, submitted_by_token='t',
        submit_date='2026-05-07',
        attendance=[{'student_id': student_fixture, 'present': False}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == [student_fixture]
    finally:
        _cleanup(lesson_id)


@pytest.mark.parametrize('lesson_type', ['regular', 'substitution', 'reschedule'])
def test_unpaid_skip_never_autocreates_on_any_course_type(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, lesson_type,
):
    """«Неоплачиваемый пропуск» — терминальный прощённый исход (перевод / начал не
    с 1-го урока): отработка не требуется, заявка НЕ создаётся. Расширение гейта на
    substitution/reschedule не должно этого менять — проверяем на всех трёх типах.

    Пометка ставится слот-маркером (LessonSkip, «Вариант A») — это единственный
    путь, которым unpaid_skip попадает в record_lesson: AttendanceItemSerializer
    такого поля не имеет, клиент его прислать не может. record_lesson форсит
    помеченного ученика в present=false/unpaid_skip=true, что бы ни пришло с фронта."""
    services.set_lesson_skip(group_fixture, student_fixture, 1, True)
    res = services.record_lesson(
        lesson_date='2026-05-08', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type=lesson_type, record_url=None, submitted_by_token='t',
        submit_date='2026-05-08',
        # Приходит «пришёл» — маркер обязан переписать исход в неоплачиваемый пропуск.
        attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        with connection.cursor() as cur:
            cur.execute(
                'SELECT present, unpaid_skip FROM lesson_attendance '
                'WHERE lesson_id=%s AND student_id=%s', [lesson_id, student_fixture])
            assert cur.fetchone() == (False, True)
        assert _pending_students(lesson_id) == []
    finally:
        services.set_lesson_skip(group_fixture, student_fixture, 1, False)
        _cleanup(lesson_id)


def test_flip_to_absent_autocreates_pending(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Ретроактивное снятие present (админ правит ячейку задним числом) ставит
    пропуск в очередь. Прежде pending создавался ТОЛЬКО в момент записи урока,
    поэтому пропуск, проставленный позже, не попадал в «Доп.уроки» никогда."""
    res = services.record_lesson(
        lesson_date='2026-05-09', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-09',
        attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == []          # пришёл — заявки нет
        services.update_attendance_cell(lesson_id, student_fixture, False)
        assert _pending_students(lesson_id) == [student_fixture]
        # Идемпотентность: повторное снятие не плодит дублей.
        services.update_attendance_cell(lesson_id, student_fixture, False)
        assert _pending_students(lesson_id) == [student_fixture]
    finally:
        _cleanup(lesson_id)


def test_flip_to_absent_does_not_autocreate_for_unpaid_skip_cell(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Ячейка, помеченная «неоплачиваемым пропуском», заявку не порождает и при
    ретро-правке: исход терминальный, отработка не требуется."""
    res = services.record_lesson(
        lesson_date='2026-05-10', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-10',
        attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        services.set_unpaid_skip(lesson_id, student_fixture, True)
        assert _pending_students(lesson_id) == []
        # Явное снятие present поверх пометки — заявки по-прежнему быть не должно.
        services.update_attendance_cell(lesson_id, student_fixture, False)
        assert _pending_students(lesson_id) == []
    finally:
        _cleanup(lesson_id)


# ---------------------------------------------------------------------------
# Усыновление заранее назначенного доп.урока «сверх курса».
#
# Менеджер назначает отработку за урок №N ДО того, как урок состоялся (ученик
# предупредил о пропуске заранее). Реального урока №N ещё нет, поэтому раздел
# заводит kind='extra' с missed_lesson=NULL. Когда урок проводится и ученика на
# нём не оказывается, по тому же пропуску завелась бы ВТОРАЯ запись — pending, —
# а закрыть её можно было бы только вторым списанием. Проверяем, что вместо
# этого назначенный доп.урок привязывается к пропуску.
# ---------------------------------------------------------------------------
import datetime


def _make_extra(group_id, student_id, teacher_id, target_number,
                status=None, scheduled_date='2026-08-26'):
    """Доп.урок сверх курса через штатный путь раздела (create_extra_direct — то
    же, чем пользуется ручное назначение). status → принудительно выставить:
    нужен makeup_done, доп.урок могли провести раньше пропущенного занятия."""
    from apps.extra_lessons import repository as el_repo
    rid = el_repo.create_extra_direct(
        group_id=group_id, student_id=student_id, assigned_teacher_id=teacher_id,
        scheduled_date=datetime.date.fromisoformat(scheduled_date),
        scheduled_time=datetime.time(15, 0), duration_minutes=60,
        target_lesson_number=target_number)
    if status is not None:
        with connection.cursor() as cur:
            cur.execute('UPDATE absence_resolutions SET status=%s WHERE id=%s', [status, rid])
    return rid


def _resolution(rid):
    with connection.cursor() as cur:
        cur.execute(
            'SELECT kind, status, missed_lesson_id, group_id, target_lesson_number, '
            'assigned_teacher_id, scheduled_date, duration_minutes '
            'FROM absence_resolutions WHERE id=%s', [rid])
        row = cur.fetchone()
    if row is None:
        return None
    return {
        'kind': row[0], 'status': row[1], 'missed_lesson_id': row[2], 'group_id': row[3],
        'target_lesson_number': row[4], 'assigned_teacher_id': row[5],
        'scheduled_date': row[6], 'duration_minutes': row[7],
    }


def _drop_resolutions(*ids):
    """Неусыновлённый extra не привязан к уроку, поэтому _cleanup(lesson_id) его
    не заберёт — общая eduson_test требует убирать за собой явно."""
    with connection.cursor() as cur:
        cur.execute('DELETE FROM absence_resolutions WHERE id = ANY(%s)', [list(ids)])


def _count_for_lesson(lesson_id):
    with connection.cursor() as cur:
        cur.execute('SELECT COUNT(*) FROM absence_resolutions WHERE missed_lesson_id=%s',
                    [lesson_id])
        return cur.fetchone()[0]


def _record(group_id, teacher_id, student_id, date, attendance=None, number=1):
    return services.record_lesson(
        lesson_date=date, teacher_id=teacher_id, group_id=group_id,
        original_teacher_id=None, lesson_number=number, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date=date,
        attendance=attendance or [{'student_id': student_id, 'present': False}])


def test_scheduled_extra_adopted_instead_of_second_pending(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Главный случай: доп.урок назначен заранее за урок №1, урок проводится,
    ученика нет. Второй записи по этому пропуску быть не должно — назначение
    само становится отработкой ЭТОГО урока."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-10')
    lesson_id = res['lesson_id']
    try:
        # Никакого нового pending: по пропуску ровно одна запись — та самая,
        # которую менеджер назначил заранее.
        assert _pending_students(lesson_id) == []
        assert _count_for_lesson(lesson_id) == 1

        r = _resolution(rid)
        assert r['kind'] == 'makeup'               # стал обычной отработкой
        assert r['missed_lesson_id'] == lesson_id  # привязан к пропуску
        assert r['group_id'] is None               # у makeup группа — из пропуска
        assert r['target_lesson_number'] is None   # номер — тоже из пропуска
        # Назначение не переигрывается: статус, преподаватель и дата те же,
        # преподавателю сообщать не о чем.
        assert r['status'] == 'makeup_scheduled'
        assert r['assigned_teacher_id'] == teacher_id_fixture
        assert r['scheduled_date'] == datetime.date(2026, 8, 26)
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_conducted_extra_adopted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Доп.урок могли ПРОВЕСТИ раньше пропущенного занятия. Тогда усыновляем уже
    закрытую запись: списание остаётся одно, а пропуск сразу помечен
    компенсированным (иначе появился бы pending и второе списание)."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1,
                      status='makeup_done')
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-11')
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == []
        r = _resolution(rid)
        assert r['kind'] == 'makeup'
        assert r['missed_lesson_id'] == lesson_id
        assert r['status'] == 'makeup_done'   # статус не сбрасывается
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_extra_for_other_number_not_adopted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Доп.урок назначен за ДРУГОЙ урок — к этому пропуску отношения не имеет:
    остаётся сверх курса, а пропуск встаёт в очередь как обычно."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 2)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-12')
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == [student_fixture]
        r = _resolution(rid)
        assert r['kind'] == 'extra'
        assert r['missed_lesson_id'] is None
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_extra_without_target_number_not_adopted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Доп.урок без указания «за какой урок» — свободное занятие сверх курса,
    привязывать его к первому попавшемуся пропуску нельзя."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, None)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-13')
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == [student_fixture]
        assert _resolution(rid)['kind'] == 'extra'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_extra_of_other_group_not_adopted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
    direction_fixture,
):
    """Номер урока совпал, но доп.урок назначен в ДРУГОЙ группе — усыновлять
    нельзя: нумерация уроков сквозная внутри группы, а не по школе."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__les_test_group2__', %s, %s, false, 60, true, 0) RETURNING id
            """, [direction_fixture, teacher_id_fixture])
        other_group = cur.fetchone()[0]
    rid = _make_extra(other_group, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-14')
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == [student_fixture]
        assert _resolution(rid)['kind'] == 'extra'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM groups WHERE id=%s', [other_group])


def test_extra_not_adopted_for_unpaid_skip(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """«Неоплачиваемый пропуск» — терминальный прощённый исход: отработки не
    требует, поэтому и назначенный доп.урок к нему не привязывается."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-16',
                  attendance=[{'student_id': student_fixture, 'present': False,
                               'unpaid_skip': True}])
    lesson_id = res['lesson_id']
    try:
        assert _pending_students(lesson_id) == []
        assert _resolution(rid)['kind'] == 'extra'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_only_one_duplicate_extra_adopted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Два доп.урока за один и тот же номер раздел создать позволяет (UNIQUE их
    не ловит: missed_lesson=NULL, а NULL в Postgres не конфликтуют). Усыновить
    можно только ОДИН — второй нарушил бы UNIQUE(missed_lesson, student)."""
    first = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    second = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-17')
    lesson_id = res['lesson_id']
    try:
        assert sorted([_resolution(first)['kind'], _resolution(second)['kind']]) == \
            ['extra', 'makeup']
        # Усыновлён самый ранний по id — детерминированно, не «какой попадётся».
        assert _resolution(first)['missed_lesson_id'] == lesson_id
        assert _resolution(second)['missed_lesson_id'] is None
        assert _pending_students(lesson_id) == []
        assert _count_for_lesson(lesson_id) == 1
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(first, second)


def test_retro_absence_adopts_extra(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Ретро-правка: урок записали с «был», позже отметку сняли. Пропуск встаёт
    в очередь тем же путём (_autocreate_pending_for_cell), значит и усыновление
    обязано отработать — иначе ретро-снятие плодило бы вторую запись.

    Назначение заводим ПОСЛЕ записи урока: запись с «был» теперь сама снимает
    назначенный за этот урок доп.урок (drop_extra_for_present_students), поэтому
    созданное заранее до проверки бы не дожило."""
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-18',
                  attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    try:
        services.update_attendance_cell(lesson_id, student_fixture, False)
        r = _resolution(rid)
        assert r['kind'] == 'makeup'
        assert r['missed_lesson_id'] == lesson_id
        assert _pending_students(lesson_id) == []
        assert _count_for_lesson(lesson_id) == 1
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_adoption_idempotent(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Повторный проход по тому же уроку (ретро-флипы туда-обратно) не плодит
    записей и не сбрасывает уже усыновлённое назначение обратно в pending."""
    from apps.extra_lessons import services as el
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-19')
    lesson_id = res['lesson_id']
    try:
        el.autocreate_pending_for_lesson(lesson_id, [student_fixture])
        el.autocreate_pending_for_lesson(lesson_id, [student_fixture])
        assert _count_for_lesson(lesson_id) == 1
        assert _resolution(rid)['status'] == 'makeup_scheduled'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


# ---------------------------------------------------------------------------
# Снятие заранее назначенного доп.урока, когда ученик всё-таки пришёл.
#
# Основание доп.урока — ожидаемый пропуск. Ученик на занятии, значит основания
# нет: назначение снимается, иначе превратится в лишнее занятие сверх курса,
# которое ученик оплатит. Проведённые доп.уроки не трогаются — за ними стоит
# факт-урок и зарплата.
# ---------------------------------------------------------------------------


def test_present_student_extra_deleted(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Ученик пришёл на урок, за который заранее назначен доп.урок → назначение
    снято."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-20',
                  attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        assert _resolution(rid) is None
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_extra_for_other_number_survives_when_present(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Доп.урок за ДРУГОЙ урок к этому занятию отношения не имеет — не трогаем."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 2)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-21',
                  attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        r = _resolution(rid)
        assert r is not None and r['kind'] == 'extra'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_extra_of_other_group_survives_when_present(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
    direction_fixture,
):
    """Совпал только номер, группа другая — не трогаем."""
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO groups (name, direction_id, teacher_id, is_individual,
                                lesson_duration_minutes, active, lesson_number_offset)
            VALUES ('__les_test_group3__', %s, %s, false, 60, true, 0) RETURNING id
            """, [direction_fixture, teacher_id_fixture])
        other_group = cur.fetchone()[0]
    rid = _make_extra(other_group, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-22',
                  attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        assert _resolution(rid) is not None
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)
        with connection.cursor() as cur:
            cur.execute('DELETE FROM groups WHERE id=%s', [other_group])


def test_conducted_extra_survives_when_present(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """ПРОВЕДЁННЫЙ доп.урок не снимается никогда: за ним стоит факт-урок и
    Payroll, удаление резолюции осиротило бы их. Занятие состоялось сверх курса —
    ученик его посетил и оплатил, отменять задним числом нечего."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1,
                      status='makeup_done')
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-23',
                  attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    try:
        r = _resolution(rid)
        assert r is not None and r['status'] == 'makeup_done'
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_retro_present_deletes_extra(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Тот же исход при простановке «был» задним числом: правило одно на оба
    пути, кто именно отметил присутствие — значения не имеет."""
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-24')
    lesson_id = res['lesson_id']
    # Назначение заводим ПОСЛЕ записи урока напрямую (минуя роутинг раздела,
    # который при существующем уроке увёл бы его в makeup) — так в базе
    # воспроизводится состояние «extra за номер уже проведённого урока».
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    try:
        services.update_attendance_cell(lesson_id, student_fixture, True)
        assert _resolution(rid) is None
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_free_lesson_counts_as_present_for_drop(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """«Бесплатное занятие» — это present=true: ученик на уроке был, значит
    основание доп.урока исчезло ровно так же."""
    rid = _make_extra(group_fixture, student_fixture, teacher_id_fixture, 1)
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-25',
                  attendance=[{'student_id': student_fixture, 'present': True,
                               'is_free': True}])
    lesson_id = res['lesson_id']
    try:
        assert _resolution(rid) is None
    finally:
        _cleanup(lesson_id)
        _drop_resolutions(rid)


def test_lesson_full_reports_compensated_for_student_without_attendance_row(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Ученика добавили в группу ПОСЛЕ проведения урока: строки посещаемости у него
    нет, но пропуск закрыт доп.уроком (makeup_done).

    Карточки в редакторе урока рисуются по СОСТАВУ ГРУППЫ, а не по строкам
    посещаемости, поэтому пометка «компенсирован» обязана приезжать отдельным
    списком: раньше она вешалась только на существующие строки и для такого
    ученика молча терялась — фронт рисовал его красным и кликабельным, хотя
    пропуск отработан. Зеркалит apps/groups/tests/test_progress_api.py::
    test_compensated_without_attendance_row_shows_yellow (там это уже учтено).
    """
    res = services.record_lesson(
        lesson_date='2026-05-07', teacher_id=teacher_id_fixture, group_id=group_fixture,
        original_teacher_id=None, lesson_number=1, lesson_duration_minutes=60,
        lesson_type='regular', record_url=None, submitted_by_token='t',
        submit_date='2026-05-07',
        attendance=[{'student_id': student_fixture, 'present': True}])
    lesson_id = res['lesson_id']
    late_id = None
    try:
        with connection.cursor() as cur:
            cur.execute("INSERT INTO students (full_name) VALUES ('__late Гена__') RETURNING id")
            late_id = cur.fetchone()[0]
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s,%s,0,true)', [group_fixture, late_id])
            cur.execute(
                'INSERT INTO absence_resolutions (missed_lesson_id, student_id, status, created_at) '
                "VALUES (%s,%s,'makeup_done',now())", [lesson_id, late_id])

        full = services.get_lesson_full(lesson_id)

        # Строки посещаемости у него действительно нет — придумывать её не надо.
        assert late_id not in [a['student_id'] for a in full['attendance']]
        # Но пометка «компенсирован» до фронта доезжает.
        assert late_id in full['compensated_student_ids']
        # Ученик со строкой и без резолюции компенсированным не считается.
        assert student_fixture not in full['compensated_student_ids']
        # Деньги при этом не были под угрозой и до фикса: серверный гард ищет
        # резолюцию по паре (урок × ученик), строка посещаемости ему не нужна —
        # флип отклонялся и без неё. Баг был чисто в отображении.
        with pytest.raises(AttendanceCompensatedElsewhere):
            services.update_attendance_cell(lesson_id, late_id, True)
    finally:
        with connection.cursor() as cur:
            if late_id is not None:
                cur.execute('DELETE FROM absence_resolutions WHERE student_id=%s', [late_id])
                cur.execute('DELETE FROM group_memberships WHERE student_id=%s', [late_id])
                cur.execute('DELETE FROM students WHERE id=%s', [late_id])
        _cleanup(lesson_id)


@pytest.mark.parametrize('status', ['pending', 'waived'])
def test_retro_present_drops_undecided_resolution(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture, status,
):
    """Инцидент ВДГ18 (урок №32, 2026-09-19): ячейку исправили на «был», а
    заявка на пропуск осталась в «Доп.уроках» — её потом сожгли, урок учёлся
    дважды. Пропуска больше нет → заявка без факта и денег (pending/waived)
    удаляется в той же транзакции, что и флип."""
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-26')
    lesson_id = res['lesson_id']
    try:
        with connection.cursor() as cur:
            cur.execute('UPDATE absence_resolutions SET status=%s WHERE missed_lesson_id=%s',
                        [status, lesson_id])
        assert services.update_attendance_cell(lesson_id, student_fixture, True) is True
        assert _count_for_lesson(lesson_id) == 0
    finally:
        _cleanup(lesson_id)


def test_retro_present_blocked_when_makeup_scheduled(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """За назначенным доп.уроком стоят преподаватель и время — снимать его молча
    нельзя. Отметка отклоняется, ячейка и назначение не меняются."""
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-27')
    lesson_id = res['lesson_id']
    try:
        with connection.cursor() as cur:
            cur.execute(
                "UPDATE absence_resolutions SET status='makeup_scheduled', "
                "assigned_teacher_id=%s, scheduled_date='2026-05-30', duration_minutes=60 "
                'WHERE missed_lesson_id=%s', [teacher_id_fixture, lesson_id])
        with pytest.raises(AttendanceHasScheduledMakeup):
            services.update_attendance_cell(lesson_id, student_fixture, True)
        with connection.cursor() as cur:
            cur.execute(
                'SELECT present FROM lesson_attendance WHERE lesson_id=%s AND student_id=%s',
                [lesson_id, student_fixture])
            assert cur.fetchone()[0] is False
            cur.execute('SELECT status FROM absence_resolutions WHERE missed_lesson_id=%s',
                        [lesson_id])
            assert cur.fetchone()[0] == 'makeup_scheduled'
    finally:
        _cleanup(lesson_id)


def test_retro_present_then_absent_recreates_pending(
    group_fixture, teacher_id_fixture, student_fixture, membership_fixture,
):
    """Туда-обратно: «был» удалил заявку, повторное «не был» заводит её заново —
    ровно одну."""
    res = _record(group_fixture, teacher_id_fixture, student_fixture, '2026-05-28')
    lesson_id = res['lesson_id']
    try:
        services.update_attendance_cell(lesson_id, student_fixture, True)
        assert _count_for_lesson(lesson_id) == 0
        services.update_attendance_cell(lesson_id, student_fixture, False)
        assert _pending_students(lesson_id) == [student_fixture]
    finally:
        _cleanup(lesson_id)
