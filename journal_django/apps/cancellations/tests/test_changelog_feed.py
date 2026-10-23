"""Журнал изменений для отмен уроков и причин (B7 плана) — сквозь реальный API.

Флаг компенсации — часть мотивации (В3): ленту /api/admin/changelog читает и
admin, поэтому в ней нет ни флага, ни слова «компенсация»; manager ленту не видит
вовсе. Детали операции (модалка «было/стало») — только superadmin, и ему флаг
виден, как суммы ставок.
"""
from __future__ import annotations

import datetime
import json

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

_LEAKS = ('компенс', 'compensat')


@pytest.fixture
def cgroup(db, payroll_rates):
    """Группа с планом из двух вторничных занятий + ставки (начисление не 0)."""
    from apps.cancellations.models import LessonCancellation
    from apps.directions.models import Direction
    from apps.groups.models import Group, GroupScheduleSlot
    from apps.scheduling.models import PlannedLesson
    from apps.teachers.models import Teacher

    now = timezone.now()
    direction = Direction.objects.create(name='Тест Журнал Отмен Направление', total_lessons=2)
    teacher = Teacher.objects.create(name='Тест Журнал Отмен Преподаватель', created_at=now)
    group = Group.objects.create(
        name='ТЖО1', direction=direction, teacher=teacher, is_individual=False,
        lesson_duration_minutes=60, lessons_per_week=1, active=True,
        lesson_number_offset=0, created_at=now,
    )
    GroupScheduleSlot.objects.create(group=group, day_of_week=2,
                                     start_time=datetime.time(18, 0),
                                     effective_from=datetime.date(2026, 7, 1))
    rows = [
        PlannedLesson.objects.create(
            group=group, seq=i, lesson_number=i, scheduled_date=d,
            scheduled_time=datetime.time(18, 0), teacher=teacher, status='pending',
            created_at=now, updated_at=now)
        for i, d in enumerate([datetime.date(2026, 7, 7), datetime.date(2026, 7, 14)], start=1)
    ]
    yield {'group': group, 'rows': rows}
    # Запись ссылается на учётку автора, а клиентская фикстура удаляет учётку
    # сырым DELETE — поэтому клиентов тест запрашивает РАНЬШЕ cgroup.
    LessonCancellation.objects.filter(group=group).delete()


def _feed(client) -> list[dict]:
    resp = client.get('/api/admin/changelog', {'page_size': 50})
    assert resp.status_code == 200, resp.content
    return resp.json()['rows']


def _assert_no_leak(payload) -> None:
    text = json.dumps(payload, ensure_ascii=False).lower()
    for word in _LEAKS:
        assert word not in text, (word, text[:2000])


def test_feed_and_details_hide_compensation_except_superadmin(
        manager_client, admin_client, superadmin_client, cgroup):
    base = '/api/admin/cancellation-reasons'
    reason = superadmin_client.post(base, {'side': 'client', 'name': 'Заболел',
                                           'compensation': True}, format='json').json()
    assert superadmin_client.patch(f'{base}/{reason["id"]}', {'compensation': False},
                                   format='json').status_code == 200
    spare = superadmin_client.post(base, {'side': 'teacher', 'name': 'Болезнь'},
                                   format='json').json()
    assert superadmin_client.delete(f'{base}/{spare["id"]}').status_code == 204

    group, row = cgroup['group'], cgroup['rows'][0]
    resp = manager_client.post(
        f'/api/admin/groups/{group.id}/plan/{row.id}/cancel',
        {'lesson_date': '2026-07-07', 'side': 'client', 'reason_id': reason['id'],
         'notified_on': '2026-07-07'}, format='json')
    assert resp.status_code == 200, resp.content

    # --- Лента admin: человеческие фразы, без флага и без оплаты ---
    rows = _feed(admin_client)
    by_op: dict[str, list[str]] = {}
    for r in rows:
        by_op.setdefault(r['operation'], []).append(r['summary'])
    assert by_op['plan.cancel'] == ['Отменено занятие ТЖО1 07.07.2026 18:00 — Клиент: Заболел']
    assert by_op['cancellation_reason.update'] == ['Изменена причина отмены «Заболел» (Клиент)']
    assert by_op['cancellation_reason.delete'] == [
        'Причина отмены «Болезнь» (Преподаватель) удалена']
    assert 'Новая причина отмены «Заболел» (Клиент)' in by_op['cancellation_reason.create']
    _assert_no_leak(rows)
    assert 'начислена' not in json.dumps(rows, ensure_ascii=False)

    # --- manager ленту не видит; детали — ни manager, ни admin ---
    assert manager_client.get('/api/admin/changelog').status_code == 403
    for r in rows:
        if r['operation'] in ('plan.cancel', 'cancellation_reason.update'):
            for client in (manager_client, admin_client):
                assert client.get(f'/api/admin/changelog/{r["id"]}').status_code == 403

    # --- superadmin: лента та же (без флага), детали — с флагом. Флаг причины
    # выключили до отмены, поэтому снимок записи — «нет». ---
    _assert_no_leak(_feed(superadmin_client))
    ids = {r['operation']: r['id'] for r in rows}
    cancel = superadmin_client.get(f'/api/admin/changelog/{ids["plan.cancel"]}').json()
    record = next(e for e in cancel['events'] if e['entity'] == 'lesson_cancellation')
    assert record['description'] == (
        'Отменено занятие ТЖО1 07.07.2026 18:00 — Клиент: Заболел; компенсация: нет')
    assert {'label': 'компенсация', 'old': None, 'new': 'нет'} in record['human']['changes']
    flag = superadmin_client.get(
        f'/api/admin/changelog/{ids["cancellation_reason.update"]}').json()
    assert flag['events'][0]['description'] == (
        'Причина отмены «Заболел» (Клиент): компенсация: да → нет')
