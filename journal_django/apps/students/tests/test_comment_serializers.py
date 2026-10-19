"""Тесты сериализаторов комментариев (без БД)."""
from __future__ import annotations

from apps.students.serializers import StudentCommentWriteSerializer


def test_rejects_blank_body():
    ser = StudentCommentWriteSerializer(data={'body': '   '})
    assert not ser.is_valid()
    assert 'body' in ser.errors


def test_strips_body():
    ser = StudentCommentWriteSerializer(data={'body': '  Привет  '})
    assert ser.is_valid(), ser.errors
    assert ser.validated_data['body'] == 'Привет'


def test_rejects_too_long_body():
    ser = StudentCommentWriteSerializer(data={'body': 'x' * 5001})
    assert not ser.is_valid()
    assert 'body' in ser.errors


def test_accepts_max_length_body():
    ser = StudentCommentWriteSerializer(data={'body': 'x' * 5000})
    assert ser.is_valid(), ser.errors
