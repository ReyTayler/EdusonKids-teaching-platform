"""Чтение базы старой системы Eduson (SQLite) строго на чтение.

РЕЖИМ ОТКРЫТИЯ: URI `file:...?mode=ro`, без `immutable`.
Eduson работает в WAL (db.js: journal_mode = WAL), и копия базы лежит парой
okk.db + okk.db-wal: последние изменения есть только в WAL. Режим mode=ro
читает WAL и ничего не пишет ни в основной файл, ни в WAL (checkpoint требует
записи, read-only соединение его не делает). `immutable=1` WAL игнорирует —
импорт молча потерял бы свежие данные (это проверяет тест). Единственный след
чтения — служебный индекс okk.db-shm, который SQLite создаёт рядом, если его
нет: данных в нём нет, исходные файлы он не меняет.
Дополнительно `PRAGMA query_only` — вторая защита от записи на случай, если
файл всё же откроется на запись (например, при ошибке в URI).

SQL: имя таблицы — только из белого списка TABLES, колонка сортировки — только
из фактических колонок таблицы (PRAGMA table_info); значения в запросах не
подставляются вовсе.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Все таблицы legacy-схемы (migrate() в legacy/server/src/db.js).
TABLES = frozenset({
    'teachers', 'users', 'student_groups', 'participants', 'sessions',
    'lesson_reports', 'notifications', 'audit_log', 'renewals',
})


class EdusonSource:
    """Источник строк Eduson. Закрывать обязательно: `with EdusonSource(path) as src:`."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        # Проверяем заранее: mode=ro и так не создаст файл, но ошибка SQLite
        # «unable to open database file» ничего не говорит человеку у консоли.
        if not self.path.is_file():
            raise FileNotFoundError(f'База Eduson не найдена: {self.path}')
        # as_uri() экранирует '?', '#', '%' и пробелы в пути: иначе часть пути
        # SQLite принял бы за параметры URI.
        self.con = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True)
        self.con.row_factory = sqlite3.Row
        self.con.execute('PRAGMA query_only = ON')
        self._columns: dict[str, tuple[set[str], list[str]]] = {}

    def __enter__(self) -> EdusonSource:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        self.con.close()

    def _table(self, table: str) -> tuple[set[str], list[str]]:
        """(колонки, первичный ключ) таблицы из белого списка."""
        if table not in TABLES:
            raise ValueError(f'Неизвестная таблица Eduson: {table!r}')
        if table not in self._columns:
            info = self.con.execute(f'PRAGMA table_info("{table}")').fetchall()
            pk = [r['name'] for r in sorted(info, key=lambda r: r['pk']) if r['pk']]
            self._columns[table] = ({r['name'] for r in info}, pk)
        return self._columns[table]

    def rows(self, table: str, order_by: str | None = None) -> list[dict]:
        """Все строки таблицы в детерминированном порядке.

        order_by=None — по первичному ключу (у renewals это group_id, а не id);
        иначе по указанной колонке с тай-брейком по rowid: одинаковые значения
        не должны давать разный порядок от запуска к запуску.
        """
        columns, pk = self._table(table)
        if order_by is None:
            order = ', '.join(f'"{c}"' for c in pk) or 'rowid'
        elif order_by in columns:
            order = f'"{order_by}", rowid'
        else:
            raise ValueError(f'Нет колонки {order_by!r} в таблице {table}')
        return [dict(r) for r in self.con.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]

    def count(self, table: str) -> int:
        self._table(table)
        return self.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
