"""
API-тесты заморозки группы (этап 3 Eduson, раздел C):
  POST /api/admin/groups/<pk>/plan/freeze

Заморозка — частный случай «Изменить расписание» (repository.permanent_change):
from_seq = первая курсовая pending/overdue позиция с датой ≥ freeze_from,
effective_from = resume_date, new_slots = текущие действующие слоты группы.
Число уроков и набор слотов не меняются, сдвигается только хвост.

Фикстура: старт 2026-06-01 (Пн), слот Пн 10:00, 8 уроков по 60 мин →
план 06-01, 06-08, 06-15, 06-22, 06-29, 07-06, 07-13, 07-20 (seq 1..8).
"""
from __future__ import annotations

import pytest
from django.db import connection

from apps.groups.models import GroupScheduleSlot
from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db


@pytest.fixture
def frz_group(db):
    with connection.cursor() as cur:
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__frz_A__', true) RETURNING id")
        teacher_a = cur.fetchone()[0]
        cur.execute("INSERT INTO teachers (name, active) VALUES ('__frz_B__', true) RETURNING id")
        teacher_b = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO directions (name,total_lessons,color,active) "
            "VALUES ('__frz_dir__',8,'#4F59F9',true) RETURNING id"
        )
        direction_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO groups (name,direction_id,teacher_id,is_individual,lesson_duration_minutes,"
            "group_start_date,active,lesson_number_offset,lessons_per_week) "
            "VALUES ('__frz_g__',%s,%s,false,60,'2026-06-01',true,0,1) RETURNING id",
            [direction_id, teacher_a],
        )
        group_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
            "VALUES (%s,1,'10:00','2026-06-01')",  # Пн (Вс=0)
            [group_id],
        )

    yield {'group_id': group_id, 'teacher_a': teacher_a, 'teacher_b': teacher_b}

    with connection.cursor() as cur:
        cur.execute('DELETE FROM planned_lessons WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM group_schedule_slots WHERE group_id = %s', [group_id])
        cur.execute('DELETE FROM groups WHERE id = %s', [group_id])
        cur.execute('DELETE FROM directions WHERE id = %s', [direction_id])
        cur.execute('DELETE FROM teachers WHERE id IN (%s,%s)', [teacher_a, teacher_b])


def _url(gid):
    return f'/api/admin/groups/{gid}/plan/freeze'


def _generate(client, gid):
    resp = client.post(f'/api/admin/groups/{gid}/plan/generate', {}, format='json')
    assert resp.status_code == 200, resp.content
    return resp.json()


def _by_seq(plan):
    return {r['seq']: r for r in plan if r['seq'] is not None}


def _open_slots(gid):
    return sorted(
        (s.day_of_week, s.start_time.strftime('%H:%M'))
        for s in GroupScheduleSlot.objects.filter(group_id=gid, effective_to__isnull=True)
    )


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------

class TestRBAC:
    def test_anon_401(self, anon_client, frz_group):
        resp = anon_client.post(_url(frz_group['group_id']), {}, format='json')
        assert resp.status_code == 401

    def test_teacher_403(self, teacher_client, frz_group):
        resp = teacher_client.post(
            _url(frz_group['group_id']),
            {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 403
        # и ничего не сдвинуто
        assert not PlannedLesson.objects.filter(group_id=frz_group['group_id']).exists()


# ---------------------------------------------------------------------------
# Сдвиг хвоста
# ---------------------------------------------------------------------------

class TestFreeze:
    def test_shifts_tail_keeps_count_and_slots(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)

        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())

        # число курсовых уроков не изменилось
        assert sorted(by_seq) == list(range(1, 9))
        assert PlannedLesson.objects.filter(group_id=gid, seq__isnull=False).count() == 8
        # голова (до freeze_from) не тронута
        assert [by_seq[i]['scheduled_date'] for i in (1, 2, 3)] == [
            '2026-06-01', '2026-06-08', '2026-06-15']
        # хвост (первая позиция ≥ 06-20 — seq4) пошёл с resume_date по тем же
        # слотам: первый понедельник не раньше 07-15 — 07-20, дальше каждую неделю
        assert [by_seq[i]['scheduled_date'] for i in (4, 5, 6, 7, 8)] == [
            '2026-07-20', '2026-07-27', '2026-08-03', '2026-08-10', '2026-08-17']
        assert {by_seq[i]['scheduled_time'] for i in range(1, 9)} == {'10:00'}
        # в окне заморозки нет ни одного курсового занятия
        assert not [r for r in by_seq.values()
                    if '2026-06-20' <= r['scheduled_date'] < '2026-07-15']
        # набор действующих слотов тот же; lessons_per_week не изменился
        assert _open_slots(gid) == [(1, '10:00')]
        with connection.cursor() as cur:
            cur.execute('SELECT lessons_per_week, teacher_id FROM groups WHERE id = %s', [gid])
            lpw, teacher_id = cur.fetchone()
        assert lpw == 1
        assert teacher_id == frz_group['teacher_a']

    def test_multi_slot_group_keeps_both_slots(self, manager_client, frz_group):
        gid = frz_group['group_id']
        with connection.cursor() as cur:
            cur.execute(
                "INSERT INTO group_schedule_slots (group_id,day_of_week,start_time,effective_from) "
                "VALUES (%s,4,'18:30','2026-06-01')", [gid])  # + Чт 18:30
        _generate(manager_client, gid)  # 06-01 Пн, 06-04 Чт, 06-08, 06-11, 06-15, 06-18, ...

        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-10', 'resume_date': '2026-07-01'}, format='json')
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())
        assert [(by_seq[i]['scheduled_date'], by_seq[i]['scheduled_time']) for i in (1, 2, 3)] == [
            ('2026-06-01', '10:00'), ('2026-06-04', '18:30'), ('2026-06-08', '10:00')]
        # seq4 (06-11 Чт) — первая ≥ 06-10; с 07-01 (Ср) по Пн 10:00 / Чт 18:30
        assert [(by_seq[i]['scheduled_date'], by_seq[i]['scheduled_time']) for i in range(4, 9)] == [
            ('2026-07-02', '18:30'), ('2026-07-06', '10:00'), ('2026-07-09', '18:30'),
            ('2026-07-13', '10:00'), ('2026-07-16', '18:30')]
        assert _open_slots(gid) == [(1, '10:00'), (4, '18:30')]
        with connection.cursor() as cur:
            cur.execute('SELECT lessons_per_week FROM groups WHERE id = %s', [gid])
            assert cur.fetchone()[0] == 2

    def test_done_rows_not_moved_and_overdue_moved(self, manager_client, frz_group):
        """Проведённая позиция после freeze_from остаётся на месте и не служит
        началом хвоста; overdue (факта нет, время прошло) — переезжает."""
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        PlannedLesson.objects.filter(group_id=gid, seq=4).update(status='done')
        PlannedLesson.objects.filter(group_id=gid, seq=5).update(status='overdue')

        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())
        assert by_seq[4]['scheduled_date'] == '2026-06-22'  # done — на месте
        assert [by_seq[i]['scheduled_date'] for i in (5, 6, 7, 8)] == [
            '2026-07-20', '2026-07-27', '2026-08-03', '2026-08-10']

    def test_resets_one_offs_in_tail(self, manager_client, frz_group):
        """Как у «Изменить расписание»: разовая замена в хвосте сбрасывается."""
        gid = frz_group['group_id']
        plan = _generate(manager_client, gid)
        target = _by_seq(plan)[6]
        r = manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': frz_group['teacher_b']}, format='json')
        assert r.status_code == 200

        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 200
        assert PlannedLesson.objects.get(id=target['id']).substitute_teacher_id is None

    def test_resume_equal_freeze_from_allowed(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-06-20'}, format='json')
        assert resp.status_code == 200, resp.content
        by_seq = _by_seq(resp.json())
        assert by_seq[4]['scheduled_date'] == '2026-06-22'
        assert by_seq[8]['scheduled_date'] == '2026-07-20'


# ---------------------------------------------------------------------------
# Валидация и ошибки
# ---------------------------------------------------------------------------

class TestValidation:
    def test_resume_before_freeze_400(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        before = list(PlannedLesson.objects.filter(group_id=gid)
                      .order_by('seq').values_list('seq', 'scheduled_date'))
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-07-01', 'resume_date': '2026-06-30'}, format='json')
        assert resp.status_code == 400
        assert 'resume_date' in resp.json()['details']
        after = list(PlannedLesson.objects.filter(group_id=gid)
                     .order_by('seq').values_list('seq', 'scheduled_date'))
        assert after == before

    @pytest.mark.parametrize('body', [
        {'resume_date': '2026-07-15'},
        {'freeze_from': '2026-06-20'},
        {'freeze_from': '2026-02-30', 'resume_date': '2026-07-15'},
        {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15', 'from_seq': 3},
    ])
    def test_bad_body_400(self, manager_client, frz_group, body):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(_url(gid), body, format='json')
        assert resp.status_code == 400

    def test_nothing_to_freeze_400(self, manager_client, frz_group):
        """Все позиции раньше freeze_from — замораживать нечего."""
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-08-01', 'resume_date': '2026-09-01'}, format='json')
        assert resp.status_code == 400
        assert 'Нечего замораживать' in resp.json()['error']
        # слоты не версионированы
        assert GroupSlotCount(gid) == 1

    def test_nothing_to_freeze_when_tail_done(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        PlannedLesson.objects.filter(group_id=gid, seq__gte=4).update(status='done')
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 400
        assert 'Нечего замораживать' in resp.json()['error']

    def test_empty_plan_400(self, manager_client, frz_group):
        resp = manager_client.post(
            _url(frz_group['group_id']),
            {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 400

    def test_no_current_slots_400(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        with connection.cursor() as cur:
            cur.execute("UPDATE group_schedule_slots SET effective_to='2026-06-30' "
                        "WHERE group_id=%s", [gid])
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 400
        assert 'расписани' in resp.json()['error']

    def test_archived_group_400_and_plan_untouched(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        before = list(PlannedLesson.objects.filter(group_id=gid)
                      .order_by('id').values_list('id', 'scheduled_date'))
        with connection.cursor() as cur:
            cur.execute('UPDATE groups SET active=false WHERE id=%s', [gid])
        resp = manager_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 400
        assert 'архив' in resp.json()['error']
        after = list(PlannedLesson.objects.filter(group_id=gid)
                     .order_by('id').values_list('id', 'scheduled_date'))
        assert after == before

    def test_missing_group_404(self, manager_client):
        resp = manager_client.post(
            _url(99999999), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'},
            format='json')
        assert resp.status_code == 404


def GroupSlotCount(gid):  # noqa: N802 — читается как утверждение в тесте
    return GroupScheduleSlot.objects.filter(group_id=gid).count()


# ---------------------------------------------------------------------------
# Превью
# ---------------------------------------------------------------------------

class TestPreview:
    def test_preview_reports_tail_and_affected_without_writing(self, manager_client, frz_group):
        gid = frz_group['group_id']
        plan = _generate(manager_client, gid)
        target = _by_seq(plan)[6]
        manager_client.post(
            f'/api/admin/groups/{gid}/plan/{target["id"]}/change-teacher',
            {'new_teacher_id': frz_group['teacher_b']}, format='json')
        plan_before = manager_client.get(f'/api/admin/groups/{gid}/plan').json()

        resp = manager_client.post(
            _url(gid),
            {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15', 'preview': True},
            format='json')
        assert resp.status_code == 200, resp.content
        body = resp.json()
        assert body['from_seq'] == 4
        assert body['lessons_count'] == 5
        assert body['first_date'] == '2026-06-22'
        assert body['resume_first_date'] == '2026-07-20'
        assert body['last_date'] == '2026-08-17'
        assert [a['kind'] for a in body['affected']] == ['substitution']
        assert body['affected'][0]['seq'] == 6

        assert manager_client.get(f'/api/admin/groups/{gid}/plan').json() == plan_before
        assert GroupSlotCount(gid) == 1

    def test_preview_nothing_to_freeze_400(self, manager_client, frz_group):
        gid = frz_group['group_id']
        _generate(manager_client, gid)
        resp = manager_client.post(
            _url(gid),
            {'freeze_from': '2026-08-01', 'resume_date': '2026-09-01', 'preview': True},
            format='json')
        assert resp.status_code == 400
        assert 'Нечего замораживать' in resp.json()['error']

    def test_preview_missing_group_404(self, manager_client):
        resp = manager_client.post(
            _url(99999999),
            {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15', 'preview': True},
            format='json')
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Журнал изменений
# ---------------------------------------------------------------------------

class TestChangelog:
    def test_label_resolves(self):
        from apps.changelog import labels
        assert labels.resolve_operation('POST', '/api/admin/groups/5/plan/freeze') == 'plan.freeze'

    def test_feed_operation_and_summary(self, admin_client, frz_group):
        gid = frz_group['group_id']
        _generate(admin_client, gid)
        resp = admin_client.post(
            _url(gid), {'freeze_from': '2026-06-20', 'resume_date': '2026-07-15'}, format='json')
        assert resp.status_code == 200, resp.content

        rows = admin_client.get(
            '/api/admin/changelog?page_size=5&filter[operation]=plan.freeze').json()['rows']
        assert rows, 'операция заморозки не попала в журнал'
        row = rows[0]
        assert row['operation'] == 'plan.freeze'
        assert row['summary'] == 'Заморозка __frz_g__: занятий перенесено — 5'
