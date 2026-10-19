"""Unit-тесты сериализаторов extra_lessons (без БД)."""
from __future__ import annotations

from apps.extra_lessons.serializers import (
    ExtraLessonCreateSerializer, ExtraLessonRecordSerializer,
)


def test_create_serializer_valid():
    s = ExtraLessonCreateSerializer(data={
        'missed_lesson_id': 1, 'teacher_id': 2, 'student_ids': [3, 4],
        'scheduled_date': '2026-04-05', 'scheduled_time': '15:00', 'duration_minutes': 45,
    })
    assert s.is_valid(), s.errors


def test_create_serializer_rejects_bad_duration():
    s = ExtraLessonCreateSerializer(data={
        'missed_lesson_id': 1, 'teacher_id': 2, 'student_ids': [3],
        'scheduled_date': '2026-04-05', 'scheduled_time': '15:00', 'duration_minutes': 20,
    })
    assert not s.is_valid()
    assert 'duration_minutes' in s.errors


def test_create_serializer_rejects_empty_students():
    s = ExtraLessonCreateSerializer(data={
        'missed_lesson_id': 1, 'teacher_id': 2, 'student_ids': [],
        'scheduled_date': '2026-04-05', 'scheduled_time': '15:00', 'duration_minutes': 45,
    })
    assert not s.is_valid()
    assert 'student_ids' in s.errors


def test_create_serializer_rejects_duplicate_students():
    s = ExtraLessonCreateSerializer(data={
        'missed_lesson_id': 1, 'teacher_id': 2, 'student_ids': [3, 3],
        'scheduled_date': '2026-04-05', 'scheduled_time': '15:00', 'duration_minutes': 45,
    })
    assert not s.is_valid()
    assert 'student_ids' in s.errors


def test_create_serializer_rejects_unknown_field():
    s = ExtraLessonCreateSerializer(data={
        'missed_lesson_id': 1, 'teacher_id': 2, 'student_ids': [3],
        'scheduled_date': '2026-04-05', 'scheduled_time': '15:00', 'duration_minutes': 45,
        'payroll': {'payment': 999},
    })
    assert not s.is_valid()


def test_record_serializer_valid():
    s = ExtraLessonRecordSerializer(data={'present': True, 'record_url': None})
    assert s.is_valid(), s.errors


def test_record_serializer_rejects_missing_present():
    s = ExtraLessonRecordSerializer(data={})
    assert not s.is_valid()
    assert 'present' in s.errors
