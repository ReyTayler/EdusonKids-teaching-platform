"""
TeachersService — тонкий слой между views и repository.

Никакого SQL здесь — всё через repository.
"""
from __future__ import annotations

from typing import Optional

from apps.teachers import repository, stats


def list_teachers(include_inactive: bool = False) -> list[dict]:
    """Делегирует список преподавателей в repository."""
    return repository.list_teachers(include_inactive=include_inactive)


def get_teacher(teacher_id: int) -> Optional[dict]:
    """Возвращает преподавателя или None."""
    return repository.get_teacher(teacher_id)


def create_teacher(data: dict) -> dict:
    """Создаёт преподавателя. 409 при UniqueViolation поднимает view."""
    return repository.create_teacher(data)


def update_teacher(teacher_id: int, data: dict) -> Optional[dict]:
    """Обновляет преподавателя. Возвращает None если не найден."""
    return repository.update_teacher(teacher_id, data)


def soft_delete_teacher(teacher_id: int) -> bool:
    """Мягкое удаление (active=false). Возвращает False если не найден."""
    return repository.soft_delete_teacher(teacher_id)


def get_teacher_stats(teacher_id: int, month: str, *, with_payroll: bool = False) -> dict:
    """
    Полный набор чисел карточки преподавателя за месяц.

    Агрегаты склеиваются здесь, а не на фронте: иначе карточка делала бы
    десяток запросов вместо одного, а VPS у нас 2 CPU.

    `with_payroll` решает вьюха по роли запрашивающего, не этот слой: раздел
    «Зарплата» закрыт `IsSuperAdmin`, а карточку преподавателя видит и менеджер.
    Ключ `payroll` при False отсутствует вовсе, а не приходит нулями — иначе
    менеджер увидел бы «0 ₽» и решил, что преподавателю не заплатили.
    """
    year = int(month[:4])
    breakdown = stats.month_breakdown(teacher_id, month)
    result = {
        'month': month,
        'year': year,
        'last_lesson_date': stats.last_lesson_date(teacher_id),
        'total': breakdown['total'],
        'by_direction': breakdown['by_direction'],
        'by_duration': breakdown['by_duration'],
        # Год берётся из выбранного месяца, отдельного переключателя года нет:
        # шагая месяцами через январь, человек и так попадает в соседний год,
        # а второй переключатель рядом с первым только запутал бы.
        'monthly': stats.year_series(teacher_id, year),
        'group_progress': stats.group_progress(teacher_id),
        'attendance': stats.attendance(teacher_id, month),
        'weekday_load': stats.weekday_load(teacher_id, month),
        # unfilled и absences.pending_now — СЕЙЧАС, не за месяц: это очереди,
        # требующие действия, и они не обнуляются от переключения периода.
        'unfilled': stats.unfilled(teacher_id),
        'absences': stats.absences(teacher_id, month),
    }
    if with_payroll:
        result['payroll'] = stats.payroll_for_month(teacher_id, month)
    return result
