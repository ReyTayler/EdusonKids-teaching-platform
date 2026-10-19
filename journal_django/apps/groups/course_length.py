"""
Эффективная длина курса группы — единственный источник числа уроков для ПЛАНА
и СЕТКИ.

`groups.lessons_total` (ручное число в форме группы) перекрывает
`directions.total_lessons`. Пусто → длина курса направления, как было раньше.

НЕ применять к лимиту продаж (apps.payments.repository.create_payment) и к
прогрессу ученика по направлению (apps.students.repository.student_stats):
там курс измеряется программой направления, иначе ученику короткой группы
нельзя будет продать больше уроков, чем она длится. См.
docs/superpowers/specs/2026-07-27-group-lessons-total-design.md, раздел 4.

Реестр куратора (apps.dashboard.registry_service) в Eduson, наоборот, считает
по этой длине: его остаток — остаток КУРСА группы, а не оплаченных уроков
(оплат в Eduson нет; спека этапа 3, раздел B).

Единица измерения — УРОКИ курса, не календарные занятия: у 45-минутной группы
шаг 0.5 (apps.scheduling.occurrences._step_for), поэтому 2 урока = 4 занятия.
"""
from __future__ import annotations

from django.db.models import F
from django.db.models.functions import Coalesce


def effective_total_lessons_expr(prefix: str = ''):
    """ORM-выражение для `.values()`/`.annotate()` на queryset модели Group.

    prefix — путь до группы от другой модели (например, 'group__' у queryset'а
    членств): правило длины курса живёт здесь и не переписывается в запросах,
    которые идут не от Group.
    """
    return Coalesce(F(f'{prefix}lessons_total'), F(f'{prefix}direction__total_lessons'))
