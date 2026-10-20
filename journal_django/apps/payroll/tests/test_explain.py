"""
Unit-тесты apps/payroll/explain.py — подписи «кто не учтён в headcount» для
кабинета преподавателя. Модуль чистый (без БД), маркер django_db не нужен.

Расшифровка суммы теперь — Payroll.rule_note от движка зарплат
(apps/payroll/tests/test_engine.py), здесь её больше нет.
"""
from __future__ import annotations

from apps.payroll.explain import explain_excluded


# ---------------------------------------------------------------------------
# explain_excluded — почему «пришли 4 из 4» в группе из пяти человек
# ---------------------------------------------------------------------------

def test_no_exclusions_returns_none():
    assert explain_excluded(free=0, skip=0) is None


def test_single_free_student():
    note = explain_excluded(free=1, skip=0)
    assert note == '1 ученик не учтён в оплате: бесплатное занятие'


def test_two_free_students_plural():
    note = explain_excluded(free=2, skip=0)
    assert note == '2 ученика не учтены в оплате: бесплатное занятие'


def test_five_skipped_students_plural():
    note = explain_excluded(free=0, skip=5)
    assert note == '5 учеников не учтены в оплате: неоплачиваемый пропуск'


def test_both_reasons_are_listed_separately():
    note = explain_excluded(free=1, skip=2)
    assert 'бесплатное занятие — 1' in note
    assert 'неоплачиваемый пропуск — 2' in note
