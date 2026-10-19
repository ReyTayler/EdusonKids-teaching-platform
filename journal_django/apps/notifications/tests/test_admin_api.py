"""Раздел «Уведомления»: доступ, фильтры, пагинация, вкладка «Расписание»."""
from __future__ import annotations

import pytest

from apps.notifications.constants import (
    CHANNEL_DM, CHANNEL_GROUP, KIND_FILL_DIGEST, KIND_MORNING_DIGEST,
    STATUS_QUEUED, STATUS_SENT,
)
from apps.notifications.models import NotificationMessage

LIST_URL = '/api/admin/notifications'
SCHEDULE_URL = '/api/admin/notifications/schedule'


@pytest.fixture
def messages_fixture(db):
    NotificationMessage.objects.create(
        kind=KIND_MORNING_DIGEST, channel=CHANNEL_DM, chat_id=1,
        text='утро', dedup_key='m1', status=STATUS_SENT)
    NotificationMessage.objects.create(
        kind=KIND_FILL_DIGEST, channel=CHANNEL_DM, chat_id=1,
        text='вечер', dedup_key='f1', status=STATUS_QUEUED)
    NotificationMessage.objects.create(
        kind=KIND_MORNING_DIGEST, channel=CHANNEL_GROUP, chat_id=-100,
        text='чат', dedup_key='g1', status=STATUS_SENT)


@pytest.mark.django_db
def test_manager_is_denied(manager_client, messages_fixture):
    """Раздел системный — как «Журнал изменений», для manager закрыт."""
    assert manager_client.get(LIST_URL).status_code == 403


@pytest.mark.django_db
def test_admin_sees_paginated_envelope(admin_client, messages_fixture):
    response = admin_client.get(LIST_URL)
    assert response.status_code == 200
    assert set(response.data.keys()) >= {'rows', 'total', 'page', 'page_size'}
    assert response.data['total'] == 3


@pytest.mark.django_db
def test_filter_by_kind(admin_client, messages_fixture):
    response = admin_client.get(f'{LIST_URL}?kind={KIND_FILL_DIGEST}')
    assert response.data['total'] == 1


@pytest.mark.django_db
def test_filter_by_channel_and_status(admin_client, messages_fixture):
    assert admin_client.get(f'{LIST_URL}?channel={CHANNEL_GROUP}').data['total'] == 1
    assert admin_client.get(f'{LIST_URL}?status={STATUS_QUEUED}').data['total'] == 1


@pytest.mark.django_db
def test_newest_first(admin_client, messages_fixture):
    rows = admin_client.get(LIST_URL).data['rows']
    dates = [r['created_at'] for r in rows]
    assert dates == sorted(dates, reverse=True)


@pytest.mark.django_db
def test_schedule_tab_reports_jobs(admin_client, messages_fixture):
    response = admin_client.get(SCHEDULE_URL)
    assert response.status_code == 200
    jobs = {j['key'] for j in response.data['jobs']}
    assert {'morning_digest', 'fill_digest', 'dispatch'} <= jobs
    morning = next(j for j in response.data['jobs'] if j['key'] == 'morning_digest')
    assert morning['schedule'] == '08:00'
    assert 'last_run_at' in morning
