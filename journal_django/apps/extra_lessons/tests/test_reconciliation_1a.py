"""
Task 8 (Phase 1a) — сверка денег/потребления/продлений на полном сценарии
компенсации пропуска через пер-ученическую AbsenceResolution.

Групповую модель доп.урока заменили на пер-ученический AbsenceResolution.
Денежное поведение обязано остаться прежним. Этот интеграционный тест
пиннит РУЧНО посчитанные значения по всем денежным путям на одном
end-to-end сценарии makeup'а и доказывает, что новый пер-ученический путь
даёт те же числа, что и раньше.

Ручной расчёт сценария
----------------------
Дано (фикстуры membership_fixture + missed_lesson_fixture):
  • Оплата: lessons_count=8, total_amount=8000 ₽ (kind='purchase', paid_at
    2026-06-01) → цена урока партии = 8000 / 8 = 1000 ₽.
  • Пропущенный урок: групповое занятие 60 минут 2026-04-01, ученик
    отмечен present=false (ничего пока не потреблено).

БАЗА (до makeup'а):
  • balance_for_student = purchased(8) − attended(0) = 8.
  • attended_units_total = 0 (ни одной present=true отметки).
  • _attended_total (движок продлений) = 0.0.

Действия:
  • create_assignment: makeup ученику против пропущенного урока,
    teacher=teacher_fixture, 2026-04-05 15:00, длительность 45 мин.
  • record(present=True): доп.урок проведён.
    payment = 200 ₽ (calculate_extra_lesson_payment: 200 × 1 present,
                      плоско, не зависит от длительности/размера группы);
    penalty = 0 (scheduled_date == submit_date == 2026-04-05).

ПОСЛЕ makeup'а — потребление считается ОДИН раз, на ИСХОДНОМ 60-мин уроке:
  • record() ретроактивно ставит present=true на исходном 60-мин уроке
    (apply_makeup_attendance). 60 минут ≠ 45 → 1 полный урок.
  • Сам факт доп.урока — lesson_type='extra', ИСКЛЮЧЁН из потребления
    (иначе один пропуск списался бы дважды).
  • attended_units_total = 1 (один 60-мин урок).
  • _attended_total = 1.0 — КЛЮЧЕВОЙ инвариант: продления и финансы
    считают «отработано» через ЕДИНЫЙ источник, значения совпадают.
  • balance_for_student = 8 − 1 = 7.
  • Помесячный отчёт '2026-04': дата потребления относится к месяцу
    ФАКТИЧЕСКОГО проведения доп.урока (fact_lesson.lesson_date=2026-04-05,
    апрель) — см. _makeup_completion_dates. Значит:
      attended_lessons = 1 (посещено в апреле),
      worked_off_month = 1000.00 ₽ (1 урок × 1000 ₽),
      balance = 7, remaining_value = 7 × 1000 = 7000.00 ₽.
  • Зарплата teacher_fixture SUM = 200: пропущенный урок дал payroll 0 ₽
    (present_count=0 → calculate_payment=0), доп.урок — 200 ₽ → 0 + 200.

Если любое вычисленное значение разойдётся с этим ручным расчётом — это
сигнал реальной денежной регрессии, а не повод молча подогнать ожидание.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from django.db import connection

from apps.extra_lessons import services
from apps.finances import repository as fin_repo
from apps.finances.reports import collect_monthly_report
from apps.renewals.engine import _attended_total

pytestmark = pytest.mark.django_db


class _FakeRequest:
    """Минимальная заглушка request для log_event (без HTTP-контекста)."""
    META = {}
    user = None


def _delete_resolutions(missed_lesson_id):
    """Сносит резолюции пропуска (снимает DB-level FK fact_lesson → lessons ДО
    удаления факт-урока в _cleanup_fact)."""
    with connection.cursor() as cur:
        cur.execute(
            'DELETE FROM absence_resolutions WHERE missed_lesson_id = %s',
            [missed_lesson_id],
        )


def _cleanup_fact(lesson_id):
    with connection.cursor() as cur:
        cur.execute('DELETE FROM payroll WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s', [lesson_id])
        cur.execute('DELETE FROM lessons WHERE id = %s', [lesson_id])


def _payroll_sum(teacher_id: int) -> int:
    with connection.cursor() as cur:
        cur.execute(
            'SELECT COALESCE(SUM(payment), 0) FROM payroll WHERE teacher_id = %s',
            [teacher_id],
        )
        return int(cur.fetchone()[0])


def _report_rows(month: str, student_id: int):
    """Строки реестра признания выручки по ученику (строка = платёж)."""
    return [r for r in collect_monthly_report(month).rows if r.student_id == student_id]


def test_makeup_money_reconciliation_1a(
    group_fixture, teacher_fixture, student_fixture,
    membership_fixture, missed_lesson_fixture,
):
    """End-to-end сверка денег/потребления/продлений для одного makeup'а."""
    # --- БАЗА: до makeup'а ничего не потреблено -----------------------------
    baseline_balance = fin_repo.balance_for_student(student_fixture)
    assert baseline_balance == 8
    assert fin_repo.attended_units_total(student_fixture) == Decimal('0')
    assert _attended_total(student_fixture) == 0.0
    # Зарплата за пропущенный урок (0 присутствующих) = 0 ₽.
    assert _payroll_sum(teacher_fixture) == 0

    # --- Назначаем и проводим доп.урок --------------------------------------
    created = services.create_assignment(
        {
            'missed_lesson_id': missed_lesson_fixture,
            'teacher_id': teacher_fixture,
            'student_ids': [student_fixture],
            'scheduled_date': '2026-04-05',
            'scheduled_time': '15:00',
            'duration_minutes': 45,
        },
        _FakeRequest(),
    )
    assert created['created'] == 1
    rid = created['resolution_ids'][0]

    result = services.record(
        rid,
        teacher_id=teacher_fixture,
        present=True,
        record_url=None,
        submitted_by_token='acct:1',
        submit_date='2026-04-05',
        request=_FakeRequest(),
    )
    try:
        assert result['payment'] == 200
        assert result['penalty'] == 0

        # --- ПОСЛЕ makeup'а: потребление один раз, на исходном 60-мин уроке ---
        attended_units = fin_repo.attended_units_total(student_fixture)
        assert attended_units == Decimal('1')

        # Ключевой инвариант: продления и финансы согласованы.
        renewals_attended = _attended_total(student_fixture)
        assert renewals_attended == 1.0
        assert float(attended_units) == renewals_attended

        balance = fin_repo.balance_for_student(student_fixture)
        assert balance == 7
        assert balance == baseline_balance - 1

        # --- Реестр признания выручки за апрель (месяц доп.урока) -------------
        rows = _report_rows('2026-04', student_fixture)
        assert len(rows) == 1                                      # одна оплата ученика
        row = rows[0]
        assert row.revenue_by_month['2026-04'] == Decimal('1000')   # 1 урок × 1000 ₽
        assert row.advance == Decimal('7000')                       # 7 × 1000 ₽
        assert row.total_amount + row.surcharge_amount == (
            row.revenue_total + row.refunded + row.advance
        )

        # --- Зарплата преподавателя: 0 (пропуск) + 200 (доп.урок) = 200 ------
        assert _payroll_sum(teacher_fixture) == 200
    finally:
        # Резолюцию (fact_lesson FK) сносим ДО факт-урока, иначе DB-level FK.
        _delete_resolutions(missed_lesson_fixture)
        _cleanup_fact(result['lesson_id'])
