"""Валидация входных данных."""
from apps.taskboard.serializers import (
    CompleteSerializer, MoveSerializer, StageWriteSerializer,
    TaskCreateSerializer, TaskPatchSerializer, WeekQuerySerializer,
)


def test_create_requires_board_and_title():
    s = TaskCreateSerializer(data={})
    assert not s.is_valid()
    assert 'board_id' in s.errors
    assert 'title' in s.errors


def test_create_accepts_minimal_payload():
    s = TaskCreateSerializer(data={'board_id': 1, 'title': 'Позвонить'})
    assert s.is_valid(), s.errors
    assert s.validated_data['priority'] == 'normal'


def test_create_rejects_unknown_priority():
    s = TaskCreateSerializer(data={'board_id': 1, 'title': 'Х', 'priority': 'urgent'})
    assert not s.is_valid()
    assert 'priority' in s.errors


def test_move_requires_stage():
    s = MoveSerializer(data={})
    assert not s.is_valid()
    assert 'to_stage_id' in s.errors


def test_move_rejects_unknown_resolution():
    s = MoveSerializer(data={'to_stage_id': 1, 'resolution': 'почти'})
    assert not s.is_valid()
    assert 'resolution' in s.errors


def test_complete_defaults_to_done():
    s = CompleteSerializer(data={})
    assert s.is_valid(), s.errors
    assert s.validated_data['resolution'] == 'done'


def test_patch_allows_partial_payload():
    s = TaskPatchSerializer(data={'title': 'Новое'})
    assert s.is_valid(), s.errors
    assert s.validated_data == {'title': 'Новое'}


def test_patch_does_not_accept_stage_or_resolution():
    """Стадия и результат меняются только через /move и /complete."""
    s = TaskPatchSerializer(data={'stage_id': 5, 'resolution': 'done'})
    assert s.is_valid(), s.errors
    assert s.validated_data == {}


def test_stage_write_rejects_unknown_category():
    s = StageWriteSerializer(data={'label': 'Х', 'category': 'paused'})
    assert not s.is_valid()
    assert 'category' in s.errors


def test_stage_write_rejects_malformed_color():
    s = StageWriteSerializer(data={'label': 'Х', 'category': 'open', 'color': 'красный'})
    assert not s.is_valid()
    assert 'color' in s.errors


def test_week_rejects_reversed_range():
    s = WeekQuerySerializer(data={'date_from': '2026-09-10', 'date_to': '2026-09-01'})
    assert not s.is_valid()
    assert 'date_to' in s.errors


def test_week_accepts_single_day():
    s = WeekQuerySerializer(data={'date_from': '2026-09-01', 'date_to': '2026-09-01'})
    assert s.is_valid(), s.errors
