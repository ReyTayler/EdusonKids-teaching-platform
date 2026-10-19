"""Источники данных дайджестов: teacher_id в fill_service и доп.уроки на дату.

Сама сборка дайджестов (Task 8+) не входит сюда — только два источника данных,
которые ей понадобятся: teacher_id в unfilled_lessons() и новая
extra_lessons.repository.scheduled_on() для утреннего списка доп.уроков.
"""
from __future__ import annotations

import datetime

import pytest

from apps.dashboard import fill_service
from apps.dashboard.tests.test_fill_api import fill_setup  # noqa: F401 — переиспользуем фикстуру
from apps.extra_lessons import repository as extra_repo
from apps.extra_lessons.tests.conftest import (  # noqa: F401 — переиспользуем фикстуры
    direction_fixture,
    group_fixture,
    membership_fixture,
    missed_lesson_fixture,
    student_fixture,
    teacher_fixture,
)
from apps.extra_lessons.tests.test_extra_lessons_repository import (  # noqa: F401
    resolution_cleanup,
)


@pytest.mark.django_db
def test_unfilled_lessons_expose_teacher_id():
    """Вечернему дайджесту нужен id для группировки, а не только имя.

    Раньше fill_service отдавал только teacher_name — по имени группировать нельзя,
    полные тёзки в школе встречаются.
    """
    rows = fill_service.unfilled_lessons(
        now=datetime.datetime(2026, 8, 3, 22, 0, tzinfo=fill_service.MSK))
    for row in rows:
        assert 'teacher_id' in row


def test_unfilled_lessons_teacher_id_matches_known_row(fill_setup):
    """Усиленная версия предыдущего теста: тест выше проходит вхолостую, если
    unfilled_lessons() вернёт пустой список (цикл не выполнится). Здесь строка
    гарантированно есть (fill_setup создаёт overdue-занятие) и teacher_id
    сверяется с конкретным известным id, а не просто с наличием ключа."""
    now = datetime.datetime(2026, 7, 1, 12, 0, tzinfo=fill_service.MSK)
    rows = fill_service.unfilled_lessons(now=now)
    ours = [r for r in rows if r['group_id'] == fill_setup['group']]
    assert len(ours) == 1
    assert ours[0]['teacher_id'] == fill_setup['teacher']


@pytest.mark.django_db
def test_scheduled_extra_lessons_on_date_returns_empty_for_far_future():
    assert extra_repo.scheduled_on(datetime.date(2099, 1, 1)) == []


@pytest.mark.django_db
def test_scheduled_on_returns_makeup_with_group_pk(
    teacher_fixture, group_fixture, missed_lesson_fixture, student_fixture, resolution_cleanup,
):
    """Осмысленная проверка: реальный назначенный доп.урок-отработка (kind='makeup',
    группа = missed_lesson.group_id) находится по дате и отдаёт правильный group_pk
    через Coalesce."""
    target_date = datetime.date(2026, 8, 10)
    resolution_id = extra_repo.lock_for_assign(missed_lesson_fixture, student_fixture)['id']
    extra_repo.assign_pending(
        resolution_id, assigned_teacher_id=teacher_fixture,
        scheduled_date=target_date, scheduled_time=datetime.time(9, 30),
        duration_minutes=45)

    rows = extra_repo.scheduled_on(target_date)
    mine = [r for r in rows if r['id'] == resolution_id]
    assert len(mine) == 1
    row = mine[0]
    assert row['assigned_teacher_id'] == teacher_fixture
    assert row['student_id'] == student_fixture
    assert row['kind'] == 'makeup'
    assert row['group_pk'] == group_fixture
