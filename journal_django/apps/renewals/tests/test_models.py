"""Проверяем, что модели renewals объявлены и корректно связаны."""
from __future__ import annotations

from django.db import models

from apps.renewals.models import RenewalActivity, RenewalDeal, RenewalPipeline, RenewalStage


def test_tables_named_as_expected():
    assert RenewalPipeline._meta.db_table == 'renewal_pipeline'
    assert RenewalStage._meta.db_table == 'renewal_stage'
    assert RenewalDeal._meta.db_table == 'renewal_deal'
    assert RenewalActivity._meta.db_table == 'renewal_activity'


def test_deal_has_student_cycle_unique_constraint():
    """Сделка — сущность ученика: одна на (student, cycle_no), направления нет."""
    names = {c.name for c in RenewalDeal._meta.constraints}
    assert 'renewal_deal_student_cycle_uq' in names


def test_stage_kinds():
    assert set(RenewalStage.Kind.values) == {'progress', 'decision', 'won', 'lost'}


def test_deal_fks_use_restrict_not_cascade():
    """Историю продлений нельзя терять при удалении ученика/стадии — RESTRICT, не CASCADE."""
    for field_name in ('student', 'pipeline', 'stage'):
        field = RenewalDeal._meta.get_field(field_name)
        assert field.remote_field.on_delete is models.RESTRICT, field_name
