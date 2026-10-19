"""
Доменные исключения раздела memberships.

Не зависят от DRF/HTTP — бросаются в repository (слой доступа к данным),
маппятся в HTTP-ответ во view. Такое разделение позволяет держать бизнес-
правила в одном месте и не тащить web-слой в репозиторий.
"""
from __future__ import annotations

from typing import Optional


class IndividualGroupFull(Exception):
    """
    Индивидуальная группа уже занята другим активным учеником.

    В индивидуальной группе (`groups.is_individual = true`) допускается не более
    одного активного membership. Попытка добавить/реактивировать ДРУГОГО ученика
    при уже активном → это исключение (view отдаёт 409 Conflict).

    active_student_id — id уже активного ученика (для диагностики/UI), опционален.
    """

    default_message = 'В индивидуальной группе может быть только один активный ученик.'

    def __init__(
        self,
        message: Optional[str] = None,
        *,
        active_student_id: Optional[int] = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.active_student_id = active_student_id


class DirectionMismatch(Exception):
    """
    Целевая группа принадлежит другому направлению, чем исходная membership.

    Перевод ученика (apps.memberships.services.transfer_membership) разрешён
    только между группами одного направления.
    """

    default_message = 'Перевод разрешён только между группами одного направления.'

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)


class SameGroupTransfer(Exception):
    """Целевая группа перевода совпадает с текущей — переводить некуда."""

    default_message = 'Ученик уже состоит в этой группе.'

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)


class TargetGroupUnavailable(Exception):
    """Целевая группа перевода не найдена или неактивна (архивная)."""

    default_message = 'Целевая группа не найдена или неактивна.'

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)


class SourceMembershipInvalid(Exception):
    """
    Источник перевода (from_membership_id) не найден или принадлежит другому
    ученику.

    Бросается универсальной place_student_in_group, когда указан явный источник
    истории, но он не проходит проверку принадлежности. View отдаёт 400.
    """

    default_message = 'Источник перевода не найден или принадлежит другому ученику.'

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)


class AlreadyActiveInGroup(Exception):
    """
    Ученик уже активен в целевой группе — переводить/записывать некуда.

    Защищает от перезаписи transferred_from/start_date у уже активной membership
    (см. аудит 2026-07-20). View отдаёт 409 Conflict.
    """

    default_message = 'Ученик уже состоит в этой группе.'

    def __init__(self, message: Optional[str] = None) -> None:
        super().__init__(message or self.default_message)
