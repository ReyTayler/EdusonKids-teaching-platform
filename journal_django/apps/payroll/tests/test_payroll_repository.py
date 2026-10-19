"""
Integration-тесты repository слоя payroll (реальная БД, managed=False).

Покрытие:
  - list_payroll: контракт {rows,total,page,page_size}, JOIN-контекст, фильтр teacher_id,
    тихий fallback невалидного sort_by.
  - payroll_summary: COUNT/SUM по учителю, lessons_count строкой, sum_* Decimal с масштабом,
    фильтр по диапазону дат.
  - update_payroll: COALESCE-семантика, None для отсутствующего id.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.payroll import repository

pytestmark = pytest.mark.django_db


def test_list_payroll_envelope_and_context(payroll_fixture, teacher_id_fixture, group_fixture):
    payroll_id, lesson_id = payroll_fixture
    result = repository.list_payroll(filters={'teacher_id': teacher_id_fixture, 'group_id': group_fixture})
    assert set(result.keys()) == {'rows', 'total', 'page', 'page_size'}
    assert result['total'] == 1
    row = result['rows'][0]
    assert row['id'] == payroll_id
    assert row['lesson_id'] == lesson_id
    assert row['group_name'] == '__pr_group__'
    assert row['payment'] == Decimal('650.00')
    assert row['lesson_number'] == Decimal('1.0')


def test_list_payroll_invalid_sort_falls_back(payroll_fixture, teacher_id_fixture):
    result = repository.list_payroll(
        sort_by='; DROP TABLE payroll; --', filters={'teacher_id': teacher_id_fixture}
    )
    assert result['total'] >= 1


def test_summary_lessons_count_is_string(payroll_fixture, teacher_id_fixture):
    rows = repository.payroll_summary(teacher_id=teacher_id_fixture)
    assert len(rows) == 1
    r = rows[0]
    # node-pg отдаёт COUNT(*) (bigint) строкой → ::text
    assert r['lessons_count'] == '1'
    assert isinstance(r['lessons_count'], str)
    assert r['sum_payment'] == Decimal('650.00')
    assert r['sum_penalty'] == Decimal('0.00')


def test_summary_date_range_filter(payroll_fixture, teacher_id_fixture):
    # Урок 2026-04-10 — попадает в диапазон.
    inside = repository.payroll_summary(
        teacher_id=teacher_id_fixture, date_from='2026-04-01', date_to='2026-04-30'
    )
    assert inside and inside[0]['lessons_count'] == '1'
    # Вне диапазона — учителя нет в сводке.
    outside = repository.payroll_summary(
        teacher_id=teacher_id_fixture, date_from='2026-05-01', date_to='2026-05-31'
    )
    assert outside == []


def test_list_payroll_single_row_per_payroll(payroll_fixture, teacher_id_fixture):
    """Одна строка на payroll (спец-надбавок «сгорания» больше нет — сгорание =
    отдельный burned-Lesson со своим payroll в свой месяц)."""
    result = repository.list_payroll(filters={'teacher_id': teacher_id_fixture})
    assert result['total'] == 1
    assert 'is_surcharge' not in result['rows'][0]


def test_update_payroll_coalesce(payroll_fixture):
    payroll_id, _ = payroll_fixture
    # Меняем только payment — остальное сохраняется.
    updated = repository.update_payroll(payroll_id, {'payment': 700})
    assert updated['payment'] == Decimal('700.00')
    assert updated['total_students'] == 5
    assert updated['present_count'] == 4


def test_update_payroll_missing_returns_none():
    assert repository.update_payroll(999_999_999, {'payment': 1}) is None
