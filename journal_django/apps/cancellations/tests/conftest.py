"""
Фикстуры тестов cancellations.

django_db_setup НЕ переопределяем (как feedback/renewals/reports): таблицы
cancellation_reasons и lesson_cancellations создаёт только миграция этого
приложения, поэтому тесты идут в свежую test_eduson_test, которую pytest-django
собирает из миграций, и не зависят от того, догнана ли общая eduson_test.

Данные — выдуманные (ensure_minimal_school) и живут в транзакции теста.
"""
from __future__ import annotations

import datetime

import pytest
from django.utils import timezone


@pytest.fixture
def school(db):
    """Минимальная школа: направление, преподаватель, группа со слотом, урок."""
    from apps.core.tests.minimal_school import ensure_minimal_school

    return ensure_minimal_school()


@pytest.fixture
def make_marker(db):
    """Фабрика маркера отмены: строка плана seq=NULL, status=cancelled — ровно так
    её создаёт scheduling.repository.cancel_lesson."""
    from apps.scheduling.models import PlannedLesson
    from apps.scheduling.occurrences import CANCELLED

    def _make(group_id: int, teacher_id: int | None, day: datetime.date,
              time: datetime.time = datetime.time(10, 0)):
        now = timezone.now()
        return PlannedLesson.objects.create(
            group_id=group_id, seq=None, lesson_number=None, scheduled_date=day,
            scheduled_time=time, teacher_id=teacher_id, status=CANCELLED,
            created_at=now, updated_at=now,
        )

    return _make


@pytest.fixture
def make_reason(db):
    """Фабрика причины отмены; позиция по умолчанию — в конец стороны."""
    from apps.cancellations.models import CancellationReason

    def _make(side: str = 'client', name: str = 'Заболел', compensation: bool = True,
              **kwargs):
        if 'position' not in kwargs:
            kwargs['position'] = CancellationReason.objects.filter(side=side).count()
        return CancellationReason.objects.create(
            side=side, name=name, compensation=compensation, **kwargs,
        )

    return _make


@pytest.fixture
def make_record(db):
    """Фабрика записи об отмене с валидными по CHECK значениями по умолчанию
    (сторона «Клиент», причина из справочника, уведомили в день урока)."""
    from apps.cancellations.models import LessonCancellation

    def _make(**kwargs):
        defaults = {
            'side': 'client', 'is_other': False, 'compensated': True,
            'reason_text': 'Заболел', 'scheduled_time': datetime.time(10, 0),
        }
        defaults.update(kwargs)
        if defaults['side'] == 'client':
            defaults.setdefault('notified_on', defaults.get('lesson_date'))
        return LessonCancellation.objects.create(**defaults)

    return _make
