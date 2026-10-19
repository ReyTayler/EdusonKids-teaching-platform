"""
Разовый перенос справочника Telegram-аккаунтов из SQLite бота в журнал.

Бот несколько месяцев накапливал пары «ник ↔ chat_id» в таблице `users`. Без
этого переноса каждому преподавателю пришлось бы заново писать боту, чтобы
появиться в списке привязки — а по @нику писать в личку Bot API не умеет, нужен
именно числовой chat_id.

Переносится ТОЛЬКО справочник. Привязку «преподаватель ↔ аккаунт» команда не
создаёт: сопоставить ФИО из журнала с телеграм-ником автоматически нельзя, это
делает админ руками в карточке преподавателя.

Идемпотентна: повторный запуск обновит имена и ники, дублей не создаст.

    manage.py import_telegram_users /path/to/bot.db
    manage.py import_telegram_users <путь> --dry-run
"""
from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.notifications.models import TelegramUser


class Command(BaseCommand):
    help = 'Перенести справочник Telegram-аккаунтов из SQLite-базы Telegram-бота.'

    def add_arguments(self, parser) -> None:
        parser.add_argument('db_path', help='Путь к SQLite-файлу базы бота')
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Показать, что будет сделано, ничего не записывая.',
        )

    def handle(self, *args, **options) -> None:
        db_path = Path(options['db_path'])
        if not db_path.is_file():
            raise CommandError(f'Файл не найден: {db_path}')

        rows = self._read_bot_users(db_path)
        if not rows:
            self.stdout.write(self.style.WARNING('В базе бота нет пользователей.'))
            return

        created = updated = skipped = 0
        for telegram_id, username, full_name in rows:
            if not telegram_id:
                skipped += 1
                continue
            # full_name в модели журнала обязателен; у бота он мог не заполниться.
            name = (full_name or '').strip() or (username or f'id{telegram_id}')
            nick = (username or '').lstrip('@').strip() or None

            if options['dry_run']:
                exists = TelegramUser.objects.filter(chat_id=telegram_id).exists()
                self.stdout.write(
                    f'{"обновить" if exists else "создать"}: {telegram_id} '
                    f'{"@" + nick if nick else "(без ника)"} — {name}'
                )
                created += 0 if exists else 1
                updated += 1 if exists else 0
                continue

            _obj, was_created = TelegramUser.objects.update_or_create(
                chat_id=telegram_id,
                defaults={'username': nick, 'full_name': name},
            )
            created += int(was_created)
            updated += int(not was_created)

        prefix = '[dry-run] ' if options['dry_run'] else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}создано: {created}, обновлено: {updated}, пропущено: {skipped}'
        ))
        if not options['dry_run']:
            self.stdout.write(
                'Привязки не создавались — выберите аккаунты в карточках '
                'преподавателей (/admin/teachers).'
            )

    @staticmethod
    def _read_bot_users(db_path: Path) -> list[tuple]:
        """
        Читает таблицу users бота, не трогая исходный файл.

        База бота работает в режиме WAL (`PRAGMA journal_mode=WAL`). Открыть её
        флагом mode=ro нельзя: для чтения WAL движку нужно создать вспомогательный
        файл -shm, и он падает с «attempt to write a readonly database» на обычном
        SELECT. Поэтому копируем базу вместе с -wal/-shm во временный каталог и
        читаем копию — оригинал остаётся байт в байт прежним, а незакоммиченный
        хвост WAL корректно доигрывается.
        """
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / db_path.name
            try:
                shutil.copy2(db_path, copy_path)
                # -wal и -shm существуют, только если база открыта или закрыта
                # некорректно; без них копия тоже валидна.
                for suffix in ('-wal', '-shm'):
                    sidecar = db_path.with_name(db_path.name + suffix)
                    if sidecar.is_file():
                        shutil.copy2(sidecar, copy_path.with_name(copy_path.name + suffix))
            except OSError as exc:
                raise CommandError(f'Не удалось прочитать файл базы: {exc}') from exc

            try:
                conn = sqlite3.connect(copy_path)
            except sqlite3.Error as exc:
                raise CommandError(f'Не удалось открыть базу бота: {exc}') from exc
            try:
                return conn.execute(
                    'SELECT telegram_id, username, full_name FROM users'
                ).fetchall()
            except sqlite3.OperationalError as exc:
                raise CommandError(
                    f'В базе нет таблицы users ({exc}). '
                    'Это точно база Telegram-бота?'
                ) from exc
            except sqlite3.Error as exc:
                raise CommandError(f'Не удалось прочитать базу бота: {exc}') from exc
            finally:
                conn.close()
