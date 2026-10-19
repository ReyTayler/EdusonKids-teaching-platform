"""
API-тесты GET /api/admin/calendar (role=manager/admin/superadmin) — тот же
build_calendar(), что и teacher-эндпоинт (см. test_calendar_api.py), но
teacher_id передаётся явно параметром запроса вместо request.user.teacher_id.
"""
from __future__ import annotations

import pytest

from apps.scheduling import repository

pytestmark = pytest.mark.django_db

WIN = '&from=2026-06-01&to=2026-06-30'


class TestAuth:
    def test_no_cookie_401(self, anon_client, sched_setup):
        url = f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}{WIN}"
        assert anon_client.get(url).status_code == 401

    def test_teacher_role_403(self, teacher_client, sched_setup):
        url = f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}{WIN}"
        assert teacher_client.get(url).status_code == 403

    def test_manager_ok(self, manager_client, sched_setup):
        url = f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}{WIN}"
        assert manager_client.get(url).status_code == 200

    def test_admin_ok(self, admin_client, sched_setup):
        url = f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}{WIN}"
        assert admin_client.get(url).status_code == 200

    def test_superadmin_ok(self, superadmin_client, sched_setup):
        url = f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}{WIN}"
        assert superadmin_client.get(url).status_code == 200


class TestValidation:
    def test_missing_teacher_id_returns_all_teachers(self, manager_client, sched_setup):
        """Без teacher_id — занятия ВСЕХ преподавателей (вся школа), 200."""
        s = sched_setup
        repository.generate_for_group(s['group_a'])
        repository.generate_for_group(s['group_b'])
        resp = manager_client.get('/api/admin/calendar?from=2026-06-01&to=2026-06-30')
        assert resp.status_code == 200
        groups = {o['group'] for o in resp.json()['occurrences']}
        assert '__sched_group_A__' in groups
        assert '__sched_group_B__' in groups

    def test_non_numeric_teacher_id_400(self, manager_client):
        resp = manager_client.get(
            '/api/admin/calendar?teacher_id=abc&from=2026-06-01&to=2026-06-30',
        )
        assert resp.status_code == 400

    def test_missing_window_400(self, manager_client, sched_setup):
        resp = manager_client.get(f"/api/admin/calendar?teacher_id={sched_setup['teacher_a']}")
        assert resp.status_code == 400

    def test_negative_teacher_id_400(self, manager_client):
        resp = manager_client.get(f'/api/admin/calendar?teacher_id=-5{WIN}')
        assert resp.status_code == 400

    def test_out_of_range_teacher_id_400(self, manager_client):
        """teacher_id за пределами PostgreSQL int4 → 400, а не 500 от БД."""
        resp = manager_client.get(f'/api/admin/calendar?teacher_id=99999999999999{WIN}')
        assert resp.status_code == 400


class TestCalendar:
    def test_returns_only_selected_teacher(self, manager_client, sched_setup):
        s = sched_setup
        repository.generate_for_group(s['group_a'])
        repository.generate_for_group(s['group_b'])

        body = manager_client.get(f"/api/admin/calendar?teacher_id={s['teacher_a']}{WIN}").json()
        groups = {o['group'] for o in body['occurrences']}
        assert '__sched_group_A__' in groups
        assert '__sched_group_B__' not in groups

    def test_occurrence_has_group_id(self, manager_client, sched_setup):
        s = sched_setup
        repository.generate_for_group(s['group_a'])

        body = manager_client.get(f"/api/admin/calendar?teacher_id={s['teacher_a']}{WIN}").json()
        assert body['occurrences'][0]['groupId'] == s['group_a']
