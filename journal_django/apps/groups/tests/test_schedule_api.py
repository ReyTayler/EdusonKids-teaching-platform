"""
API-тесты admin-редактирования расписания (Ф3):
  GET  /api/admin/groups/:id/schedule
  POST /api/admin/groups/:id/schedule-change

Права: IsManagerOrAdmin. Аутентификация — JWT (root conftest клиенты).
"""
from __future__ import annotations

import pytest
from django.db import connection

pytestmark = pytest.mark.django_db


@pytest.fixture
def sched_group(db):
    """Группа + направление + преподаватель. Возвращает group_id."""
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__sch_t__', true) RETURNING id")
        teacher_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,active) "
            "VALUES ('__sch_d__',8,true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,"
            "group_start_date,active,lesson_number_offset) VALUES ('__sch_g__',%s,%s,false,60,'2026-06-01',true,0) RETURNING id",
            [direction_id, teacher_id],
        )
        group_id = cur.fetchone()[0]
    yield group_id
    with connection.cursor() as cur:
        # planned_lessons: schedule-change теперь авто-генерирует план (Механизм 1),
        # FK на groups — Python-CASCADE (не ON DELETE в БД), чистим детей первыми.
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id = %s', [teacher_id])


class TestAuth:
    def test_schedule_anon_401(self, anon_client, sched_group):
        assert anon_client.get(f'/api/admin/groups/{sched_group}/schedule').status_code == 401

    def test_schedule_teacher_403(self, teacher_client, sched_group):
        assert teacher_client.get(f'/api/admin/groups/{sched_group}/schedule').status_code == 403

    def test_schedule_change_teacher_403(self, teacher_client, sched_group):
        resp = teacher_client.post(
            f'/api/admin/groups/{sched_group}/schedule-change',
            {'effective_from': '2026-06-01', 'slots': [{'day_of_week': 1, 'start_time': '10:00'}]},
            format='json',
        )
        assert resp.status_code == 403


class TestScheduleChange:
    def test_get_empty_schedule(self, manager_client, sched_group):
        resp = manager_client.get(f'/api/admin/groups/{sched_group}/schedule')
        assert resp.status_code == 200
        body = resp.json()
        assert body == {'slots': []}

    def test_apply_and_read(self, manager_client, sched_group):
        resp = manager_client.post(
            f'/api/admin/groups/{sched_group}/schedule-change',
            {'effective_from': '2026-06-01', 'slots': [{'day_of_week': 1, 'start_time': '10:00'}]},
            format='json',
        )
        assert resp.status_code == 200
        slots = resp.json()['slots']
        assert len(slots) == 1
        assert slots[0]['day_of_week'] == 1
        assert slots[0]['start_time'] == '10:00'
        assert slots[0]['effective_from'] == '2026-06-01'
        assert slots[0]['effective_to'] is None

    def test_permanent_change_closes_previous(self, manager_client, sched_group):
        base = f'/api/admin/groups/{sched_group}/schedule-change'
        manager_client.post(base, {'effective_from': '2026-06-01',
                                    'slots': [{'day_of_week': 1, 'start_time': '10:00'}]}, format='json')
        manager_client.post(base, {'effective_from': '2026-07-01',
                                   'slots': [{'day_of_week': 3, 'start_time': '14:00'}]}, format='json')
        body = manager_client.get(f'/api/admin/groups/{sched_group}/schedule').json()
        slots = {(s['day_of_week'], s['start_time']): s for s in body['slots']}
        assert slots[(1, '10:00')]['effective_to'] == '2026-06-30'   # старый закрыт
        assert slots[(3, '14:00')]['effective_from'] == '2026-07-01'  # новый открыт
        assert slots[(3, '14:00')]['effective_to'] is None

    def test_group_detail_shows_only_active_slot(self, manager_client, sched_group):
        """Витрина группы (GET /groups/<id>) показывает только АКТИВНЫЙ сегодня слот,
        а не все версии — иначе после смены расписания группа выглядит занимающейся
        сразу в двух слотах. История версий остаётся в /schedule (экран правки)."""
        base = f'/api/admin/groups/{sched_group}/schedule-change'
        # Старый слот действовал 2026-06-01..06-30, новый — с 2026-07-01. Обе даты в
        # прошлом относительно сегодня → активен только новый (Ср 14:00).
        manager_client.post(base, {'effective_from': '2026-06-01',
                                    'slots': [{'day_of_week': 1, 'start_time': '10:00'}]}, format='json')
        manager_client.post(base, {'effective_from': '2026-07-01',
                                   'slots': [{'day_of_week': 3, 'start_time': '14:00'}]}, format='json')

        detail = manager_client.get(f'/api/admin/groups/{sched_group}').json()
        slots = detail['slots']
        assert len(slots) == 1                       # только активный, не два
        assert slots[0]['day_of_week'] == 3
        assert slots[0]['start_time'] == '14:00:00'

        # Экран редактирования по-прежнему видит ОБЕ версии (история).
        edit = manager_client.get(f'/api/admin/groups/{sched_group}/schedule').json()
        assert len(edit['slots']) == 2

    def test_schedule_change_missing_group_404(self, manager_client):
        resp = manager_client.post(
            '/api/admin/groups/99999999/schedule-change',
            {'effective_from': '2026-06-01', 'slots': [{'day_of_week': 1, 'start_time': '10:00'}]},
            format='json',
        )
        assert resp.status_code == 404

    def test_schedule_change_empty_slots_400(self, manager_client, sched_group):
        resp = manager_client.post(
            f'/api/admin/groups/{sched_group}/schedule-change',
            {'effective_from': '2026-06-01', 'slots': []},
            format='json',
        )
        assert resp.status_code == 400
