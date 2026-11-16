"""Правило обязательности ссылки на запись урока (спека 2026-11-16)."""
from __future__ import annotations

import pytest
from django.db import IntegrityError, transaction

from apps.core.tests.minimal_school import ensure_minimal_school
from apps.lessons.models import (
    RecordUrlRule,
    RecordUrlRuleEvent,
    RecordUrlRuleGroup,
    RecordUrlRuleTeacher,
    RecordUrlRuleTeacherEvent,
)
from apps.lessons.record_url_rule import RuleSnapshot, load_rule, replace_rule

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Модели
# ---------------------------------------------------------------------------

def test_load_creates_single_row_required_by_default():
    rule = RecordUrlRule.load()
    assert rule.pk == 1
    assert rule.required is True
    assert RecordUrlRule.load().pk == 1
    assert RecordUrlRule.objects.count() == 1


def test_exception_links_unique():
    ids = ensure_minimal_school()
    rule = RecordUrlRule.load()
    RecordUrlRuleTeacher.objects.create(rule=rule, teacher_id=ids['teacher_id'])
    RecordUrlRuleGroup.objects.create(rule=rule, group_id=ids['group_id'])
    with pytest.raises(IntegrityError), transaction.atomic():
        RecordUrlRuleTeacher.objects.create(rule=rule, teacher_id=ids['teacher_id'])
    with pytest.raises(IntegrityError), transaction.atomic():
        RecordUrlRuleGroup.objects.create(rule=rule, group_id=ids['group_id'])


def test_changelog_labels_for_rule_entities():
    """Журнал изменений называет сущности правила по-русски (а не ключом реестра):
    и в описании операции, и в причине отказа отката."""
    from apps.changelog.humanize import FORBIDDEN_RU
    from apps.changelog.registry import TRACKED
    from apps.changelog.summary import FIELD_RU, entity_ru

    for name, entity in (('lessons.RecordUrlRule', 'record_url_rule'),
                         ('lessons.RecordUrlRuleTeacher', 'record_url_rule_teacher'),
                         ('lessons.RecordUrlRuleGroup', 'record_url_rule_group')):
        assert TRACKED[name].entity == entity
        assert TRACKED[name].revertable is False
        assert entity_ru(entity)
        assert FORBIDDEN_RU.get(entity)
    assert FIELD_RU['required'] == 'обязательность'


# ---------------------------------------------------------------------------
# Расчёт: правило XOR исключение
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('required, in_group, in_teacher, expected', [
    (True,  False, False, True),
    (True,  True,  False, False),
    (True,  False, True,  False),
    (True,  True,  True,  False),   # одно исключение, а не два гасящих друг друга
    (False, False, False, False),
    (False, True,  False, True),
    (False, False, True,  True),
    (False, True,  True,  True),
])
def test_is_required_matrix(required, in_group, in_teacher, expected):
    snap = RuleSnapshot(
        required=required,
        teacher_ids=frozenset({7}) if in_teacher else frozenset(),
        group_ids=frozenset({3}) if in_group else frozenset(),
    )
    assert snap.is_required(group_id=3, teacher_id=7) is expected


def test_is_required_unknown_teacher_uses_group_only():
    snap = RuleSnapshot(True, frozenset({7}), frozenset())
    assert snap.is_required(group_id=3, teacher_id=None) is True


def test_load_rule_without_row_is_default_and_does_not_write():
    """Чтение правила ничего не создаёт: строки нет → правило по умолчанию."""
    RecordUrlRule.objects.all().delete()
    snap = load_rule()
    assert snap == RuleSnapshot(True, frozenset(), frozenset())
    assert RecordUrlRule.objects.count() == 0


def test_load_rule_reflects_stored_row():
    ids = ensure_minimal_school()
    rule = RecordUrlRule.load()
    rule.required = False
    rule.save()
    RecordUrlRuleGroup.objects.create(rule=rule, group_id=ids['group_id'])
    assert load_rule() == RuleSnapshot(False, frozenset(), frozenset({ids['group_id']}))


# ---------------------------------------------------------------------------
# Сохранение
# ---------------------------------------------------------------------------

def test_replace_rule_replaces_sets_entirely():
    ids = ensure_minimal_school()
    replace_rule(required=False, teacher_ids=[ids['teacher_id']], group_ids=[ids['group_id']],
                 updated_by='Тест')
    snap = load_rule()
    assert snap == RuleSnapshot(False, frozenset({ids['teacher_id']}), frozenset({ids['group_id']}))
    replace_rule(required=True, teacher_ids=[], group_ids=[ids['group_id']], updated_by='Тест')
    snap = load_rule()
    assert snap == RuleSnapshot(True, frozenset(), frozenset({ids['group_id']}))
    assert RecordUrlRule.load().updated_by == 'Тест'


def test_replace_rule_collapses_duplicates():
    ids = ensure_minimal_school()
    replace_rule(required=True, teacher_ids=[ids['teacher_id'], ids['teacher_id']],
                 group_ids=[], updated_by=None)
    assert RecordUrlRuleTeacher.objects.count() == 1


def test_replace_rule_keeps_unchanged_links():
    """Неизменённое исключение не пересоздаётся — в журнале нет шума delete+insert."""
    ids = ensure_minimal_school()
    replace_rule(required=True, teacher_ids=[ids['teacher_id']], group_ids=[], updated_by=None)
    link_id = RecordUrlRuleTeacher.objects.get().id
    replace_rule(required=False, teacher_ids=[ids['teacher_id']], group_ids=[], updated_by=None)
    assert RecordUrlRuleTeacher.objects.get().id == link_id


def test_replace_rule_without_changes_writes_no_events():
    """Повторное сохранение того же содержимого не пишет событий журнала:
    ни правки строки правила, ни ссылок исключений."""
    ids = ensure_minimal_school()
    replace_rule(required=False, teacher_ids=[ids['teacher_id']], group_ids=[ids['group_id']],
                 updated_by='Первый')
    rule_events = RecordUrlRuleEvent.objects.count()
    link_events = RecordUrlRuleTeacherEvent.objects.count()
    updated_at = RecordUrlRule.load().updated_at

    replace_rule(required=False, teacher_ids=[ids['teacher_id']], group_ids=[ids['group_id']],
                 updated_by='Второй')

    assert RecordUrlRuleEvent.objects.count() == rule_events
    assert RecordUrlRuleTeacherEvent.objects.count() == link_events
    rule = RecordUrlRule.load()
    # Ничего не поменялось — «кто сохранил» остаётся автором реальной правки.
    assert rule.updated_by == 'Первый'
    assert rule.updated_at == updated_at


def test_replace_rule_is_tracked_in_changelog():
    ids = ensure_minimal_school()
    replace_rule(required=True, teacher_ids=[ids['teacher_id']], group_ids=[], updated_by=None)
    assert RecordUrlRuleTeacherEvent.objects.filter(
        pgh_label='insert', teacher_id=ids['teacher_id']).exists()
    replace_rule(required=True, teacher_ids=[], group_ids=[], updated_by=None)
    assert RecordUrlRuleTeacherEvent.objects.filter(
        pgh_label='delete', teacher_id=ids['teacher_id']).exists()


def test_rule_payload_names_sorted():
    from apps.lessons.record_url_rule import rule_payload

    ids = ensure_minimal_school()
    replace_rule(required=False, teacher_ids=[ids['teacher_id']], group_ids=[ids['group_id']],
                 updated_by='Тест')
    body = rule_payload()
    assert body['required'] is False
    assert body['teachers'] == [{'id': ids['teacher_id'], 'name': 'Тест Преподаватель 1'}]
    assert [g['id'] for g in body['groups']] == [ids['group_id']]
    assert body['groups'][0]['name']
    assert body['updated_by'] == 'Тест'
    assert body['updated_at'] is not None
