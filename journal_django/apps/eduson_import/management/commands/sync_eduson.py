"""
Мягкое обновление данных Eduson из свежего снимка старой системы — без подмены базы.

    python manage.py sync_eduson /путь/okk.db
    python manage.py sync_eduson /путь/okk.db --as-of 2026-10-20 --sheets-dir /путь/sheets-…
    python manage.py sync_eduson /путь/okk.db --dry-run         # всё выполнить и откатить

Запускается скриптом scripts/refresh_from_prod.sh --soft (кнопка «Обновить данные с
прода» в разделе «Синхро»). Решение владельца 2026-10-20: обновляются только учебные
данные — ученики, группы, уроки, отзывы и всё, что с уроками связано. Спека:
EdusonPlatform/docs/specs/2026-10-20-eduson-sync-soft-refresh-design.md.

ЧТО МЕНЯЕТСЯ:
  * направления и преподаватели — по связям EdusonLink обновляются на месте, новые заводятся;
  * учётки — только новые; существующие (пароль, 2FA, роль) не трогаются, суперадмин
    не назначается;
  * группы и ученики — обновляются на месте, id не меняются (на них ссылаются сделки
    продлений и задачи); группа, исчезнувшая из старой системы, уходит в архив;
  * членства, уроки, посещаемость, план, отзывы групп старой системы — удаляются и
    заливаются заново теми же шагами, что у полного импорта (apps.eduson_import.sync).
НЕ ТРОГАЕТ: продления, задачи, настройки, журнал ИБ, группы и учеников, заведённых в
платформе. Записанное в платформе по группам старой системы (уроки, отзывы) заменяется:
источник правды — старая система.

ВСЁ ИЛИ НИЧЕГО — одна transaction.atomic: ошибка данных или несошедшаяся сверка
(scope='sync') откатывают всё, база остаётся как была. Соединения не рвутся —
перезапускать runserver не нужно.

ТРЕБУЕТ СВЯЗЕЙ. Таблицу EdusonLink пишет полный импорт; база, собранная до неё, —
сначала полная пересборка (bash scripts/refresh_from_prod.sh без флагов).

ВЫВОД — без ПДн, как у import_eduson: id Eduson, коды групп, числа.
"""
from __future__ import annotations

import pghistory
from django.core.management.base import CommandError
from django.db import DatabaseError, transaction

from apps.audit.services import log_event
from apps.dashboard.registry_service import invalidate_registry_cache
from apps.dashboard.services import invalidate_finance_cache
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.links import has_links, save_links
from apps.eduson_import.management.commands.import_eduson import Command as ImportCommand
from apps.eduson_import.plans import fit_plans_to_course
from apps.eduson_import.reconcile import reconcile
from apps.eduson_import.sheets import load_sheets
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_memberships, sync_groups
from apps.eduson_import.steps.lessons import import_feedback, import_lessons
from apps.eduson_import.steps.people import sync_people
from apps.eduson_import.sync import facts_without_history, purge_group_facts, restore_manual_payroll
from apps.payroll.recalc import recalculate

# Строка итога — её ищут скрипт обновления и кнопка в «Синхро» (apps.sync.prod_refresh).
DONE_LINE = 'Мягкое обновление Eduson завершено.'


class _ReconcileFailed(Exception):
    """Сверка не сошлась — выйти из atomic с откатом."""


class Command(ImportCommand):
    help = 'Мягкое обновление учебных данных Eduson из SQLite (okk.db) без подмены базы.'

    def add_arguments(self, parser):
        parser.add_argument('sqlite_path', help='Путь к okk.db (рядом может лежать okk.db-wal).')
        parser.add_argument('--as-of', metavar='ГГГГ-ММ-ДД',
                            help='День обновления (как у import_eduson). По умолчанию — сегодня (МСК).')
        parser.add_argument('--sheets-dir', metavar='КАТАЛОГ',
                            help='CSV вкладок «Июнь 2026»/«Июнь 2027» таблиц Eduson (как у import_eduson).')
        parser.add_argument('--dry-run', action='store_true',
                            help='Выполнить всё и откатить: показать сверку, ничего не записав.')

    def handle(self, *args, sqlite_path, as_of=None, dry_run=False, sheets_dir=None, **options):
        day = self._as_of(as_of)
        sheet_marks = None
        if sheets_dir:
            try:
                sheet_marks = load_sheets(sheets_dir)
            except ImportDataError as e:
                raise CommandError(f'Таблицы Eduson: {e}') from None
        try:
            src = EdusonSource(sqlite_path)
        except FileNotFoundError as e:
            raise CommandError(str(e)) from None
        with src:
            try:
                with transaction.atomic():
                    if not has_links():
                        raise CommandError('В базе нет связей со старой системой (таблица eduson_links пуста) — '
                                           'мягкое обновление невозможно. Сначала полная пересборка: '
                                           'bash scripts/refresh_from_prod.sh')
                    ctx, lines, errors, warnings, purged = self._sync(src, day, sheet_marks)
                    self._print(lines, warnings, errors, ctx)
                    if errors:
                        raise _ReconcileFailed
                    # Аудит — в той же транзакции: пробный прогон и откат его не оставляют.
                    log_event('eduson.sync', meta={
                        'as_of': day.isoformat(), 'sheets': sheet_marks is not None,
                        'groups': len(ctx.group_by_eduson), 'vanished_groups': len(ctx.vanished_groups),
                        'lessons': len(ctx.lesson_by_session), 'purged': purged,
                    })
                    if dry_run:
                        transaction.set_rollback(True)
                    else:
                        transaction.on_commit(_invalidate_caches)
            except _ReconcileFailed:
                raise CommandError('Сверка не сошлась, всё откатано — база осталась как была.') from None

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'Пробный прогон: сверка сошлась, в базе ничего не изменилось (всё откатано).'))
        else:
            self.stdout.write(self.style.SUCCESS(DONE_LINE))

    def _sync(self, src: EdusonSource, day, sheet_marks):
        """Шаги и сверка → (ctx, lines, errors, warnings, purged). Ошибки — CommandError
        без значений полей (как у импорта): транзакция вокруг откатится."""
        step = 'анализ данных Eduson'
        try:
            with pghistory.context(operation='eduson.sync', method='COMMAND',
                                   url='manual/sync-eduson', note='Мягкое обновление Eduson'):
                ctx = ImportContext.from_source(src, day, sheet_marks)
                step = 'люди и справочники'
                sync_people(src, ctx)
                step = 'группы и ученики'
                members = sync_groups(src, ctx)
                groups = {g['id']: g for g in src.rows('student_groups')}
                # Исчезнувшие группы — тоже в охвате: их факты удаляются, а пересчёт
                # убирает будущие начисления за отмены, которых больше нет.
                run_groups = sorted({*ctx.group_by_eduson.values(), *ctx.vanished_groups})
                with facts_without_history():
                    step = 'удаление прежних уроков'
                    purged, manual_pay = purge_group_facts(run_groups)
                    step = 'членства'
                    import_memberships(src, groups, members, ctx)
                    step = 'уроки и план'
                    import_lessons(src, ctx)
                    step = 'отзывы'
                    import_feedback(src, ctx)
                # Сверка — вне блока без журнала: она только читает, а сырой SQL проверки
                # здоровья плана (именованные параметры) не уживается с префиксом, который
                # pgtrigger.ignore дописывает к каждому запросу (позиционные параметры).
                step = 'сверка'
                lines, errors, warnings = reconcile(src, ctx, scope='sync')
                if not errors:
                    with facts_without_history():
                        step = 'план под длину курса'
                        fit_plans_to_course(ctx)
                        # Зарплата — после плана: маркеры отмен уже на месте, к ним
                        # привязываются начисления-сироты (ручные правки отмен).
                        # Без журнала: начисления уроков создаются заново на каждом
                        # синке (удалены вместе с уроками) — это замена, не правка.
                        step = 'зарплата'
                        pay = recalculate(group_ids=run_groups)
                        restored, lost = restore_manual_payroll(manual_pay, ctx)
                        ctx.report.append(
                            f'зарплата: начислений {pay["lessons"] + pay["cancellations"]}, '
                            f'ручных восстановлено {restored}, не найдено {lost}, '
                            f'удалено начислений за отмены {pay["deleted"]}')
                    step = 'связи'
                    save_links(ctx)
            for table, n in purged.items():
                ctx.report.append(f'удалено перед заливкой: {table} — {n}')
            return ctx, lines, errors, warnings, purged
        except ImportDataError as e:
            raise CommandError(f'Данные Eduson не ложатся на модель (шаг «{step}»): {e}. Всё откатано.') from e
        except DatabaseError as e:
            constraint = getattr(getattr(e.__cause__, 'diag', None), 'constraint_name', None)
            raise CommandError(f'Ошибка базы ({type(e).__name__}) на шаге «{step}»'
                               + (f', ограничение {constraint}' if constraint else '')
                               + '. Всё откатано; подробности — с --traceback.') from e
        except (ValueError, KeyError) as e:
            raise CommandError(f'Нарушена связность данных Eduson на шаге «{step}» ({type(e).__name__}). '
                               'Всё откатано; подробности — с --traceback.') from e


def _invalidate_caches() -> None:
    """После коммита: сводки дашборда пересчитаются с обновлёнными уроками."""
    invalidate_finance_cache()
    invalidate_registry_cache()
