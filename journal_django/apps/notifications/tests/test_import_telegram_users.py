"""Перенос справочника Telegram-аккаунтов из SQLite бота."""
from __future__ import annotations

import sqlite3

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.notifications.models import TelegramUser


@pytest.fixture
def bot_db(tmp_path):
    """Файл-заглушка базы бота с таблицей users в реальной форме."""
    path = tmp_path / 'bot.db'
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id   INTEGER UNIQUE NOT NULL,
            username      TEXT,
            full_name     TEXT,
            is_superadmin BOOLEAN NOT NULL DEFAULT 0,
            is_admin      BOOLEAN NOT NULL DEFAULT 0,
            is_manager    BOOLEAN NOT NULL DEFAULT 0,
            is_teacher    BOOLEAN NOT NULL DEFAULT 0,
            added_by      INTEGER,
            created_at    TEXT NOT NULL
        );
        INSERT INTO users (telegram_id, username, full_name, is_teacher, created_at)
        VALUES (111, 'anna', 'Анна Петрова', 1, '2026-01-01'),
               (222, NULL,   'Без ника',     1, '2026-01-01'),
               (333, '@bob', 'Боб',          0, '2026-01-01');
    """)
    conn.commit()
    conn.close()
    return path


@pytest.mark.django_db
def test_imports_all_accounts_regardless_of_role(bot_db):
    """Переносим всех: человек может быть и менеджером, и преподавателем,
    а роли у бота больше нет — фильтровать не по чему."""
    call_command('import_telegram_users', str(bot_db))

    assert TelegramUser.objects.filter(chat_id__in=[111, 222, 333]).count() == 3
    assert TelegramUser.objects.get(chat_id=111).username == 'anna'


@pytest.mark.django_db
def test_strips_at_sign_from_username(bot_db):
    """В базе бота ник мог сохраниться с собакой — в журнале храним без неё."""
    call_command('import_telegram_users', str(bot_db))
    assert TelegramUser.objects.get(chat_id=333).username == 'bob'


@pytest.mark.django_db
def test_account_without_username_keeps_full_name(bot_db):
    call_command('import_telegram_users', str(bot_db))
    row = TelegramUser.objects.get(chat_id=222)
    assert row.username is None
    assert row.full_name == 'Без ника'


@pytest.mark.django_db
def test_rerun_updates_instead_of_duplicating(bot_db):
    call_command('import_telegram_users', str(bot_db))

    conn = sqlite3.connect(bot_db)
    conn.execute("UPDATE users SET username='anna_new' WHERE telegram_id=111")
    conn.commit()
    conn.close()

    call_command('import_telegram_users', str(bot_db))

    assert TelegramUser.objects.filter(chat_id=111).count() == 1
    assert TelegramUser.objects.get(chat_id=111).username == 'anna_new'


@pytest.mark.django_db
def test_dry_run_writes_nothing(bot_db):
    call_command('import_telegram_users', str(bot_db), '--dry-run')
    assert TelegramUser.objects.count() == 0


@pytest.mark.django_db
def test_missing_file_is_a_clear_error(tmp_path):
    with pytest.raises(CommandError, match='Файл не найден'):
        call_command('import_telegram_users', str(tmp_path / 'нет-такого.db'))


@pytest.mark.django_db
def test_wrong_database_is_a_clear_error(tmp_path):
    """Подсунули не ту базу — сообщение должно объяснять, что именно не так."""
    path = tmp_path / 'other.db'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE something_else (id INTEGER)')
    conn.commit()
    conn.close()

    with pytest.raises(CommandError, match='таблицы users'):
        call_command('import_telegram_users', str(path))


@pytest.fixture
def bot_db_wal(tmp_path):
    """База в режиме WAL — ровно как боевая у бота.

    Это тот случай, что сломал первую версию команды: открытая флагом mode=ro
    WAL-база падает с «attempt to write a readonly database» на обычном SELECT,
    потому что движку нужно создать вспомогательный файл -shm.
    """
    path = tmp_path / 'wal.db'
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript("""
        CREATE TABLE users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username    TEXT,
            full_name   TEXT,
            created_at  TEXT NOT NULL
        );
        INSERT INTO users (telegram_id, username, full_name, created_at)
        VALUES (444, 'wal_user', 'Из WAL-базы', '2026-01-01');
    """)
    conn.commit()
    conn.close()
    return path


@pytest.mark.django_db
def test_reads_wal_database(bot_db_wal):
    call_command('import_telegram_users', str(bot_db_wal))
    assert TelegramUser.objects.get(chat_id=444).username == 'wal_user'


@pytest.mark.django_db
def test_original_file_is_not_modified(bot_db_wal):
    """Читаем копию: боевой файл обязан остаться байт в байт прежним."""
    before = bot_db_wal.read_bytes()
    call_command('import_telegram_users', str(bot_db_wal))
    assert bot_db_wal.read_bytes() == before
