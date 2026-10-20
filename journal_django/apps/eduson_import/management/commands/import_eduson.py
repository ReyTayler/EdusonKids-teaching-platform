"""
Разовый перенос данных Eduson (SQLite старой системы) в пустую базу журнала.

    python manage.py import_eduson /путь/okk.db                  # день импорта — сегодня по Москве
    python manage.py import_eduson /путь/okk.db --as-of 2026-10-18
    python manage.py import_eduson /путь/okk.db --dry-run        # всё выполнить и откатить
    python manage.py import_eduson /путь/okk.db --superadmin <почта admin Eduson> [--superadmin …]
    python manage.py import_eduson /путь/okk.db --sheets-dir /путь/sheets-ГГГГММДД-ЧЧММ

SHEETS-DIR. Необязательно: каталог CSV-выгрузок таблиц Eduson ({course}-2026-06.csv и
{course}-2027-06.csv для scratch/python/vibe/groups — вкладки «Июнь 2026» и «Июнь 2027»;
кладёт scripts/refresh_from_prod.sh). С ним группы с «пройдено до учёта» N > 0
получают уроки до запуска старой системы, отмеченные в таблицах (правило —
analysis.sheet_backfill). Без него — поведение прежнее. Нет каталога или одного из
8 файлов, не та шапка — команда останавливается до импорта.

SUPERADMIN. В Eduson этой роли нет, а в KOTOKOD учётками, 2FA, зарплатой, журналом
ИБ и Синхро управляет только superadmin. --superadmin назначает её перенесённым
учёткам-admin с этой почтой (шаг people); почта не найдена или учётка переносится
преподавателем — импорт останавливается. Без параметра сверка предупреждает.

Спека: docs/specs/2026-10-15-eduson-fork-design.md §5 (решения §5.4 перекрывают 5.1–5.3).

ВСЁ ИЛИ НИЧЕГО. Шаги people → groups → lessons → feedback → renewals и сверка идут
в одной transaction.atomic: ошибка данных, нарушение ограничения БД или
несошедшаяся сверка откатывают всё. Полуимпорт недопустим — повторный запуск
его бы уже не принял (база не пустая).

ПОВТОРНЫЙ ЗАПУСК ЗАПРЕЩЁН. «Данные» — ученики, группы, учётки, направления,
преподаватели, уроки, оплаты. Сиды миграций (воронка продлений, доска задач,
настройки) данными не считаются — они есть в любой свежей базе.

ЖУРНАЛ ИЗМЕНЕНИЙ. Весь импорт — одна операция pghistory 'eduson.import' (как
record_lesson_override), автор — «Система». Откатить импорт через журнал
технически можно, но не нужно: правильный путь — пересоздать пустую базу.

КЭШИ. Шаги пишут bulk_create — post_save не срабатывает, и подписки дашборда
(apps/dashboard/signals.py) кэши не сбросят. Поэтому после коммита команда
сбрасывает их сама теми же функциями (финансовая сводка, реестр).

ВЫВОД — без ПДн: таблица сверки, предупреждения, отчёт импорта и список «на
ручную проверку» содержат только id записей Eduson, коды групп и числа. Тексты
ошибок шагов и сверки из исключений Python (ValueError/KeyError/DatabaseError) не
печатаются — в них бывают значения полей (почта в DETAIL нарушения уникальности);
подробности — флагом --traceback у своей консоли.
"""
from __future__ import annotations

import datetime

import pghistory
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, transaction

from apps.accounts.models import Account
from apps.core.utils.dates import msk_today
from apps.dashboard.registry_service import invalidate_registry_cache
from apps.dashboard.services import invalidate_finance_cache
from apps.directions.models import Direction
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.links import save_links
from apps.eduson_import.plans import fit_plans_to_course
from apps.eduson_import.normalize import norm_email
from apps.eduson_import.reconcile import reconcile
from apps.eduson_import.sheets import load_sheets
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import import_feedback, import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.steps.renewals import import_renewals
from apps.groups.models import Group
from apps.lessons.models import Lesson
from apps.payments.models import Payment
from apps.students.models import Student
from apps.teachers.models import Teacher

# Что считается «данными» целевой базы (сиды миграций сюда не входят).
DATA_MODELS = (Student, Group, Account, Direction, Teacher, Lesson, Payment)

class _ReconcileFailed(Exception):
    """Сверка не сошлась — выйти из atomic с откатом."""


class Command(BaseCommand):
    help = 'Перенос данных Eduson из SQLite (okk.db) в пустую базу журнала.'

    def add_arguments(self, parser):
        parser.add_argument('sqlite_path', help='Путь к okk.db (рядом может лежать okk.db-wal).')
        parser.add_argument('--as-of', metavar='ГГГГ-ММ-ДД',
                            help='День импорта: held позже него — с ошибочным годом (уроки годом раньше) или фиктивные. '
                                 'По умолчанию — сегодня (МСК).')
        parser.add_argument('--superadmin', action='append', default=[], metavar='EMAIL',
                            help='Почта учётки-admin Eduson, которая станет superadmin (можно несколько раз).')
        parser.add_argument('--sheets-dir', metavar='КАТАЛОГ',
                            help='CSV вкладок «Июнь 2026»/«Июнь 2027» таблиц Eduson ({course}-ГГГГ-ММ.csv): '
                                 'уроки до запуска старой системы для групп с пройденным до учёта.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Выполнить всё и откатить: показать сверку, ничего не записав.')

    def handle(self, *args, sqlite_path, as_of=None, dry_run=False, superadmin=(), sheets_dir=None, **options):
        day = self._as_of(as_of)
        sheet_marks = None
        if sheets_dir:
            try:
                sheet_marks = load_sheets(sheets_dir)
            except ImportDataError as e:
                raise CommandError(f'Таблицы Eduson: {e}') from None
        superadmins = tuple(dict.fromkeys(norm_email(e) for e in superadmin if norm_email(e)))
        try:
            src = EdusonSource(sqlite_path)
        except FileNotFoundError as e:
            raise CommandError(str(e)) from None
        with src:
            try:
                with transaction.atomic():
                    if any(model.objects.exists() for model in DATA_MODELS):
                        raise CommandError('База не пустая — импорт отменён (повторный запуск запрещён). '
                                           'Импорт идёт только в свежую базу из миграций.')
                    ctx, lines, errors, warnings = self._run(src, day, superadmins, sheet_marks)
                    self._print(lines, warnings, errors, ctx)
                    if errors:
                        raise _ReconcileFailed
                    if dry_run:
                        transaction.set_rollback(True)
                    else:
                        transaction.on_commit(_invalidate_caches)
            except _ReconcileFailed:
                raise CommandError('Сверка не сошлась, всё откатано.') from None

        if dry_run:
            self.stdout.write(self.style.WARNING(
                'Пробный прогон: сверка сошлась, в базе ничего не изменилось (всё откатано).'))
        else:
            self.stdout.write(self.style.SUCCESS('Импорт Eduson завершён.'))

    @staticmethod
    def _as_of(value: str | None) -> datetime.date:
        today = datetime.date.fromisoformat(msk_today())
        try:
            day = datetime.date.fromisoformat(value) if value else today
        except ValueError:
            raise CommandError(f'--as-of: дата «{value}» не в формате ГГГГ-ММ-ДД') from None
        # Будущий день импорта сделал бы «настоящими» held, которых ещё не было, а
        # проверка здоровья плана сравнивает «проведено» с CURRENT_DATE базы.
        if day > today:
            raise CommandError(f'--as-of: {day.isoformat()} позже сегодняшнего дня ({today.isoformat()}, МСК)')
        return day

    def _run(self, src: EdusonSource, day: datetime.date, superadmins: tuple[str, ...], sheet_marks=None):
        """Шаги и сверка → (ctx, lines, errors, warnings). Ошибки любого этапа —
        CommandError без значений полей: транзакция вокруг откатится."""
        step = 'анализ данных Eduson'
        try:
            with pghistory.context(operation='eduson.import', method='COMMAND',
                                   url='manual/import-eduson', note='Импорт Eduson'):
                ctx = ImportContext.from_source(src, day, sheet_marks)
                ctx.superadmin_emails = superadmins
                steps = (('люди и справочники', import_people), ('группы и ученики', import_groups),
                         ('уроки и план', import_lessons), ('отзывы', import_feedback),
                         ('продления', import_renewals))
                for step, run in steps:
                    run(src, ctx)
                    self.stdout.write(f'· шаг «{step}» выполнен')
                step = 'сверка'
                lines, errors, warnings = reconcile(src, ctx)
                if not errors:
                    # План старой системы покрывает пакет Eduson (12, 24, …), а длина курса
                    # теперь — из направления (решение владельца 2026-10-19). Подгоняем план
                    # штатным resize_plan уже ПОСЛЕ сверки (она сравнивает план с сессиями),
                    # в том же контексте журнала — импорт остаётся одной операцией.
                    step = 'план под длину курса'
                    fit_plans_to_course(ctx)
                    # Соответствия — для мягкого обновления (sync_eduson).
                    step = 'связи для мягкого обновления'
                    save_links(ctx)
            return ctx, lines, errors, warnings
        except ImportDataError as e:
            # Тексты ImportDataError пишутся под консоль: id Eduson, коды групп, без ПДн.
            raise CommandError(f'Данные Eduson не ложатся на модель (шаг «{step}»): {e}. Всё откатано.') from e
        except DatabaseError as e:
            # IntegrityError, DataError и пр.: текст psycopg содержит значения полей
            # (DETAIL: Key (email)=(…), «value too long … 'Имя'») — печатаем только
            # шаг и имя ограничения.
            constraint = getattr(getattr(e.__cause__, 'diag', None), 'constraint_name', None)
            raise CommandError(f'Ошибка базы ({type(e).__name__}) на шаге «{step}»'
                               + (f', ограничение {constraint}' if constraint else '')
                               + '. Всё откатано; подробности — с --traceback.') from e
        except (ValueError, KeyError) as e:
            raise CommandError(f'Нарушена связность данных Eduson на шаге «{step}» ({type(e).__name__}). '
                               'Всё откатано; подробности — с --traceback.') from e

    def _print(self, lines: list[str], warnings: list[str], errors: list[str], ctx: ImportContext) -> None:
        out = self.stdout
        out.write('\nСверка Eduson → KOTOKOD')
        for line in lines:
            out.write(line)
        if warnings:
            out.write('\nПредупреждения (ожидаемые особенности данных):')
            for line in warnings:
                out.write(self.style.WARNING(f'  · {line}'))
        if ctx.report:
            out.write('\nОтчёт импорта:')
            for line in ctx.report:
                out.write(f'  · {line}')
        if ctx.manual_review:
            out.write(f'\nНа ручную проверку ({len(ctx.manual_review)}):')
            for line in ctx.manual_review:
                out.write(self.style.WARNING(f'  · {line}'))
        if errors:
            out.write(self.style.ERROR(f'\nОшибки сверки ({len(errors)}):'))
            for line in errors:
                out.write(self.style.ERROR(f'  · {line}'))


def _invalidate_caches() -> None:
    """После коммита: сводки дашборда пересчитаются с импортированными уроками."""
    invalidate_finance_cache()
    invalidate_registry_cache()
