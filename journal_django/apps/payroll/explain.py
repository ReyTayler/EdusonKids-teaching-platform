"""
explain.py — подпись к headcount строки расчётного листа в кабинете преподавателя.

Расшифровка самой суммы больше не выводится здесь: правило, по которому
начислено, движок зарплат записывает в Payroll.rule_note
(apps/payroll/engine.py — единственное место правил, спека
2026-10-20-eduson-motivation-payroll-design). Здесь остаётся только объяснение,
почему «пришли 4 из 4», когда в группе пятеро: это свойство посещаемости, а не
ставок, и в rule_note оно не попадает.

Модуль чистый: ни ORM, ни запросов, ни Django. Тестируется в изоляции
(tests/test_explain.py).
"""
from __future__ import annotations

from typing import Optional


def _plural(count: int, forms: tuple[str, str, str]) -> str:
    """Русская форма слова: (1 ученик, 2 ученика, 5 учеников)."""
    tail_100 = count % 100
    if 11 <= tail_100 <= 14:
        return forms[2]
    tail = count % 10
    if tail == 1:
        return forms[0]
    if 2 <= tail <= 4:
        return forms[1]
    return forms[2]


def explain_excluded(*, free: int, skip: int) -> Optional[str]:
    """
    Почему «пришли 4 из 4», когда в группе пятеро.

    total_students в payroll — это НЕ размер группы: неоплачиваемые пропуски
    (unpaid_skip) из headcount исключены (apps.payroll.recalc).
    free — бесплатные занятия, не попавшие в present_count: так считалось до
    2026-09-28 и держится в строках, которые движок ещё не пересчитал
    (manage.py recalculate_payroll пересчитывает всю историю). Без этой подписи
    расхождение выглядит как ошибка системы.

    Подпись говорит о счёте «пришли N из M», а не об оплате: сумма за урок —
    фикс и от числа учеников не зависит; от присутствия зависит только штраф
    «за каждого присутствовавшего». Сказать «не учтён в оплате» значило бы
    пообещать преподавателю деньги, которых формула не начисляет.
    """
    if not free and not skip:
        return None

    if free and skip:
        return (
            'не учтены в счёте «пришли из»: бесплатное занятие — '
            f'{free}, неоплачиваемый пропуск — {skip}'
        )

    count = free or skip
    reason = 'бесплатное занятие' if free else 'неоплачиваемый пропуск'
    noun = _plural(count, ('ученик', 'ученика', 'учеников'))
    verb = _plural(count, ('не учтён', 'не учтены', 'не учтены'))
    return f'{count} {noun} {verb} в счёте «пришли из»: {reason}'
