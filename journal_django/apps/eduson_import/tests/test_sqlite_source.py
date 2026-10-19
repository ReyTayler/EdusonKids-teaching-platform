"""EdusonSource: база Eduson читается строго на чтение, видит данные из WAL и не
пропускает в SQL ничего, кроме имён из белого списка."""
from __future__ import annotations

import hashlib
import shutil
import sqlite3

import pytest

from apps.eduson_import.sqlite_source import TABLES, EdusonSource
from apps.eduson_import.tests.factory import EdusonDB


def _digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def okk(tmp_path):
    with EdusonDB(tmp_path / 'okk.db') as db:
        db.teacher(2, 'Борис Тестов')
        db.teacher(1, 'Анна Тестова', role='admin')
        db.group(10, 'ТС1001', 'python', 1)
        db.renewal(10, 'contacted')
    return tmp_path / 'okk.db'


def test_rows_sorted_by_primary_key_by_default(okk):
    with EdusonSource(okk) as src:
        rows = src.rows('teachers')
        assert [r['id'] for r in rows] == [1, 2]
        assert rows[0]['name'] == 'Анна Тестова' and rows[0]['role'] == 'admin'
        # у renewals ключ — group_id, а не id
        assert src.rows('renewals') == [
            {'group_id': 10, 'status': 'contacted', 'note': '', 'updated_at': '2026-07-01T10:00:00.000Z',
             'updated_by': 'Тестовый админ'},
        ]


def test_rows_explicit_order_by_column(okk):
    with EdusonSource(okk) as src:
        assert [r['name'] for r in src.rows('teachers', order_by='name')] == ['Анна Тестова', 'Борис Тестов']


def test_count(okk):
    with EdusonSource(okk) as src:
        assert src.count('teachers') == 2
        assert src.count('sessions') == 0


@pytest.mark.parametrize('table', ['sqlite_master', 'teachers; DROP TABLE teachers', 'Teachers', ''])
def test_unknown_table_rejected(okk, table):
    with EdusonSource(okk) as src:
        with pytest.raises(ValueError):
            src.rows(table)
        with pytest.raises(ValueError):
            src.count(table)
        assert src.count('teachers') == 2


@pytest.mark.parametrize('order_by', ['nope', 'id; DROP TABLE teachers', 'name DESC', '1'])
def test_unknown_order_column_rejected(okk, order_by):
    with EdusonSource(okk) as src:
        with pytest.raises(ValueError):
            src.rows('teachers', order_by=order_by)


def test_whitelist_matches_legacy_schema(okk):
    con = sqlite3.connect(okk)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' "
                                       "AND name NOT LIKE 'sqlite_%'")}
    con.close()
    assert TABLES == names


def test_cannot_write_and_file_untouched(okk):
    before = _digest(okk)
    with EdusonSource(okk) as src:
        with pytest.raises(sqlite3.OperationalError):
            src.con.execute("INSERT INTO teachers (id,name,role,active) VALUES (3,'x','teacher',1)")
        with pytest.raises(sqlite3.OperationalError):
            src.con.execute('DELETE FROM teachers')
    assert _digest(okk) == before
    with EdusonSource(okk) as src:
        assert src.count('teachers') == 2


def test_missing_file_not_created(tmp_path):
    path = tmp_path / 'absent.db'
    with pytest.raises(FileNotFoundError):
        EdusonSource(path)
    assert not path.exists()


def test_closed_after_context(okk):
    with EdusonSource(okk) as src:
        pass
    with pytest.raises(sqlite3.ProgrammingError):
        src.count('teachers')


def test_reads_uncheckpointed_wal_of_copied_database(tmp_path):
    """Копия Eduson лежит как okk.db + okk.db-wal: часть данных только в WAL.
    Чтение обязано их видеть и не трогать ни основной файл, ни WAL."""
    live = tmp_path / 'live'
    copy = tmp_path / 'copy'
    live.mkdir()
    copy.mkdir()
    db = EdusonDB(live / 'okk.db', wal=True)
    db.con.execute('PRAGMA wal_checkpoint(TRUNCATE)')   # схема — в основном файле
    db.teacher(1, 'Анна Тестова')
    db.teacher(2, 'Борис Тестов')
    db.commit()                                         # строки — только в WAL
    # копируем, пока писатель открыт (иначе close() сольёт WAL в основной файл)
    shutil.copy(live / 'okk.db', copy / 'okk.db')
    shutil.copy(live / 'okk.db-wal', copy / 'okk.db-wal')
    db.close()

    main_before, wal_before = _digest(copy / 'okk.db'), _digest(copy / 'okk.db-wal')
    with sqlite3.connect((copy / 'okk.db').resolve().as_uri() + '?immutable=1', uri=True) as naive:
        # предохранитель теста: без WAL строк не видно, значит, сценарий настоящий
        assert naive.execute('SELECT COUNT(*) FROM teachers').fetchone()[0] == 0
    naive.close()

    with EdusonSource(copy / 'okk.db') as src:
        assert [r['name'] for r in src.rows('teachers')] == ['Анна Тестова', 'Борис Тестов']
    assert _digest(copy / 'okk.db') == main_before
    assert _digest(copy / 'okk.db-wal') == wal_before


def test_path_with_spaces_hash_and_cyrillic(tmp_path):
    # '#' и '%' в пути без экранирования SQLite принял бы за часть URI ('?' запрещён в Windows)
    folder = tmp_path / 'копия Eduson #1 %20'
    folder.mkdir()
    with EdusonDB(folder / 'okk.db') as db:
        db.teacher(1, 'Анна Тестова')
    with EdusonSource(folder / 'okk.db') as src:
        assert src.count('teachers') == 1
