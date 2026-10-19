"""
Эффективная длина курса группы — единственный источник числа уроков для ПЛАНА
и СЕТКИ.

`groups.lessons_total` (ручное число в форме группы) перекрывает
`directions.total_lessons`. Пусто → длина курса направления, как было раньше.

НЕ применять к лимиту продаж (apps.payments.repository.create_payment) и к
прогрессу ученика по направлению (apps.students.repository.student_stats,
apps.dashboard.registry_service): там курс измеряется программой направления,
иначе ученику короткой группы нельзя будет продать больше уроков, чем она
длится. См. docs/superpowers/specs/2026-07-27-group-lessons-total-design.md,
раздел 4.

Единица измерения — УРОКИ курса, не календарные занятия: у 45-минутной группы
шаг 0.5 (apps.scheduling.occurrences._step_for), поэтому 2 урока = 4 занятия.
"""
from __future__ import annotations

from django.db.models import F
from django.db.models.functions import Coalesce


def effective_total_lessons_expr():
    """ORM-выражение для `.values()`/`.annotate()` на queryset модели Group."""
    return Coalesce(F('lessons_total'), F('direction__total_lessons'))
