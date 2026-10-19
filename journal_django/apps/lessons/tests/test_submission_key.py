"""
Ключ отправки — суррогат «это то же самое занятие». Чистая функция, без БД.

Смысл: у повторной отправки одного и того же занятия ключ обязан совпасть,
у двух разных занятий — различаться. Именно на этом держится защита от дублей
на путях, где позиции курса нет (группы без плана, дата вне плана).
"""
from __future__ import annotations

import datetime

from apps.lessons.submission_key import build_submission_key


def test_position_based_key_is_stable_across_retries():
    first = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=31)
    second = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=31)
    assert first == second


def test_different_positions_get_different_keys():
    """Мультислот: два занятия одного дня — две разные позиции, два ключа."""
    a = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=31)
    b = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=32)
    assert a != b


def test_without_position_key_falls_back_to_group_and_date():
    """Группа без плана: одно курсовое занятие на группу в день."""
    key = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=None)
    assert key == 'slot:7:2026-08-16'


def test_without_position_key_is_stable_across_retries():
    first = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=None)
    second = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=None)
    assert first == second


def test_different_dates_get_different_keys():
    a = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=None)
    b = build_submission_key(group_id=7, lesson_date='2026-08-17', planned_lesson_id=None)
    assert a != b


def test_date_object_and_string_give_same_key():
    """Вызывающие передают дату то строкой, то date — ключ обязан совпасть."""
    a = build_submission_key(group_id=7, lesson_date='2026-08-16', planned_lesson_id=None)
    b = build_submission_key(
        group_id=7, lesson_date=datetime.date(2026, 8, 16), planned_lesson_id=None,
    )
    assert a == b
