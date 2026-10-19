"""
Баланс ученика — выводится, не хранится. С 2026-07-08 общий пул по всем
направлениям (payments.direction_id — информационный тег, не скоуп списания).

Единый дом расчёта баланса. paid_by_direction/attended_by_direction — только
информационные разбивки (см. docs/superpowers/specs/2026-07-08-student-balance-pooling-design.md).

balance_for_student переэкспортируется из repository для удобства потребителей.
"""
from __future__ import annotations

from apps.finances import repository
from apps.finances.repository import balance_for_student  # re-export

__all__ = ['balance_for_student', 'get_student_balance']


def get_student_balance(student_id: int) -> dict:
    """
    Общий баланс ученика (единый пул) + информационные разбивки по направлениям
    + список оплат. list_payments импортируется лениво, чтобы не создавать цикл
    finances ↔ payments.
    """
    from apps.payments.repository import list_payments

    total_balance = repository.balance_for_student(student_id)
    remaining = repository.student_fifo_remaining(student_id)

    paid_by_direction = [
        {
            'direction_id':      r['direction_id'],
            'direction_name':    r['direction_name'],
            'direction_color':   r['direction_color'],
            'total_paid_amount': repository._js_number(r['total_paid_amount']),
        }
        for r in repository.paid_by_direction_rows(student_id)
    ]
    attended_by_direction = [
        {
            'direction_id':     r['direction_id'],
            'direction_name':   r['direction_name'],
            'direction_color':  r['direction_color'],
            'attended_lessons': repository._js_number(r['attended_lessons']),
        }
        for r in repository.attended_by_direction_rows(student_id)
    ]

    total_paid = repository.total_paid_amount(student_id)
    payments = list_payments(student_id=student_id)

    return {
        'total_balance':         total_balance,
        'remaining_value':       repository._js_number(remaining['remaining_value']),
        'total_paid_amount':     total_paid,
        'paid_by_direction':     paid_by_direction,
        'attended_by_direction': attended_by_direction,
        'payments':              payments,
    }
