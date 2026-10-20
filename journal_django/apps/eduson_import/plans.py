"""Подгонка плана групп прогона под длину курса — общий финальный шаг полного
импорта (import_eduson) и мягкого обновления (sync_eduson)."""
from __future__ import annotations

from apps.eduson_import.context import ImportContext


def fit_plans_to_course(ctx: ImportContext) -> None:
    """Подогнать план каждой группы под длину курса (resize_plan): дописать недостающие
    позиции после последнего занятия по слотам, убрать непроведённые сверх курса.
    Проведённые сверх курса не трогаются — группа в отчёт. Отчёт — только числа."""
    from apps.scheduling.exceptions import PlanHasRecordedLessons
    from apps.scheduling.models import PlannedLesson
    from apps.scheduling.repository import resize_plan

    group_ids = sorted(set(ctx.group_by_eduson.values()))
    before = PlannedLesson.objects.filter(group_id__in=group_ids, seq__isnull=False).count()
    touched, refused = 0, 0
    for gid in group_ids:
        try:
            touched += bool(resize_plan(gid))
        except PlanHasRecordedLessons:
            refused += 1
    after = PlannedLesson.objects.filter(group_id__in=group_ids, seq__isnull=False).count()
    ctx.report.append(f'план подогнан под длину курса: групп изменено {touched}, '
                      f'позиций было {before}, стало {after}'
                      + (f'; проведённых сверх курса — групп {refused} (не тронуты)' if refused else ''))
