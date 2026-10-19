"""
Вычисление номера цикла продления и признака «окно продления».

Цикл = 1 оплаченный месяц = 4 урока (LESSONS_PER_CYCLE). Half-lesson (0.5)
уже учтён в attended (numeric), поэтому floor по 4 корректен.
"""
from __future__ import annotations

import math

LESSONS_PER_CYCLE = 4


def open_cycle_no(attended: float) -> int:
    """
    attended отработанных уроков → номер ОТКРЫТОГО цикла ученика (1-based).

    Ровно на рубеже (attended кратно 4 и > 0) цикл ещё открыт: уроки отработаны,
    но решение «продлил / ушёл» по нему не принято — сделка должна встать на
    «Ждём продление», а не начинать следующий цикл с нуля.

    Правило одно на весь раздел: та же раскладка, что у пересбора истории
    (rebuild.plan_for_student). Совпадение закреплено тестом
    test_open_cycle_no_matches_rebuild_plan — при правке любой из сторон он упадёт.
    """
    units = float(attended)
    completed_full = math.floor(units / LESSONS_PER_CYCLE)
    if completed_full >= 1 and units % LESSONS_PER_CYCLE == 0:
        return completed_full
    return completed_full + 1


def in_renewal_window(remaining: float, balance: float) -> bool:
    """Окно продления: остался ≤1 урок ИЛИ баланс отработан (≤0)."""
    return float(remaining) <= 1 or float(balance) <= 0
