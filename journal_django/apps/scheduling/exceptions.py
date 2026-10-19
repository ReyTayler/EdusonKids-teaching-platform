"""
Доменные исключения планировщика.

Не зависят от DRF/HTTP — бросаются в repository, маппятся в HTTP-ответ во view.
"""
from __future__ import annotations


class PlanHasRecordedLessons(Exception):
    """
    Попытка задать группе длину курса короче, чем в ней уже проведено занятий.

    Урезать план можно только по непроведённым (pending/overdue) строкам:
    удаление проведённого (done) или перенесённого (moved) занятия — потеря
    фактических данных. View отдаёт 409 Conflict.
    """

    def __init__(self, recorded_lessons: int) -> None:
        self.recorded_lessons = recorded_lessons
        super().__init__(
            f'В группе уже проведено занятий: {recorded_lessons}. '
            f'Задать меньшее число уроков нельзя.'
        )


class PlanResyncBlocked(Exception):
    """
    Починку плана («номер факта = номер позиции») выполнять нельзя.

    Три разные причины, все — предусловия операции, а не сбой:
      • blocked_by — сработала проверка слоя 3 (apps.scheduling.health):
        fact_without_position / duplicate_dates. Пока занятие не привязано ни к
        одной позиции или два занятия стоят на одной дате, «правильная» раскладка
        не определена — чинить наугад значит закрепить ошибку;
      • orphan_facts — занятию не находится позиции С ЕГО НОМЕРОМ (это ДРУГОЙ
        предикат, чем fact_without_position: факт может сидеть на позиции с чужим
        номером и первую проверку пройти). Чинить наполовину нельзя;
      • состояние группы изменилось между предпросмотром и применением (expected
        разошёлся с диффом, посчитанным под локом).

    View отдаёт 409 Conflict, команда — CommandError. ValueError здесь нельзя:
    он маппится на 400/500 в других вьюхах и потерял бы поля разбора.
    """

    def __init__(
        self,
        message: str = 'Починка плана сейчас невозможна.',
        *,
        blocked_by: list[str] | None = None,
        orphan_facts: list[dict] | None = None,
    ) -> None:
        self.blocked_by: list[str] = list(blocked_by or [])
        self.orphan_facts: list[dict] = list(orphan_facts or [])
        super().__init__(message)
