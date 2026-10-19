"""Ручная правка даты закрытия сделки (admin/superadmin).

Сделку часто закрывают позже, чем ученик реально ушёл или продлил, а аналитика и
«Переходимость» относят событие к МЕСЯЦУ outcome_at — без правки отчёт за июнь
молча уезжает в июль.
"""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone

from apps.core.utils.dates import MSK
from apps.renewals.models import RenewalDeal, RenewalStage

URL = '/api/admin/renewals/{}/outcome-date'


@pytest.fixture
def closed_deal(db, make_student):
    """Сделка, закрытая как «Ушёл» сегодня."""
    sid = make_student('__outcome_date_stud__')
    churned = RenewalStage.objects.get(pipeline__is_default=True, key='churned')
    return RenewalDeal.objects.create(
        student_id=sid, cycle_no=1, pipeline_id=churned.pipeline_id,
        stage=churned, outcome_at=timezone.now())


@pytest.fixture
def open_deal(db, make_student):
    sid = make_student('__outcome_date_open_stud__')
    lesson_1 = RenewalStage.objects.get(pipeline__is_default=True, key='lesson_1')
    return RenewalDeal.objects.create(
        student_id=sid, cycle_no=1, pipeline_id=lesson_1.pipeline_id, stage=lesson_1)


@pytest.mark.django_db
def test_admin_sets_outcome_date(admin_client, closed_deal):
    resp = admin_client.patch(URL.format(closed_deal.id),
                              {'outcome_date': '2026-06-15'}, format='json')
    assert resp.status_code == 200, resp.json()
    closed_deal.refresh_from_db()
    assert closed_deal.outcome_at.astimezone(MSK).date() == datetime.date(2026, 6, 15)


@pytest.mark.django_db
def test_stored_time_survives_msk_conversion(admin_client, closed_deal):
    """Отчёты берут дату как `outcome_at AT TIME ZONE 'Europe/Moscow'` — день
    обязан совпасть с выбранным, а не уехать на сутки назад."""
    admin_client.patch(URL.format(closed_deal.id),
                       {'outcome_date': '2026-01-01'}, format='json')
    closed_deal.refresh_from_db()
    assert closed_deal.outcome_at.astimezone(MSK).date() == datetime.date(2026, 1, 1)
    assert closed_deal.outcome_at.astimezone(MSK).hour == 12


@pytest.mark.django_db
def test_stage_and_outcome_flag_untouched(admin_client, closed_deal):
    """Меняется КОГДА закрыли, а не ЧЕМ закончилось: стадия та же, сделка закрыта."""
    before_stage = closed_deal.stage_id
    admin_client.patch(URL.format(closed_deal.id),
                       {'outcome_date': '2026-06-15'}, format='json')
    closed_deal.refresh_from_db()
    assert closed_deal.stage_id == before_stage
    assert closed_deal.outcome_at is not None


@pytest.mark.django_db
def test_change_is_visible_in_timeline(admin_client, closed_deal):
    """Правка задним числом обязана оставлять след в таймлайне сделки."""
    admin_client.patch(URL.format(closed_deal.id),
                       {'outcome_date': '2026-06-15'}, format='json')
    body = (closed_deal.activities.filter(kind='system')
            .order_by('-created_at').first().body)
    assert '2026-06-15' in body


@pytest.mark.django_db
def test_future_date_rejected(admin_client, closed_deal):
    future = (datetime.date.today() + datetime.timedelta(days=3)).isoformat()
    resp = admin_client.patch(URL.format(closed_deal.id),
                              {'outcome_date': future}, format='json')
    assert resp.status_code == 400
    assert 'outcome_date' in resp.json()['details']


@pytest.mark.django_db
def test_open_deal_rejected(admin_client, open_deal):
    """У открытой сделки даты закрытия нет — 409, а не тихое проставление."""
    resp = admin_client.patch(URL.format(open_deal.id),
                              {'outcome_date': '2026-06-15'}, format='json')
    assert resp.status_code == 409
    open_deal.refresh_from_db()
    assert open_deal.outcome_at is None


@pytest.mark.django_db
def test_manager_forbidden(manager_client, closed_deal):
    """Правка двигает отчётность — менеджеру, ведущему сделку, она недоступна."""
    before = closed_deal.outcome_at
    resp = manager_client.patch(URL.format(closed_deal.id),
                                {'outcome_date': '2026-06-15'}, format='json')
    assert resp.status_code == 403
    closed_deal.refresh_from_db()
    assert closed_deal.outcome_at == before


@pytest.mark.django_db
def test_teacher_forbidden(teacher_client, closed_deal):
    resp = teacher_client.patch(URL.format(closed_deal.id),
                                {'outcome_date': '2026-06-15'}, format='json')
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_missing_deal_is_404(admin_client):
    resp = admin_client.patch(URL.format(0), {'outcome_date': '2026-06-15'}, format='json')
    assert resp.status_code == 404


@pytest.mark.django_db
def test_operation_label_resolves():
    """Новый мутирующий URL обязан иметь метку в журнале изменений."""
    from apps.changelog import labels
    assert labels.resolve_operation(
        'PATCH', '/api/admin/renewals/7/outcome-date') == 'renewal.outcome_date_update'
    # generic-правило PATCH /renewals/<id> не должно перехватывать этот путь
    assert labels.resolve_operation('PATCH', '/api/admin/renewals/7') == 'renewal.update'
