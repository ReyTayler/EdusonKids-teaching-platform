"""
Согласованность «план ↔ факт» при правке и удалении урока (docs/plan-plan-fact-sync.md).

Задача A — link_facts берёт в кандидаты только настоящие занятия курса
  (regular/substitution/reschedule). Сгорание пропуска (burned) и доп.урок (extra)
  занятиями не являются — позицию курса занимать не должны.

Задача B — привязка пересчитывается:
  B1 правка lesson_number перевешивает факт на позицию с тем же номером;
  B2 правка lesson_date привязку НЕ трогает (плановая и фактическая даты
     законно расходятся — разовый перенос);
  B3 удаление урока освобождает позицию, и она подхватывает свободный факт
     с совпадающим номером (случай ВДГ18).
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.lessons import repository, services
from apps.scheduling.repository import link_facts

pytestmark = pytest.mark.django_db


def _planned(group_id: int, seq: int, number, date: str, teacher_id: int) -> int:
    """Плановая позиция курса в состоянии «ждём»."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO planned_lessons (group_id, seq, lesson_number, scheduled_date, '
            "scheduled_time, teacher_id, status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, '10:00', %s, 'pending', NOW(), NOW()) RETURNING id",
            [group_id, seq, number, date, teacher_id],
        )
        return cur.fetchone()[0]


def _raw_lesson(group_id: int, teacher_id: int, date: str, number, lesson_type: str) -> int:
    """Факт напрямую в БД — минуя record_lesson (не нужны ни payroll, ни счётчики)."""
    with connection.cursor() as cur:
        cur.execute(
            'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
            'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
            "VALUES (%s, %s, %s, %s, 60, %s, NOW(), '__relink_test__') RETURNING id",
            [group_id, teacher_id, date, number, lesson_type],
        )
        return cur.fetchone()[0]


def _link_state(planned_id: int) -> tuple:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT fact_lesson_id, status FROM planned_lessons WHERE id = %s', [planned_id])
        return cur.fetchone()


def _cleanup(planned_ids: list[int], lesson_ids: list[int]) -> None:
    with connection.cursor() as cur:
        for pid in planned_ids:
            cur.execute('UPDATE planned_lessons SET fact_lesson_id = NULL WHERE id = %s', [pid])
        for lid in lesson_ids:
            cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lid])
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for pid in planned_ids:
            cur.execute('DELETE FROM planned_lessons WHERE id = %s', [pid])


# ---------------------------------------------------------------------------
# Задача A: сгорание/доп.урок не занимают позицию курса
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('system_type', ['burned', 'extra'])
def test_link_facts_ignores_non_course_lesson_types(
    group_fixture, teacher_id_fixture, system_type,
):
    """Сгорание пропуска и доп.урок — не занятия курса: позиция остаётся «ждём»."""
    planned_id = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    lesson_id = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, system_type)
    try:
        assert link_facts(group_fixture) == 0
        assert _link_state(planned_id) == (None, 'pending')
    finally:
        _cleanup([planned_id], [lesson_id])


def test_link_facts_still_links_regular_lesson(group_fixture, teacher_id_fixture):
    """Контроль к предыдущему тесту: обычное занятие позицию занимает."""
    planned_id = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    lesson_id = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    try:
        assert link_facts(group_fixture) == 1
        assert _link_state(planned_id) == (lesson_id, 'done')
    finally:
        _cleanup([planned_id], [lesson_id])


# ---------------------------------------------------------------------------
# Задача B1/B2: правка урока пересчитывает привязку
# ---------------------------------------------------------------------------

def test_update_lesson_number_moves_link_to_matching_position(
    group_fixture, teacher_id_fixture,
):
    """Смена номера урока перевешивает факт на позицию с тем же номером."""
    p1 = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    p2 = _planned(group_fixture, 2, 2, '2026-03-14', teacher_id_fixture)
    lesson_id = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    try:
        assert link_facts(group_fixture) == 1
        assert _link_state(p1) == (lesson_id, 'done')

        services.update_lesson(lesson_id, {'lesson_number': 2})

        assert _link_state(p1) == (None, 'pending')
        assert _link_state(p2) == (lesson_id, 'done')
    finally:
        _cleanup([p1, p2], [lesson_id])


def test_update_lesson_date_keeps_link(group_fixture, teacher_id_fixture):
    """Смена только даты привязку не трогает: перенос занятия — норма, плановая
    дата остаётся плановой."""
    p1 = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    p2 = _planned(group_fixture, 2, 2, '2026-03-14', teacher_id_fixture)
    lesson_id = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    try:
        link_facts(group_fixture)
        assert _link_state(p1) == (lesson_id, 'done')

        services.update_lesson(lesson_id, {'lesson_date': '2026-03-14'})

        assert _link_state(p1) == (lesson_id, 'done')
        assert _link_state(p2) == (None, 'pending')
    finally:
        _cleanup([p1, p2], [lesson_id])


def test_update_lesson_number_to_occupied_position_keeps_link(
    group_fixture, teacher_id_fixture,
):
    """Целевая позиция занята другим фактом — не перевешиваем (иначе выбили бы
    чужую привязку). Расхождение останется видимым, но данные целы."""
    p1 = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    p2 = _planned(group_fixture, 2, 2, '2026-03-14', teacher_id_fixture)
    first = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    second = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-14', 2, 'regular')
    try:
        assert link_facts(group_fixture) == 2
        assert _link_state(p1) == (first, 'done')
        assert _link_state(p2) == (second, 'done')

        services.update_lesson(first, {'lesson_number': 2})

        # p2 остаётся у second, first никуда не переехал
        assert _link_state(p2) == (second, 'done')
        assert _link_state(p1) == (first, 'done')
    finally:
        _cleanup([p1, p2], [first, second])


# ---------------------------------------------------------------------------
# Задача B3: удаление освобождает позицию и она подхватывает свободный факт
# ---------------------------------------------------------------------------

def test_delete_lesson_lets_position_pick_up_free_fact(
    group_fixture, teacher_id_fixture,
):
    """Случай ВДГ18: на позицию №1 претендуют два факта, привязан первый.
    Удаляем его — позиция подхватывает второй, а не остаётся «ждём»."""
    p1 = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    linked = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    spare = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-08', 1, 'regular')
    try:
        assert link_facts(group_fixture) == 1
        assert _link_state(p1) == (linked, 'done')

        assert services.delete_lesson_full(linked) is True

        assert _link_state(p1) == (spare, 'done')
    finally:
        _cleanup([p1], [spare])


def test_delete_lesson_does_not_pick_up_burned(group_fixture, teacher_id_fixture):
    """Стык A и B3: после удаления позиция не должна подхватить сгорание."""
    p1 = _planned(group_fixture, 1, 1, '2026-03-07', teacher_id_fixture)
    linked = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-07', 1, 'regular')
    burned = _raw_lesson(group_fixture, teacher_id_fixture, '2026-03-08', 1, 'burned')
    try:
        link_facts(group_fixture)
        assert _link_state(p1) == (linked, 'done')

        assert services.delete_lesson_full(linked) is True

        assert _link_state(p1) == (None, 'pending')
    finally:
        _cleanup([p1], [burned])
