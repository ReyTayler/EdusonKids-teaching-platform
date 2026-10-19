"""Проверяем, что дефолтная воронка и её стадии засеяны data-миграцией."""
from __future__ import annotations

import pytest

from apps.renewals.models import RenewalPipeline, RenewalStage


@pytest.mark.django_db
def test_default_pipeline_seeded():
    pipe = RenewalPipeline.objects.get(is_default=True)
    stages = list(RenewalStage.objects.filter(pipeline=pipe).order_by('sort_order'))
    # Первая по sort_order — «Не было урока» (миграция 0003 разбила
    # lesson_progress на 4 стадии, 0009 переименовала первую из них — см.
    # apps/renewals/tests/test_lesson_progress.py::test_default_pipeline_has_four_lesson_stages).
    assert [s.key for s in stages][0] == 'no_lesson_yet'
    assert {s.kind for s in stages} >= {'progress', 'decision', 'won', 'lost'}
    assert next(s for s in stages if s.key == 'no_lesson_yet').is_auto is True


@pytest.mark.django_db
def test_awaiting_renewal_stage_seeded():
    """Миграция 0005: «Ждём продление» сразу после «Ждём оплату», обе — авто."""
    pipe = RenewalPipeline.objects.get(is_default=True)
    ar = RenewalStage.objects.get(pipeline=pipe, key='awaiting_renewal')
    ap = RenewalStage.objects.get(pipeline=pipe, key='awaiting_payment')
    assert ar.is_auto and ap.is_auto and ar.kind == 'decision'
    assert ar.sort_order == ap.sort_order + 1


@pytest.mark.django_db
def test_frozen_stage_is_manual():
    """Миграция 0012 отменила 0010: «Заморожен» снова обычная ручная стадия.

    0010 пометила её is_auto=True искусственно — только чтобы
    transitions.is_allowed запретил ручной вход, а двигал стадию каскад смены
    статуса ученика. Статусы удалены (спека 2026-07-25), заморозка — обычная
    decision-стадия, которую ставит менеджер.
    """
    pipe = RenewalPipeline.objects.get(is_default=True)
    frozen = RenewalStage.objects.get(pipeline=pipe, key='frozen')
    assert frozen.is_auto is False
    assert frozen.kind == 'decision'
