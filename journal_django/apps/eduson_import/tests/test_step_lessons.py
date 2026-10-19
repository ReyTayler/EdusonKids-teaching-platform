"""Шаг «уроки»: сессии Eduson → Lesson + LessonAttendance + PlannedLesson, отчёты
преподавателей → LessonFeedback (спека §5.1 с поправками §5.4).

Каждый сценарий собирается фабрикой теми же действиями, что делал Eduson (перенос,
правка карточки, смена преподавателя группы, удаление урока), — чтобы шаг проверялся
на формате боевой базы, а не на придуманном нами."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import SUBMITTED_BY, import_feedback, import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.tests.factory import EdusonDB
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group
from apps.lessons.models import Lesson, LessonAttendance
from apps.scheduling import health
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import CANCELLED, DONE, PENDING
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

AS_OF = dt.date(2026, 10, 18)
D = dt.date.fromisoformat
# Проверки здоровья плана, которым на этих данных взяться неоткуда: сработавшая
# значила бы, что импорт сам рассогласовал план и факты.
MUST_BE_CLEAN = ('fact_without_position', 'number_mismatch', 'date_mismatch', 'done_in_future',
                 'beyond_course')


def _ts(day: str, hhmm: str = '09:00') -> str:
    """Метка audit_log в UTC (как toISOString) для момента МСК day hhmm."""
    msk = dt.datetime.fromisoformat(f'{day}T{hhmm}')
    return (msk - dt.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.teacher(2, 'Борис Тестов')
    db.teacher(3, 'Вера Замена')
    return db


def _run(db: EdusonDB, *, feedback: bool = False) -> ImportContext:
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
        if feedback:
            import_feedback(src, ctx)
    return ctx


def _teacher(name: str) -> int:
    return Teacher.objects.get(name=name).id


def _lesson(ctx, session_id) -> Lesson:
    return Lesson.objects.get(pk=ctx.lesson_by_session[session_id])


def _planned(ctx, session_id) -> PlannedLesson:
    return PlannedLesson.objects.get(pk=ctx.planned_by_session[session_id])


def _assert_plan_healthy(ctx, *eduson_group_ids):
    for gid in eduson_group_ids:
        findings = health.check_group(ctx.group_by_eduson[gid])['findings']
        assert not set(findings) & set(MUST_BE_CLEAN), findings


# ── нумерация, фиктивные held, отработка, отмена ────────────────────────────

def _numbering_db(tmp_path) -> EdusonDB:
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20, dow=1, time='15:00')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(5, 'Маша', 'Мама Маши', '79990000002')
    # Фиктивная held первичной загрузки: дата-заглушка в будущем, без времени, №1.
    db.session(90, 5, '2027-06-07', time='', lesson_no=1)
    # Урок отметили «проведён» кнопкой статуса; updated_at потом сдвинула ссылка на запись.
    db.session(101, 5, '2026-07-06', status='scheduled', lesson_no=2, recording_url='https://rec/101',
               created_at='2026-06-28T20:58:00.000Z')
    db.set_status(101, 'held', ts=_ts('2026-07-06', '16:05'))
    db.con.execute("UPDATE sessions SET updated_at='2026-07-20T09:00:00.000Z' WHERE id=101")
    # Отработка из первичной загрузки: held без следа в журнале — момент = created_at.
    db.session(102, 5, '2026-07-13', type='makeup', duration=45, lesson_no=3,
               created_at='2026-07-13T15:00:00.000Z', updated_at='2026-07-25T09:00:00.000Z')
    db.session(103, 5, '2026-07-20', status='cancelled', lesson_no=4)
    db.session(104, 5, '2026-07-27', lesson_no=4)
    db.session(105, 5, '2030-01-07', status='scheduled', lesson_no=5)
    return db


def test_numbering_continues_after_pre_system_and_skips_fake_held(tmp_path):
    ctx = _run(_numbering_db(tmp_path))
    group = Group.objects.get(pk=ctx.group_by_eduson[5])
    assert group.lesson_number_offset == Decimal('1')     # №1 пройден до первого учтённого

    # Фиктивная held не стала ни уроком, ни позицией плана.
    assert 90 not in ctx.lesson_by_session and 90 not in ctx.planned_by_session
    assert set(ctx.lesson_by_session) == {101, 102, 104}
    assert set(ctx.planned_by_session) == {101, 102, 103, 104, 105}
    assert Lesson.objects.count() == 3 and PlannedLesson.objects.count() == 5
    assert any('ПИ1001' in line and 'фиктивн' in line for line in ctx.report)

    # N=1; отработка 45 мин весит 0.5; отмена номер не занимает.
    assert [_lesson(ctx, s).lesson_number for s in (101, 102, 104)] == [Decimal('2'), Decimal('2.5'),
                                                                         Decimal('3.5')]
    plan = [_planned(ctx, s) for s in (101, 102, 103, 104, 105)]
    assert [(p.seq, p.lesson_number) for p in plan] == [
        (1, Decimal('2')), (2, Decimal('2.5')), (None, None), (3, Decimal('3.5')), (4, Decimal('4.5'))]
    assert [p.status for p in plan] == [DONE, DONE, CANCELLED, DONE, PENDING]
    assert [p.fact_lesson_id for p in plan] == [ctx.lesson_by_session[101], ctx.lesson_by_session[102],
                                                None, ctx.lesson_by_session[104], None]
    assert plan[2].scheduled_date == D('2026-07-20') and plan[4].scheduled_date == D('2030-01-07')
    _assert_plan_healthy(ctx, 5)


def test_held_lesson_fields(tmp_path):
    ctx = _run(_numbering_db(tmp_path))
    first, makeup = _lesson(ctx, 101), _lesson(ctx, 102)
    anna = _teacher('Анна Тестова')
    assert (first.group_id, first.teacher_id, first.original_teacher_id) == (ctx.group_by_eduson[5], anna, None)
    assert (first.lesson_date, first.lesson_duration_minutes, first.lesson_type) == (D('2026-07-06'), 60, 'regular')
    assert first.record_url == 'https://rec/101' and makeup.record_url is None
    assert first.submitted_by_token == SUBMITTED_BY == 'eduson-import'
    assert first.submission_key is None                          # исторический факт, как в KOTOKOD
    # Момент перехода в held из журнала, не updated_at (её двигает ссылка на запись).
    assert first.submitted_at == dt.datetime(2026, 7, 6, 13, 5, tzinfo=dt.timezone.utc)
    assert makeup.submitted_at == dt.datetime(2026, 7, 13, 15, 0, tzinfo=dt.timezone.utc)
    # Сессию сгенерировали заранее (created_at 01.07), следа «проведён» нет — момент
    # не раньше самого урока: 27.07 15:00 МСК.
    assert _lesson(ctx, 104).submitted_at == dt.datetime(2026, 7, 27, 12, 0, tzinfo=dt.timezone.utc)
    # Отработка — курсовой урок 45 минут, не доп.урок KOTOKOD.
    assert (makeup.lesson_type, makeup.lesson_duration_minutes) == ('regular', 45)

    # Посещаемость — все ученики группы, «был».
    students = set(LessonAttendance.objects.filter(lesson=first).values_list('student_id', 'present'))
    assert students == {(ctx.student_by_participant[p], True) for p in ctx.student_by_participant}
    assert LessonAttendance.objects.count() == 3 * 2

    pos = _planned(ctx, 101)
    assert (pos.scheduled_date, pos.scheduled_time) == (D('2026-07-06'), dt.time(15, 0))
    assert (pos.teacher_id, pos.substitute_teacher_id, pos.moved_from_date) == (anna, None, None)


# ── преподаватель урока: замена и смена преподавателя группы ────────────────
#
# Как в боевой базе: «Исправить прогресс» генерирует уроки с отметкой teacher_id
# группы (Анна), потом преподавателя группы меняют карточкой — старые уроки
# сохраняют отметку Анны. Замену доказывает только перенос/правка урока с teacherId.

def _teacher_change_db(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=6, dow=1, time='15:00')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.update_progress(5, lessons_done=0, from_date='2026-08-03', ts=_ts('2026-08-01'))
    ids = [r['id'] for r in db._rows('SELECT id FROM sessions WHERE group_id=5 ORDER BY date')]
    # 03.08, 10.08, 17.08, 24.08, 31.08, 07.09 — все с отметкой Анны (#1)
    db.update_group(5, teacher_id=2, audit_kw={'ts': _ts('2026-08-05')})   # группу ведёт Борис
    db.move_session(ids[3], teacher_id=3, ts=_ts('2026-08-20'))             # разовая замена: Вера
    db.move_session(ids[5], teacher_id=3, ts=_ts('2026-08-20'))             # замена на непроведённом уроке
    for sid in ids[:5]:
        db.set_status(sid, 'held', ts=_ts('2026-09-01'))
    return db, ids


def test_teacher_change_by_card_is_not_substitution(tmp_path):
    db, ids = _teacher_change_db(tmp_path)
    ctx = _run(db)
    anna, boris, vera = _teacher('Анна Тестова'), _teacher('Борис Тестов'), _teacher('Вера Замена')

    before_change = _lesson(ctx, ids[0])                  # 03.08 — группа ещё у Анны
    assert (before_change.teacher_id, before_change.original_teacher_id, before_change.lesson_type) == (
        anna, None, 'regular')
    assert (_planned(ctx, ids[0]).teacher_id, _planned(ctx, ids[0]).substitute_teacher_id) == (anna, None)

    # 10.08 и 17.08: отметка генерации — Анна, но группа уже у Бориса и замены не было.
    for sid in ids[1:3]:
        lesson = _lesson(ctx, sid)
        assert (lesson.teacher_id, lesson.original_teacher_id, lesson.lesson_type) == (boris, None, 'regular')
        assert (_planned(ctx, sid).teacher_id, _planned(ctx, sid).substitute_teacher_id) == (boris, None)
        assert any(f'#{sid}' in line and 'замены' in line for line in ctx.manual_review)

    sub = _lesson(ctx, ids[3])                             # 24.08 — замена переносом
    assert (sub.teacher_id, sub.original_teacher_id) == (vera, boris)
    assert sub.lesson_type == 'substitution'               # замена важнее переноса
    assert (_planned(ctx, ids[3]).teacher_id, _planned(ctx, ids[3]).substitute_teacher_id) == (boris, vera)
    assert not any(f'#{ids[3]}' in line for line in ctx.manual_review)

    future = _planned(ctx, ids[5])                         # непроведённая позиция с заменой
    assert (future.status, future.teacher_id, future.substitute_teacher_id) == (PENDING, boris, vera)
    _assert_plan_healthy(ctx, 5)


def test_pending_and_cancelled_positions_belong_to_owner(tmp_path):
    """Отметка генерации на непроведённых позициях не оставляет их старому
    преподавателю: teacher = преподаватель группы на дату (будущие — текущий)."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=3, dow=1, time='15:00')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.update_progress(5, lessons_done=0, from_date='2029-12-31', ts=_ts('2026-08-01'))
    ids = [r['id'] for r in db._rows('SELECT id FROM sessions WHERE group_id=5 ORDER BY date')]
    db.update_group(5, teacher_id=2, audit_kw={'ts': _ts('2026-09-01')})
    db.set_status(ids[1], 'cancelled')
    ctx = _run(db)
    boris = _teacher('Борис Тестов')
    assert {(_planned(ctx, sid).teacher_id, _planned(ctx, sid).substitute_teacher_id) for sid in ids} == {
        (boris, None)}
    assert _planned(ctx, ids[1]).status == CANCELLED


def test_reschedule_with_teacher_change_from_past_date(tmp_path):
    """Расписание сменили с прошедшей даты вместе с преподавателем: урок, проведённый
    в день fromDate раньше самой записи, уже у нового преподавателя (legacy переводит
    группу с fromDate), будущие позиции — у него же."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=4, dow=1, time='15:00')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.update_progress(5, lessons_done=0, from_date='2026-08-31', ts=_ts('2026-08-30'))
    ids = [r['id'] for r in db._rows('SELECT id FROM sessions WHERE group_id=5 ORDER BY date')]
    db.set_status(ids[0], 'held', ts=_ts('2026-08-31', '16:00'))           # 31.08 — у Анны
    db.set_status(ids[1], 'held', ts=_ts('2026-09-07', '16:00'))           # 07.09 — уже fromDate
    db.reschedule_group(5, from_date='2026-09-07', dows=[1], time='15:00', teacher_id=2,
                        ts=_ts('2026-09-09'))
    new_ids = [r['id'] for r in db._rows("SELECT id FROM sessions WHERE group_id=5 AND status='scheduled'")]
    ctx = _run(db)
    anna, boris = _teacher('Анна Тестова'), _teacher('Борис Тестов')
    assert (_lesson(ctx, ids[0]).teacher_id, _lesson(ctx, ids[0]).lesson_type) == (anna, 'regular')
    assert (_lesson(ctx, ids[1]).teacher_id, _lesson(ctx, ids[1]).lesson_type) == (boris, 'regular')
    assert new_ids and {_planned(ctx, sid).teacher_id for sid in new_ids} == {boris}
    _assert_plan_healthy(ctx, 5)


def test_explicit_mark_used_when_owner_unknown(tmp_path):
    """Преподавателя группы на дату нет, замены нет — проведший = отметка урока."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', None, fmt='group', package=20)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.update_group(5, teacher_id=1, audit_kw={'ts': _ts('2026-09-10')})
    db.session(310, 5, '2026-09-01', teacher_id=2)
    ctx = _run(db)
    lesson = _lesson(ctx, 310)
    assert (lesson.teacher_id, lesson.original_teacher_id, lesson.lesson_type) == (
        _teacher('Борис Тестов'), None, 'regular')
    assert not any('#310' in line for line in ctx.manual_review)


def test_unknown_group_teacher_on_date_falls_back_to_current_and_flags(tmp_path):
    """У группы был период без преподавателя (legacy PATCH teacherId: null), а у
    урока нет явного teacher_id: Lesson.teacher обязателен — ставим текущего и
    просим человека проверить."""
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', None, fmt='group', package=20)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.update_group(5, teacher_id=None, notes='x', audit_kw={'ts': _ts('2026-08-10')})
    db.update_group(5, teacher_id=1, audit_kw={'ts': _ts('2026-09-10')})
    db.session(301, 5, '2026-09-01')
    db.session(302, 5, '2026-09-21')
    ctx = _run(db)
    anna = _teacher('Анна Тестова')
    assert (_lesson(ctx, 301).teacher_id, _lesson(ctx, 301).lesson_type) == (anna, 'regular')
    assert any('#301' in line and 'преподавател' in line for line in ctx.manual_review)
    assert not any('#302' in line for line in ctx.manual_review)


# ── переносы ────────────────────────────────────────────────────────────────

def test_moved_sessions(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20, dow=3, time='15:00')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.session(401, 5, '2026-07-08', status='scheduled')
    db.move_session(401, date='2026-07-09')
    db.set_status(401, 'held')                                   # перенесённый урок провели
    db.session(402, 5, '2030-01-09', status='scheduled')
    db.move_session(402, date='2030-01-10', time='16:30')        # перенос по дате — будущий
    db.session(403, 5, '2030-01-16', status='scheduled')
    db.move_session(403, time='18:00')                           # перенесли только время
    ctx = _run(db)

    held = _lesson(ctx, 401)
    assert (held.lesson_type, held.lesson_date) == ('reschedule', D('2026-07-09'))
    # Проведённая позиция стоит на дате факта без метки переноса — так её держит
    # сам KOTOKOD (relink_fact/link_facts/sync_position_date гасят moved_from_date).
    pos = _planned(ctx, 401)
    assert (pos.status, pos.scheduled_date, pos.moved_from_date) == (DONE, D('2026-07-09'), None)

    future = _planned(ctx, 402)
    assert (future.status, future.scheduled_date, future.scheduled_time, future.moved_from_date) == (
        PENDING, D('2030-01-10'), dt.time(16, 30), D('2030-01-09'))
    time_only = _planned(ctx, 403)
    assert (time_only.scheduled_time, time_only.moved_from_date) == (dt.time(18, 0), None)
    _assert_plan_healthy(ctx, 5)


# ── прошлые scheduled, сессии без времени, группы без учеников ──────────────

def test_past_scheduled_stays_pending_and_is_reported(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.session(501, 5, '2026-08-03')
    db.session(502, 5, '2026-08-10', status='scheduled')     # не отмечен — просрочен
    db.session(503, 5, '2026-08-17', status='scheduled')
    db.session(504, 5, '2030-01-07', status='scheduled')
    ctx = _run(db)
    assert {_planned(ctx, s).status for s in (502, 503, 504)} == {PENDING}
    assert [_planned(ctx, s).lesson_number for s in (501, 502, 503, 504)] == [
        Decimal('1'), Decimal('2'), Decimal('3'), Decimal('4')]
    assert any('ПИ1001' in line and 'прошл' in line and '2' in line for line in ctx.report)


def test_session_without_time_takes_slot_time(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20,
             slots=[{'dow': 1, 'time': '15:00'}, {'dow': 3, 'time': '17:30'}])
    db.group(6, 'ПИ1002', 'python', 1, fmt='group', package=20, dow=None, time='')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(6, 'Маша', 'Мама Маши', '79990000002')
    db.session(601, 5, '2026-08-05', time='')               # среда → слот среды
    db.session(602, 5, '2026-08-07', time='')               # пятница: слота нет → первый слот
    db.session(603, 6, '2026-08-07', time='')               # слотов нет вовсе → 00:00
    ctx = _run(db)
    assert _planned(ctx, 601).scheduled_time == dt.time(17, 30)
    assert _planned(ctx, 602).scheduled_time == dt.time(15, 0)
    assert _planned(ctx, 603).scheduled_time == dt.time(0, 0)
    assert any('ПИ1001' in line and 'без времени' in line for line in ctx.report)
    assert any('ПИ1002' in line and 'без времени' in line for line in ctx.report)


def test_bad_time_is_import_error(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20)
    db.session(701, 5, '2026-08-03', time='25:99')
    with pytest.raises(ImportDataError, match='#701'):
        _run(db)


@pytest.mark.parametrize('field,value', [('status', 'done'), ('type', 'extra')])
def test_unknown_status_or_type_is_import_error(tmp_path, field, value):
    db = _db(tmp_path)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20)
    db.session(702, 5, '2026-08-03', **{field: value})
    with pytest.raises(ImportDataError, match='#702'):
        _run(db)


def test_group_without_students_gets_lessons_without_attendance(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'СИ1019', 'scratch', 1, package=20)
    db.empty_participant(5)                                  # legacy createGroup без участников
    db.group(6, 'ПИ1002', 'python', 1, fmt='group', package=20)
    db.empty_participant(6)
    db.participant(6, 'Миша', 'Мама Миши', '79990000001')
    db.session(801, 5, '2026-08-03')
    db.session(802, 6, '2026-08-03')
    ctx = _run(db)
    assert not LessonAttendance.objects.filter(lesson_id=ctx.lesson_by_session[801]).exists()
    assert LessonAttendance.objects.filter(lesson_id=ctx.lesson_by_session[802]).count() == 1
    assert any('СИ1019' in line and 'без учеников' in line and 'посещаемост' in line for line in ctx.report)
    _assert_plan_healthy(ctx, 5, 6)


def test_check_all_is_clean_on_factory_scenario(tmp_path):
    """Сводка по всем группам — та же, что импорт выведет в сверке."""
    db = _numbering_db(tmp_path)
    db.update_group(5, teacher_id=2, audit_kw={'ts': _ts('2026-07-15')})
    db.session(106, 5, '2026-08-03', teacher_id=3)
    _run(db)
    for row in health.check_all()['groups']:
        assert not set(row['counts']) & set(MUST_BE_CLEAN), row


# ── отзывы ──────────────────────────────────────────────────────────────────

def _feedback_db(tmp_path) -> EdusonDB:
    db = _numbering_db(tmp_path)
    db.report(101, 5, 1, understanding=7, interest=9, tech_problems='yes', comment='ок',
              submitted_at='2026-07-06T14:00:00.000Z')
    db.report(103, 5, 1, took_place='no', understanding=None, interest=None, comment='заболел')
    db.report(105, 5, 1, status='pending')                       # служебная заготовка напоминания
    db.session(150, 5, '2026-08-03', status='scheduled')
    db.report(150, 5, 2, took_place='no', understanding=3, interest=4)
    db.delete_session(150)                                       # урок удалили — отзыв висит
    db.report(9999, 5, 1)                                        # урок исчез без следа
    db.report(90, 5, 1)                                          # на фиктивную held
    db.report(104, 5, 1, understanding=11, interest=0)           # вне шкалы
    return db


def test_feedback_import(tmp_path):
    ctx = _run(_feedback_db(tmp_path), feedback=True)
    group_id = ctx.group_by_eduson[5]
    anna, boris = _teacher('Анна Тестова'), _teacher('Борис Тестов')
    assert LessonFeedback.objects.count() == 4

    held = LessonFeedback.objects.get(lesson_id=ctx.lesson_by_session[101])
    assert (held.group_id, held.lesson_date, held.teacher_id, held.took_place) == (
        group_id, D('2026-07-06'), anna, True)
    assert (held.understanding, held.interest, held.tech_problems, held.comment) == (7, 9, True, 'ок')
    assert held.submitted_at == dt.datetime(2026, 7, 6, 14, 0, tzinfo=dt.timezone.utc)

    cancelled = LessonFeedback.objects.get(group_id=group_id, lesson_date=D('2026-07-20'))
    assert (cancelled.lesson_id, cancelled.took_place, cancelled.understanding, cancelled.interest,
            cancelled.tech_problems, cancelled.comment) == (None, False, None, None, False, 'заболел')

    deleted = LessonFeedback.objects.get(group_id=group_id, lesson_date=D('2026-08-03'))
    # Оценки у «не состоялся» переносятся как есть (спека §5.4).
    assert (deleted.lesson_id, deleted.teacher_id, deleted.took_place, deleted.understanding) == (
        None, boris, False, 3)

    out_of_range = LessonFeedback.objects.get(lesson_id=ctx.lesson_by_session[104])
    assert (out_of_range.understanding, out_of_range.interest) == (None, None)

    review = '\n'.join(ctx.manual_review)
    assert 'урок #9999' in review and 'урок #90' in review and 'оценк' in review
    assert any('не отправлен' in line and '1' in line for line in ctx.report)
    # Отчёт и ручная проверка — без ПДн (комментарии преподавателя туда не попадают).
    assert 'заболел' not in review and 'заболел' not in '\n'.join(ctx.report)


def test_feedback_teacher_missing_is_import_error(tmp_path):
    db = _numbering_db(tmp_path)
    db.report(101, 5, None)
    with pytest.raises(ImportDataError, match='преподавател'):
        _run(db, feedback=True)


def test_feedback_on_deleted_lesson_takes_group_from_report(tmp_path):
    """Отзыв на удалённый урок: группа — из отчёта (спека §5.4), а если её среди
    перенесённых нет — из снимка урока; дата — всегда из снимка."""
    db = _numbering_db(tmp_path)
    db.group(6, 'ПИ1002', 'python', 2, fmt='group', package=20)
    db.participant(6, 'Петя', 'Мама Пети', '79990000003')
    db.session(160, 5, '2026-08-10', status='scheduled')
    db.report(160, 6, 2, took_place='no', understanding=None, interest=None)    # группа в отчёте — 6
    db.delete_session(160)
    db.session(161, 5, '2026-08-17', status='scheduled')
    db.report(161, 77, 2, took_place='no', understanding=None, interest=None)   # группы 77 нет
    db.delete_session(161)
    ctx = _run(db, feedback=True)
    by_date = {f.lesson_date: f for f in LessonFeedback.objects.all()}
    assert by_date[D('2026-08-10')].group_id == ctx.group_by_eduson[6]
    assert by_date[D('2026-08-17')].group_id == ctx.group_by_eduson[5]
    assert all(by_date[d].lesson_id is None for d in (D('2026-08-10'), D('2026-08-17')))


def test_payroll_not_created_is_reported(tmp_path):
    ctx = _run(_numbering_db(tmp_path))
    assert ctx.report.count('зарплата за импортированные уроки не начисляется (спека §5.4)') == 1
