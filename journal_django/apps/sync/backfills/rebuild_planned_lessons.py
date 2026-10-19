# journal_django/apps/sync/backfills/rebuild_planned_lessons.py
"""Полная пересборка planned_lessons из фактов для всех активных групп.

Тонкая обёртка над apps.scheduling.repository — генератор/репозиторий уже
существуют и покрыты тестами в apps/scheduling/tests/test_backfill_planned_lessons.py,
логику не дублируем. Делает то же самое, что management-команда
apps/scheduling/management/commands/backfill_planned_lessons.py, но через Celery
(см. apps/sync/tasks.py).

⚠️ РАЗРУШИТЕЛЬНО: пересборка группы удаляет её текущий план (reset) и разворачивает
будущее заново от последнего факта — ПЕРЕЗАПИСЫВАЕТ ручные операции будущего
(переносы/отмены/смену преподавателя), сделанные через /api/admin/groups/<pk>/plan.
Результат today-независим (будущее считается от последнего факта, не от даты
запуска). Группы без group_start_date/direction.total_lessons/слотов пропускаются
(см. docs/lesson-scheduling.md).
"""
from __future__ import annotations

from apps.scheduling import repository

_BLOCKING_REASONS = ('no_start_date', 'no_total_lessons', 'no_slots')


def run(dry_run: bool = False) -> dict:
    result = {
        'entity': 'planned-lessons-rebuild',
        'groups_total': 0, 'processed': 0, 'rows_written': 0,
        'skipped': 0, 'skipped_details': [], 'dry_run': dry_run,
    }

    groups = repository.active_groups()
    ids = [g['id'] for g in groups]
    slots_map = repository.slots_by_group(ids)
    facts_map = repository.facts_by_group(ids)
    result['groups_total'] = len(groups)

    for g in groups:
        gid = g['id']
        g_norm = {
            'group_start_date': g['group_start_date'],
            'total_lessons': g['total_lessons'],
            'lesson_duration_minutes': g['lesson_duration_minutes'],
            'teacher_id': g['teacher_pk'],
        }
        rebuild_result = repository.rebuild_group_plan(
            gid, g_norm, slots_map.get(gid, []), facts_map.get(gid, []),
            dry_run=dry_run,
        )
        if rebuild_result['written'] == 0 and rebuild_result['reason'] in _BLOCKING_REASONS:
            result['skipped'] += 1
            result['skipped_details'].append({'group': g['name'], 'reason': rebuild_result['reason']})
            continue
        result['rows_written'] += rebuild_result['written']
        result['processed'] += 1

    return result
