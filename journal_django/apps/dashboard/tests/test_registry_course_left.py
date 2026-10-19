"""
Остаток в «Реестре куратора» Eduson — остаток КУРСА, а не денег (спека этапа 3, раздел B).

Оплат в Eduson нет (решение владельца на этапе 1), поэтому прежний остаток
«куплено − пройдено» давал «Закрыт» всем ученикам школы. Теперь
`balance` = Σ по активным членствам (длина курса группы − пройдено):
  • длина — groups.lessons_total, пусто → directions.total_lessons
    (apps.groups.course_length);
  • пройдено — lessons_done членства (в группе-продолжении он уже засеян
    офсетом) + неоплачиваемые пропуски в группе (тот же счёт, что у прогресса).

Плюс сегмент «Отмены»: в текущем месяце у группы ученика есть отменённое
плановое занятие — то же определение, что у KPI cancellations сводки.

Список (base_students_qs) и сводка (_summary_rows) проверяются оба: числа
обязаны совпадать.
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection
from django.utils import timezone

from apps.dashboard import registry_service as svc
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import CANCELLED

pytestmark = pytest.mark.django_db

TODAY = datetime.date(2026, 6, 15)


@pytest.fixture(scope='session')
def django_db_setup():
    pass


@pytest.fixture
def graph():
    """
    Направление на 16 уроков и четыре группы (оплат нет ни у кого):
      g10   — 60 мин, lessons_total=10 (ручная длина перекрывает направление);
      g45   — 45 мин, lessons_total пусто → 16 от направления;
      gcont — продолжение перевода: offset=6, lessons_total=12;
      gold  — архивная группа (active=false) — в остаток не идёт.
    """
    ids: dict[str, list[int]] = {'groups': [], 'students': []}
    with connection.cursor() as cur:
        cur.execute("INSERT INTO directions (name, total_lessons, active) "
                    "VALUES ('__rcl_dir__', 16, true) RETURNING id")
        direction_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name) VALUES ('__rcl_teacher__') RETURNING id")
        teacher_id = cur.fetchone()[0]
        groups = {}
        for key, duration, total, offset, active in (
            ('g10', 60, 10, 0, True),
            ('g45', 45, None, 0, True),
            ('gcont', 60, 12, 6, True),
            ('gold', 60, 10, 0, False),
        ):
            cur.execute(
                'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
                'lesson_duration_minutes, active, lesson_number_offset, lessons_total) '
                'VALUES (%s, %s, %s, false, %s, %s, %s, %s) RETURNING id',
                [f'__rcl_{key}__', direction_id, teacher_id, duration, active, offset, total],
            )
            groups[key] = cur.fetchone()[0]
            ids['groups'].append(groups[key])

    g = {'direction_id': direction_id, 'teacher_id': teacher_id, **groups, '_ids': ids}
    yield g

    with connection.cursor() as cur:
        group_ids, student_ids = ids['groups'], ids['students']
        cur.execute('DELETE FROM planned_lessons WHERE group_id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM lesson_skips WHERE group_id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM group_memberships WHERE student_id = ANY(%s)', [student_ids])
        cur.execute('DELETE FROM students WHERE id = ANY(%s)', [student_ids])
        cur.execute('DELETE FROM groups WHERE id = ANY(%s)', [group_ids])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])


def _student(g, name: str, memberships=()) -> int:
    """Ученик + членства [(group_id, lessons_done, active)]."""
    with connection.cursor() as cur:
        cur.execute('INSERT INTO students (full_name) VALUES (%s) RETURNING id',
                    [f'__rcl_{name}__'])
        sid = cur.fetchone()[0]
        for group_id, done, active in memberships:
            cur.execute(
                'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                'VALUES (%s, %s, %s, %s)', [group_id, sid, done, active],
            )
    g['_ids']['students'].append(sid)
    return sid


def _slot_skip(group_id: int, student_id: int, lesson_number) -> None:
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lesson_skips (group_id, student_id, lesson_number, created_at) '
            'VALUES (%s, %s, %s, now())', [group_id, student_id, lesson_number],
        )


def _cancelled(g, group_id: int, day: datetime.date) -> None:
    now = timezone.now()
    PlannedLesson.objects.create(
        group_id=group_id, teacher_id=g['teacher_id'], scheduled_date=day,
        scheduled_time=datetime.time(18, 0), status=CANCELLED,
        created_at=now, updated_at=now,
    )


def _list_row(sid: int, *, no_group: bool = False):
    return svc.base_students_qs(TODAY, no_group=no_group).get(pk=sid)


def _summary_row(sid: int) -> dict:
    return {r['student_id']: r for r in svc._summary_rows(TODAY)}[sid]


def _left(sid: int):
    """Остаток обоими путями — они обязаны совпасть."""
    listed = _list_row(sid).balance
    assert _summary_row(sid)['balance'] == listed, 'список и сводка разошлись'
    return listed


# --- формула ---------------------------------------------------------------

def test_left_is_group_course_length_minus_done_without_payments(graph):
    """Ручная длина группы (10) перекрывает направление (16); оплат нет — не «Закрыт»."""
    sid = _student(graph, 'basic', [(graph['g10'], 7, True)])
    assert _left(sid) == 3
    assert _list_row(sid).planned == 10


def test_left_falls_back_to_direction_length(graph):
    sid = _student(graph, 'dir', [(graph['g45'], 10, True)])
    assert _left(sid) == 6
    assert _list_row(sid).planned == 16


def test_left_sums_over_active_memberships(graph):
    sid = _student(graph, 'two', [(graph['g10'], 9, True), (graph['g45'], 14, True)])
    assert _left(sid) == (10 - 9) + (16 - 14)


def test_half_lesson_progress_leaves_half_lesson(graph):
    """45-мин курс: lessons_done растёт шагом 0.5 — остаток бывает дробным."""
    sid = _student(graph, 'half', [(graph['g45'], 15.5, True)])
    assert _left(sid) == 0.5


def test_transfer_continuation_uses_seeded_done(graph):
    """Группа-продолжение: lessons_done уже содержит офсет (6) + свои уроки (3)."""
    sid = _student(graph, 'cont', [(graph['gcont'], 9, True)])
    assert _left(sid) == 12 - 9


def test_unpaid_skips_reduce_left(graph):
    """Неоплачиваемый пропуск — урок курса, пройденный в другом месте."""
    sid = _student(graph, 'skip', [(graph['g10'], 5, True)])
    _slot_skip(graph['g10'], sid, 6)
    _slot_skip(graph['g10'], sid, 7)
    assert _left(sid) == 10 - 5 - 2


def test_inactive_membership_and_archived_group_do_not_count(graph):
    sid = _student(graph, 'inact', [
        (graph['g10'], 7, True),
        (graph['g45'], 0, False),      # снятое членство
        (graph['gold'], 0, True),      # активное членство в архивной группе
    ])
    assert _left(sid) == 3
    assert _list_row(sid).planned == 10


def test_no_group_student_has_no_course_left(graph):
    """Без активного курса остатка нет (None), а не 0 — иначе все «Без группы» «Закрыты»."""
    sid = _student(graph, 'nogroup', [(graph['g10'], 4, False)])
    row = _list_row(sid, no_group=True)
    assert row.balance is None
    out = svc.serialize_rows([row])[0]
    assert out['balance'] is None
    assert out['status'] != 'closed'


# --- статусы на границах ---------------------------------------------------

@pytest.mark.parametrize(('done', 'status'), [
    (10, 'closed'),   # left 0
    (8, 'ending'),    # left 2 — включительно
    (7.5, 'ok'),      # left 2.5
])
def test_status_boundaries_on_course_left(graph, done, status):
    sid = _student(graph, f'b{done}', [(graph['g10'], done, True)])
    _future_plan(graph, graph['g10'])
    row = _list_row(sid)
    assert svc.serialize_rows([row])[0]['status'] == status


def test_closed_and_ending_segments_follow_course_left(graph):
    closed = _student(graph, 'segc', [(graph['g10'], 10, True)])
    ending = _student(graph, 'sege', [(graph['g10'], 9, True)])
    ok = _student(graph, 'sego', [(graph['g10'], 3, True)])

    def ids(segment):
        return set(svc.students_qs(segment=segment, search='__rcl_seg')
                   .values_list('pk', flat=True))

    assert ids('closed') == {closed}
    assert ids('ending') == {ending}
    assert ok not in ids('closed') | ids('ending')


def _future_plan(g, group_id):
    """Будущее занятие, чтобы статус не скатывался в no_plan."""
    from apps.scheduling.occurrences import PENDING
    now = timezone.now()
    PlannedLesson.objects.create(
        group_id=group_id, teacher_id=g['teacher_id'], seq=1, lesson_number=1,
        scheduled_date=TODAY + datetime.timedelta(days=3),
        scheduled_time=datetime.time(18, 0), status=PENDING,
        created_at=now, updated_at=now,
    )


# --- сегмент «Отмены» ------------------------------------------------------

def test_cancellations_segment_this_month_only(graph):
    hit = _student(graph, 'cxl_hit', [(graph['g10'], 3, True)])
    old = _student(graph, 'cxl_old', [(graph['g45'], 3, True)])
    gone = _student(graph, 'cxl_gone', [(graph['gcont'], 7, False)])
    _cancelled(graph, graph['g10'], TODAY.replace(day=2))
    _cancelled(graph, graph['g45'], datetime.date(2026, 5, 30))   # прошлый месяц
    _cancelled(graph, graph['gcont'], TODAY.replace(day=3))       # членство снято

    qs = svc._apply_segment(svc.base_students_qs(TODAY), 'cancellations', TODAY)
    found = set(qs.filter(full_name__startswith='__rcl_cxl').values_list('pk', flat=True))
    assert found == {hit}
    assert old not in found and gone not in found

    assert _list_row(hit).cancellations == 1
    assert _list_row(old).cancellations == 0
    assert _summary_row(hit)['cancellations'] == 1
    assert _summary_row(old)['cancellations'] == 0


def test_cancellations_in_serialized_row_and_summary_signal(graph, monkeypatch):
    monkeypatch.setattr(svc, '_today', lambda: TODAY)
    sid = _student(graph, 'cxl_ser', [(graph['g10'], 3, True)])
    _cancelled(graph, graph['g10'], TODAY.replace(day=5))
    _cancelled(graph, graph['g10'], TODAY.replace(day=6))

    out = svc.serialize_rows([_list_row(sid)])[0]
    assert out['cancellations'] == 2
    assert out['status'] == 'no_plan'   # отмены — сигнал, не статус-бейдж

    summary = svc.build_summary()
    assert summary['signals']['cancellations']['count'] >= 1
    # KPI — число отменённых занятий, сегмент — число учеников: разные единицы.
    assert summary['kpis']['cancellations'] >= 2


def test_cancellations_segment_is_whitelisted():
    assert 'cancellations' in svc.SEGMENTS
