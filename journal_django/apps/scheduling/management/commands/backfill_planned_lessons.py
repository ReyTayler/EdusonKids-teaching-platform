"""
Пересборка материализованных плановых занятий (planned_lessons) из фактов.

  python manage.py backfill_planned_lessons [--dry-run]

Для всех активных групп с заданными group_start_date И direction.total_lessons:
пересобирает план из фактов (см. docs/lesson-scheduling.md):
  - ПРОШЛОЕ = факты: проведённые уроки → done, плановая дата = фактическая
    (scheduled_date = lesson.lesson_date), преподаватель из факта;
  - БУДУЩЕЕ: оставшиеся уроки (total − проведено) разворачиваются по текущему слоту,
    начиная с ближайшего слот-дня СТРОГО ПОСЛЕ последнего проведённого урока
    (напр. последний урок в СБ 04.07 → следующий в СБ 11.07). Будущие строки со
    временем в прошлом читаются как overdue «надо заполнить».

⚠️ РАЗРУШИТЕЛЬНО и re-runnable: каждая пересборка начисто удаляет план группы
(reset) и разворачивает будущее заново → ПЕРЕЗАПИСЫВАЕТ ручные операции будущего
(переносы/отмены/смену преподавателя). Результат today-независим: даты зависят от
ФАКТОВ, а не от даты прогона (повтор без новых фактов даёт тот же план). Группы
без старта/total/слотов пропускаются (data-quality сигнал).

--dry-run: ничего не пишет, показывает, сколько строк было бы записано.
--reset:   устарел (reset встроен в пересборку) — игнорируется с предупреждением.

Гонять на dev-БД (eduson), не на eduson_test.
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from apps.scheduling import repository

# Причины, по которым план построить нельзя (группа пропускается, план не трогаем).
_BLOCKING_REASONS = ('no_start_date', 'no_total_lessons', 'no_slots')


class Command(BaseCommand):
    help = 'Пересборка planned_lessons из фактов (прошлое=факты, будущее от сегодня).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Не писать в БД: только показать, сколько строк было бы записано.',
        )
        parser.add_argument(
            '--reset', action='store_true',
            help='УСТАРЕЛ: пересборка всегда идёт начисто (reset встроен). Игнорируется.',
        )

    def handle(self, *args, **opts):
        dry = opts['dry_run']
        if opts['reset']:
            self.stdout.write(self.style.WARNING(
                '--reset устарел и игнорируется: пересборка всегда начисто (reset встроен).'
            ))
        if not dry:
            self.stdout.write(self.style.WARNING(
                'ВНИМАНИЕ: пересборка перестраивает будущее от ТЕКУЩЕГО момента и '
                'ПЕРЕЗАПИСЫВАЕТ ручные операции будущего (переносы/отмены/смену препода).'
            ))

        groups = repository.active_groups()
        ids = [g['id'] for g in groups]
        slots_map = repository.slots_by_group(ids)     # батч, без N+1
        facts_map = repository.facts_by_group(ids)      # батч, без N+1

        processed = 0
        rows_written = 0
        skipped: list[tuple[str, str]] = []

        for g in groups:
            gid = g['id']
            g_norm = {
                'group_start_date': g['group_start_date'],
                'total_lessons': g['total_lessons'],
                'lesson_duration_minutes': g['lesson_duration_minutes'],
                'teacher_id': g['teacher_pk'],
            }
            result = repository.rebuild_group_plan(
                gid, g_norm, slots_map.get(gid, []), facts_map.get(gid, []),
                dry_run=dry,
            )
            if result['written'] == 0 and result['reason'] in _BLOCKING_REASONS:
                skipped.append((g['name'], result['reason']))
                continue
            rows_written += result['written']
            processed += 1

        self._report(
            dry=dry, total_groups=len(groups), processed=processed,
            rows_written=rows_written, skipped=skipped,
        )

    def _report(self, *, dry, total_groups, processed, rows_written, skipped):
        mode = 'DRY-RUN (запись отключена)' if dry else 'запись в БД'
        self.stdout.write(self.style.MIGRATE_HEADING(
            f'Пересборка planned_lessons — {mode}'
        ))
        label = 'Строк было бы записано' if dry else 'Строк записано'
        self.stdout.write(f'  Активных групп:            {total_groups}')
        self.stdout.write(f'  Обработано:                {processed}')
        self.stdout.write(f'  {label}: {rows_written}')
        self.stdout.write(f'  Пропущено (не построить):  {len(skipped)}')

        if skipped:
            self.stdout.write(self.style.WARNING('  Пропущенные группы:'))
            for name, reason in skipped:
                self.stdout.write(f'    - {name}: {reason}')

        self.stdout.write(self.style.SUCCESS('Готово.'))
