"""
calculator.py — расчёт зарплаты преподавателя за урок (payment) и штрафа за
просрочку отчёта (penalty). Общее для teacher SPA и admin SPA — оба пути
записи урока (apps.teacher_spa.services.submit_lesson,
apps.lessons.services.record_lesson) вызывают эти функции, сервер всегда
считает сам (клиентские payment/penalty не принимаются).

Все функции работают с int (рублями), никаких Decimal — оплата у преподавателя
всегда целая (нет копеек).
"""
from __future__ import annotations

PAY_RATES = {
    'halfLesson': 250,    # за каждого присутствующего в полуурочном занятии
    'smallGroup': 500,    # малая группа (1-2 чел.) — все пришли
    'smallPartial': 300,  # малая группа — пришли не все
    'perStudent': 200,    # большая группа (3+ чел.) — за каждого пришедшего
}

# Штраф за отчёт, сданный не в день урока — на каждого присутствовавшего.
# Вынесен из calculate_penalty, чтобы расшифровка в кабинете преподавателя
# (apps.payroll.explain) не дублировала ставку у себя.
PENALTY_PER_STUDENT = 40


def calculate_payment(total: int, present: int, is_half: bool = False) -> int:
    """
    Правила:
      present == 0            → 0
      isHalf                  → 250 * present
      total <= 2, все пришли  → 500
      total <= 2, часть       → 300
      total > 2               → 200 * present
    """
    if present == 0:
        return 0
    if is_half:
        return PAY_RATES['halfLesson'] * present
    if total <= 2:
        return PAY_RATES['smallGroup'] if present == total else PAY_RATES['smallPartial']
    return PAY_RATES['perStudent'] * present


def calculate_penalty(lesson_date: str, submit_date: str, count_students: int) -> int:
    """
    Штраф за просрочку отчёта: тот же день → 0, иначе → 40₽ на каждого
    присутствовавшего ученика. Оба аргумента в формате 'YYYY-MM-DD'.

    Вызывающая сторона решает, что передать в submit_date: teacher SPA — реальную
    сегодняшнюю дату (штраф за опоздание с отчётом); admin SPA передаёт
    submit_date=lesson_date всегда (админ не должен штрафоваться за
    административную запись задним числом — см. design doc).
    """
    if lesson_date == submit_date:
        return 0
    return PENALTY_PER_STUDENT * count_students


def calculate_extra_lesson_payment(present: int) -> int:
    """
    Доп.урок (компенсация пропуска основного занятия): строго 200₽ за каждого
    присутствовавшего, независимо от длительности доп.урока и общего числа
    участников — НЕ переиспользует PAY_RATES/calculate_payment (та ветвится по
    размеру группы/half-lesson, что здесь не применяется, см. design doc).
    """
    return PAY_RATES['perStudent'] * present
