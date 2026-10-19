"""
Доменные исключения раздела groups.

Не зависят от DRF/HTTP — бросаются в repository, маппятся в HTTP-ответ во view.
"""
from __future__ import annotations


class ImmutableGroupFormat(Exception):
    """
    Попытка изменить формат группы (индивидуальная/групповая) после создания.

    Поле `groups.is_individual` определяет тип группы и влияет на инвариант
    «≤1 активный membership». Менять его на существующей группе нельзя —
    view отдаёт 400 Bad Request.
    """

    default_message = 'Формат группы (индивидуальная/групповая) нельзя изменить после создания.'

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)
