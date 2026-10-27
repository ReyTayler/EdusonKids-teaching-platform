"""
API выключателей (GET/POST /api/admin/notifications/toggle) — флаг родительских
напоминаний, вкладка «Расписание», команда send_reminders, расписание beat.
Письма преподавателям убраны 2026-10-27 — здесь же проверяется, что их нет.
"""
from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from apps.notifications.models import NotificationSettings

URL = '/api/admin/notifications/toggle'


@pytest.mark.django_db
def test_get_reports_parent_flag_off_by_default(admin_client):
    NotificationSettings.objects.all().delete()
    data = admin_client.get(URL).data
    assert data['is_enabled'] is True
    assert data['parent_reminders_enabled'] is False


@pytest.mark.django_db
def test_parent_flag_can_be_switched_alone(admin_client):
    resp = admin_client.post(URL, {'parent_reminders_enabled': True}, format='json')
    assert resp.status_code == 200
    assert resp.data['parent_reminders_enabled'] is True
    assert resp.data['is_enabled'] is True          # общий выключатель не тронут
    assert resp.data['updated_by']                   # подпись «кто переключил»
    row = NotificationSettings.load()
    assert row.parent_reminders_enabled is True and row.is_enabled is True


@pytest.mark.django_db
def test_switching_is_enabled_keeps_parent_flag(admin_client):
    admin_client.post(URL, {'parent_reminders_enabled': True}, format='json')
    admin_client.post(URL, {'is_enabled': False}, format='json')
    row = NotificationSettings.load()
    assert (row.is_enabled, row.parent_reminders_enabled) == (False, True)


@pytest.mark.django_db
@pytest.mark.parametrize('body', [
    {'parent_reminders_enabled': 'true'},
    {'parent_reminders_enabled': 1},
    {},
])
def test_rejects_non_boolean_and_empty_body(admin_client, body):
    assert admin_client.post(URL, body, format='json').status_code == 400
    assert NotificationSettings.load().parent_reminders_enabled is False


@pytest.mark.django_db
def test_manager_cannot_switch_parent_reminders(manager_client):
    resp = manager_client.post(URL, {'parent_reminders_enabled': True}, format='json')
    assert resp.status_code == 403
    assert NotificationSettings.load().parent_reminders_enabled is False


@pytest.mark.django_db
def test_schedule_tab_lists_only_parent_email_jobs(admin_client):
    """Письма преподавателям убраны (2026-10-27): во вкладке — только родительские."""
    jobs = admin_client.get('/api/admin/notifications/schedule').data['jobs']
    keys = [j['key'] for j in jobs]
    assert {'parent_day_before', 'parent_hour_before'} <= set(keys)
    assert 'lesson_unrecorded' not in keys


@pytest.mark.django_db
def test_send_reminders_command_runs_job_and_optionally_dispatches():
    out = StringIO()
    with patch('apps.notifications.management.commands.send_reminders.JOBS',
               {'parent-day': lambda: 3}), \
            patch('apps.notifications.management.commands.send_reminders.dispatcher.dispatch',
                  return_value=2) as dispatch:
        call_command('send_reminders', 'parent-day', stdout=out)
        dispatch.assert_not_called()
        call_command('send_reminders', 'parent-day', '--dispatch', stdout=out)
        dispatch.assert_called_once()
    assert 'Поставлено в очередь: 3.' in out.getvalue()
    assert 'Отправлено: 2.' in out.getvalue()


def test_send_reminders_command_offers_only_parent_jobs():
    from apps.notifications.management.commands.send_reminders import JOBS
    assert set(JOBS) == {'parent-day', 'parent-hour'}
    # Преподавательские письма убраны: старые имена команд отвергаются, а не
    # молча ничего не делают.
    for job in ('schedule', 'unrecorded'):
        with pytest.raises(CommandError):
            call_command('send_reminders', job, stdout=StringIO())


def test_beat_schedule_has_only_parent_email_reminders():
    pytest.importorskip('celery')
    beat = settings.CELERY_BEAT_SCHEDULE
    assert 'notifications-email-teacher-schedule' not in beat
    assert 'notifications-email-unrecorded' not in beat
    expected = {
        'notifications-email-parent-day-before': 'send_parent_day_before',
        'notifications-email-parent-hour-before': 'send_parent_hour_before',
    }
    for key, task in expected.items():
        assert beat[key]['task'] == f'apps.notifications.tasks.{task}'
    assert beat['notifications-email-parent-day-before']['schedule'].hour == {10}
    assert len(beat['notifications-email-parent-hour-before']['schedule'].minute) == 12
    # Ни одна задача beat не ссылается на удалённые преподавательские письма.
    tasks = {entry['task'] for entry in beat.values()}
    assert not tasks & {
        'apps.notifications.tasks.send_teacher_schedule_email',
        'apps.notifications.tasks.send_unrecorded_reminders',
    }
