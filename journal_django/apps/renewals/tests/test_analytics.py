import pytest

from apps.renewals import analytics, engine


@pytest.mark.django_db
def test_analytics_shape(manager_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    resp = manager_client.get('/api/admin/renewals/analytics')
    assert resp.status_code == 200
    body = resp.json()
    assert 'stages' in body and 'renewal_rate_30d' in body
    assert 'won_30d' in body and 'lost_30d' in body


@pytest.mark.django_db
def test_analytics_counts_open_deal_in_progress_stage(manager_client, make_student, make_direction):
    sid, did = make_student(), make_direction()
    engine.ensure_deal(sid, cycle_no=1)
    body = manager_client.get('/api/admin/renewals/analytics').json()
    progress = next(s for s in body['stages'] if s['kind'] == 'progress')
    assert progress['cnt'] >= 1


@pytest.mark.django_db
def test_analytics_requires_staff(teacher_client):
    assert teacher_client.get('/api/admin/renewals/analytics').status_code == 403


@pytest.mark.django_db
def test_funnel_group_by_month(make_student, make_direction, make_teacher,
                               make_payment, make_attendance):
    """Когорты по месяцам: созревший в этом месяце цикл виден в строке месяца."""
    from django.db import connection
    from apps.renewals import engine
    sid, did, tid = make_student(), make_direction(), make_teacher()
    make_payment(sid, did, lessons=8)
    with connection.cursor() as cur:
        cur.execute("INSERT INTO groups (name, direction_id, teacher_id, is_individual, active, created_at, "
                    "lesson_number_offset) "
                    "VALUES ('__an_group__', %s, %s, false, true, now(), 0) RETURNING id", [did, tid])
        gid = cur.fetchone()[0]
        cur.execute("INSERT INTO group_memberships (group_id, student_id, lessons_done, active) "
                    "VALUES (%s,%s,0,true)", [gid, sid])
    try:
        make_attendance(sid, gid, tid, count=4)
        engine.ensure_deal(sid, cycle_no=1)
        engine.sync_lesson_stage(sid)  # цикл отработан → due_at = сегодня
        data = analytics.funnel(group_by='month')
        assert 'months' in data and len(data['months']) >= 1
        row = data['months'][0]
        assert {'month', 'matured', 'won', 'lost', 'in_progress', 'conversion'} <= set(row)
        assert row['matured'] >= 1 and row['in_progress'] >= 1
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
            cur.execute('DELETE FROM group_memberships WHERE group_id = %s', [gid])
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])


@pytest.mark.django_db
def test_funnel_month_uses_moscow_timezone_for_outcome_at(make_student, make_direction):
    """Сделка, закрытая в 01:00 по Москве 1 августа (= 22:00 UTC 31 июля),
    должна попасть в когорту августа — PostgreSQL-сессия сидит в UTC (Django
    её не переключает), поэтому date_trunc('month', outcome_at) обязан явно
    конвертировать в МСК, иначе событие уезжает в когорту июля."""
    from datetime import datetime, timezone as dt_timezone
    from django.db import connection
    from apps.renewals.models import RenewalDeal, RenewalStage

    sid, did = make_student(), make_direction()
    deal = engine.ensure_deal(sid, cycle_no=1)
    lost_stage = RenewalStage.objects.filter(pipeline=deal.pipeline, kind='lost').first()
    utc_instant = datetime(2026, 7, 31, 22, 0, tzinfo=dt_timezone.utc)
    RenewalDeal.objects.filter(id=deal.id).update(stage=lost_stage, outcome_at=utc_instant)
    try:
        data = analytics.funnel(group_by='month')
        row = next((m for m in data['months'] if m['month'] == '2026-08'), None)
        assert row is not None, 'сделка должна попасть в когорту 2026-08, а не 2026-07'
        assert row['lost'] >= 1
    finally:
        with connection.cursor() as cur:
            cur.execute('DELETE FROM renewal_activity WHERE deal_id IN '
                        '(SELECT id FROM renewal_deal WHERE student_id = %s)', [sid])
            cur.execute('DELETE FROM renewal_deal WHERE student_id = %s', [sid])
