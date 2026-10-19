"""
Тесты ручной длины курса группы (groups.lessons_total).

См. docs/superpowers/specs/2026-07-27-group-lessons-total-design.md.
Схема eduson_test общая — данные создаём и удаляем сами (managed-таблицы,
django_db_setup в conftest — no-op).
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection, IntegrityError, transaction


@pytest.fixture
def dir_and_teacher(db):
    """Направление (курс 8 уроков) + преподаватель. Возвращает (direction_id, teacher_id)."""
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO directions (name, total_lessons, color, active) "
            "VALUES ('__lt_dir__', 8, '#4F59F9', true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__lt_tch__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
    yield direction_id, teacher_id
    with connection.cursor() as cur:
        cur.execute("DELETE FROM planned_lessons WHERE group_id IN "
                    "(SELECT id FROM groups WHERE direction_id = %s)", [direction_id])
        cur.execute("DELETE FROM group_schedule_slots WHERE group_id IN "
                    "(SELECT id FROM groups WHERE direction_id = %s)", [direction_id])
        cur.execute("DELETE FROM groups WHERE direction_id = %s", [direction_id])
        cur.execute("DELETE FROM directions WHERE id = %s", [direction_id])
        cur.execute("DELETE FROM teachers WHERE id = %s", [teacher_id])


@pytest.mark.django_db
def test_lessons_total_defaults_to_null(dir_and_teacher):
    """Новая группа без явного числа наследует длину курса от направления."""
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_default__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        created_at='2026-07-27T00:00:00+03:00',
    )
    assert g.lessons_total is None


@pytest.mark.django_db
def test_lessons_total_stores_value(dir_and_teacher):
    """Заданное число сохраняется как есть."""
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_value__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        lessons_total=2, created_at='2026-07-27T00:00:00+03:00',
    )
    g.refresh_from_db()
    assert g.lessons_total == 2


@pytest.mark.django_db
def test_lessons_total_rejects_zero(dir_and_teacher):
    """Ноль уроков бессмыслен — запрещён CHECK-констрейнтом в БД."""
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    with pytest.raises(IntegrityError):
        with transaction.atomic():
            Group.objects.create(
                name='__lt_zero__', direction_id=direction_id, teacher_id=teacher_id,
                is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
                lessons_total=0, created_at='2026-07-27T00:00:00+03:00',
            )


@pytest.mark.django_db
def test_progress_grid_uses_group_lessons_total(dir_and_teacher):
    """Сетка «Прогресс» рисует клетки по длине группы, а не направления."""
    from apps.groups.repository import get_group_progress
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_grid__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        lessons_total=2, created_at='2026-07-27T00:00:00+03:00',
    )

    progress = get_group_progress(g.id)

    # Направление — 8 уроков, группа — 2: клеток должно быть 2.
    assert progress['total_slots'] == 2
    assert len(progress['slots']) == 2


@pytest.mark.django_db
def test_progress_grid_half_lesson(dir_and_teacher):
    """45 мин: 2 урока группы = 4 клетки сетки."""
    from apps.groups.repository import get_group_progress
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_grid_half__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=45, lessons_per_week=1,
        lessons_total=2, created_at='2026-07-27T00:00:00+03:00',
    )

    progress = get_group_progress(g.id)

    assert progress['total_slots'] == 4
    assert len(progress['slots']) == 4


@pytest.mark.django_db
def test_api_create_group_with_lessons_total(admin_client, dir_and_teacher):
    """POST принимает число уроков и возвращает его в ответе."""
    direction_id, teacher_id = dir_and_teacher
    resp = admin_client.post('/api/admin/groups', {
        'name': '__lt_api_create__',
        'direction_id': direction_id,
        'teacher_id': teacher_id,
        'is_individual': True,
        'lesson_duration_minutes': 90,
        'lessons_per_week': 1,
        'lessons_total': 2,
        'slots': [],
    }, format='json')

    assert resp.status_code == 201, resp.content
    assert resp.json()['lessons_total'] == 2


@pytest.mark.django_db
def test_api_patch_lessons_total_and_reset(admin_client, dir_and_teacher):
    """PATCH задаёт число и умеет сбросить его обратно в «как в направлении»."""
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_api_patch__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        created_at='2026-07-27T00:00:00+03:00',
    )

    resp = admin_client.patch(f'/api/admin/groups/{g.id}', {'lessons_total': 3}, format='json')
    assert resp.status_code == 200, resp.content
    assert resp.json()['lessons_total'] == 3

    resp = admin_client.patch(f'/api/admin/groups/{g.id}', {'lessons_total': None}, format='json')
    assert resp.status_code == 200, resp.content
    assert resp.json()['lessons_total'] is None


@pytest.mark.django_db
def test_api_rejects_zero_lessons_total(admin_client, dir_and_teacher):
    """Ноль уроков отклоняется валидацией, а не падает на CHECK."""
    from apps.groups.models import Group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_api_zero__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        created_at='2026-07-27T00:00:00+03:00',
    )

    resp = admin_client.patch(f'/api/admin/groups/{g.id}', {'lessons_total': 0}, format='json')

    assert resp.status_code == 400


@pytest.mark.django_db
def test_api_patch_shrinks_plan(admin_client, dir_and_teacher):
    """Правка числа через API подгоняет план (не только сохраняет поле)."""
    from apps.groups.models import Group
    from apps.scheduling.models import PlannedLesson
    from apps.scheduling.repository import generate_for_group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_api_plan__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        group_start_date=datetime.date(2026, 8, 3),
        created_at='2026-07-27T00:00:00+03:00',
    )
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id, day_of_week, start_time, effective_from) "
            "VALUES (%s, 1, TIME '10:00', DATE '2000-01-01')", [g.id])
    generate_for_group(g.id)
    assert PlannedLesson.objects.filter(group_id=g.id, seq__isnull=False).count() == 8

    resp = admin_client.patch(f'/api/admin/groups/{g.id}', {'lessons_total': 3}, format='json')

    assert resp.status_code == 200, resp.content
    assert PlannedLesson.objects.filter(group_id=g.id, seq__isnull=False).count() == 3


@pytest.mark.django_db
def test_api_patch_conflict_on_recorded(admin_client, dir_and_teacher):
    """Урезать план короче проведённых занятий — 409, поле не сохраняется."""
    from apps.groups.models import Group
    from apps.scheduling.models import PlannedLesson
    from apps.scheduling.repository import generate_for_group
    direction_id, teacher_id = dir_and_teacher
    g = Group.objects.create(
        name='__lt_api_conflict__', direction_id=direction_id, teacher_id=teacher_id,
        is_individual=True, lesson_duration_minutes=90, lessons_per_week=1,
        group_start_date=datetime.date(2026, 8, 3),
        created_at='2026-07-27T00:00:00+03:00',
    )
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id, day_of_week, start_time, effective_from) "
            "VALUES (%s, 1, TIME '10:00', DATE '2000-01-01')", [g.id])
    generate_for_group(g.id)
    PlannedLesson.objects.filter(group_id=g.id, seq__in=[1, 2, 3]).update(status='done')

    resp = admin_client.patch(f'/api/admin/groups/{g.id}', {'lessons_total': 2}, format='json')

    assert resp.status_code == 409, resp.content
    g.refresh_from_db()
    assert g.lessons_total is None  # откат транзакции: поле не сохранилось
    assert PlannedLesson.objects.filter(group_id=g.id, seq__isnull=False).count() == 8


def test_changelog_label_for_lessons_total():
    """Поле подписано по-русски — иначе в журнале изменений будет сырое имя колонки."""
    from apps.changelog.summary import FIELD_RU
    assert FIELD_RU['lessons_total'] == 'уроков в группе'
