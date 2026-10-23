"""
Правило компенсации преподавателю за отмену урока (спека §4) — чистые функции.

Решение считается один раз при отмене и хранится в LessonCancellation.compensated:
позднейшая правка флага причины в настройках прошлые отмены не меняет
(принцип владельца «изменения не меняют прошлое»). Сумму считает движок
«Мотивации» (payroll.engine), здесь — только «платить или нет».
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from apps.cancellations.models import CancellationSide


@dataclass(frozen=True)
class CancellationDraft:
    """Проверенные входы отмены — собираются до мутаций (services.build_draft) и
    превращаются в запись внутри транзакции отмены."""

    lesson_date: datetime.date        # дата занятия, как её видел клиент (В1)
    side: str
    reason_id: int | None             # None — «Другая причина»
    reason_name: str | None
    reason_compensation: bool
    other_text: str | None
    notified_on: datetime.date | None
    created_by_id: int | None

    @property
    def is_other(self) -> bool:
        return self.reason_id is None


def is_compensated(*, side: str, is_other: bool, reason_compensation: bool,
                   notified_on: datetime.date | None,
                   lesson_date: datetime.date) -> bool:
    """Платить ли преподавателю за отмену.

    «Другая причина» — никогда (решение владельца). Сторона «Преподаватель» —
    только флаг причины. Сторона «Клиент» — флаг причины и регламент «день в
    день»: компенсация, лишь если клиент сообщил об отмене не раньше дня урока.
    Неизвестная сторона — ошибка, а не молчаливая ветка клиента: решение о
    деньгах по мусорному входу принимать нельзя."""
    if side not in (CancellationSide.TEACHER, CancellationSide.CLIENT):
        raise ValueError(f'Неизвестная сторона отмены: {side!r}')
    if is_other:
        return False
    if side == CancellationSide.TEACHER:
        return reason_compensation
    return reason_compensation and notified_on is not None and notified_on >= lesson_date
