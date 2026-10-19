from __future__ import annotations

from datetime import timedelta

import pytest
from django.apps import apps
from django.core.management import call_command
from django.utils import timezone

from apps.directions.models import Direction

pytestmark = pytest.mark.django_db


def test_prune_removes_old_events_keeps_fresh():
    d = Direction.objects.create(name='__chg_prune__')
    ev_model = apps.get_model('directions', 'DirectionEvent')
    ev = ev_model.objects.get(pgh_obj_id=d.id)
    # состарить событие
    ev_model.objects.filter(pk=ev.pk).update(
        pgh_created_at=timezone.now() - timedelta(days=400))

    call_command('prune_changelog', '--keep-months', '12')
    assert not ev_model.objects.filter(pgh_obj_id=d.id).exists()

    d2 = Direction.objects.create(name='__chg_prune_fresh__')
    call_command('prune_changelog', '--keep-months', '12')
    assert ev_model.objects.filter(pgh_obj_id=d2.id).exists()
