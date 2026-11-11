"""Правило компенсации за отмену (спека §4) — чистая функция, без БД.

L — дата урока. «День в день без предварительного уведомления» касается только
клиента: сообщил в день урока или позже → компенсация (если она есть у причины),
сообщил заранее → нет."""
from __future__ import annotations

import datetime

import pytest

from apps.cancellations.rules import CancellationDraft, is_compensated

L = datetime.date(2026, 11, 10)
D = datetime.timedelta(days=1)


@pytest.mark.parametrize(
    'side, reason_compensation, notified_on, expected',
    [
        ('teacher', True, None, True),
        ('teacher', False, None, False),
        ('client', True, L - D, False),      # сообщили накануне
        ('client', True, L, True),           # в день урока
        ('client', True, L + D, True),       # после (внесли с опозданием)
        ('client', False, L, False),
        # Клиент без даты уведомления (в БД невозможно, CHECK) — не платим.
        ('client', True, None, False),
    ],
)
def test_is_compensated(side, reason_compensation, notified_on, expected):
    assert is_compensated(side=side, reason_compensation=reason_compensation,
                          notified_on=notified_on, lesson_date=L) is expected


def test_teacher_side_ignores_notified_on():
    """Для стороны «Преподаватель» решает только флаг причины — дата не влияет."""
    assert is_compensated(side='teacher', reason_compensation=True,
                          notified_on=L - 10 * D, lesson_date=L) is True


def test_unknown_side_rejected():
    with pytest.raises(ValueError):
        is_compensated(side='student', reason_compensation=True,
                       notified_on=L, lesson_date=L)


def _draft(**over):
    base = dict(lesson_date=L, side='client', reason_id=5, reason_name='Заболел',
                reason_compensation=True, details='', notified_on=L,
                created_by_id=None)
    base.update(over)
    return CancellationDraft(**base)


def test_draft_is_immutable():
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        _draft().side = 'teacher'
