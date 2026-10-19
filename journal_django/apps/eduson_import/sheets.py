"""Отметки уроков из публичных Google-таблиц Eduson — только чтение CSV-выгрузок с диска.

ЗАЧЕМ. У части групп после правил базы остаётся «пройдено до учёта» N > 0: уроки
до запуска старой системы, которых в её SQLite нет. Часть их отмечена в таблицах
Eduson (по таблице на курс, вкладка на месяц). Импорт берёт оттуда только две
вкладки — «Июнь 2026» (первый месяц учёта) и «Июнь 2027» (её же отмечали по ошибке
на вкладке следующего года, как и held «2027-06-DD» в базе). Правило дозаполнения —
analysis.sheet_backfill; здесь — только разбор файлов.

ФАЙЛЫ. Каталог --sheets-dir с файлами `{course}-{ГГГГ}-{ММ}.csv` (course — курс
Eduson: scratch/python/vibe/groups); их кладёт scripts/refresh_from_prod.sh. Нужны
все 8: без одного файла часть групп молча осталась бы без отметок, поэтому отсутствие
— ошибка, а не пропуск.

ФОРМАТ ВКЛАДКИ (gviz CSV, как читал legacy services/sheetsSync.js). Шапка — первая
ячейка «Название группы»; сколько в шапке строк, gviz решает сам (обычно две, но
бывает, что вторую строку шапки он склеивает с первой и строка группы идёт сразу
второй), поэтому строки шапки не отсчитываются, а отсеиваются по коду. Строка группы: col0 код группы, col1
преподаватель, col2 пакет, col3 осталось, col4 пройдено, с col5 — пары (флажок
TRUE/FALSE, время «ЧЧ:ММ» или пусто) на дни месяца: день = (col − 5) // 2 + 1.
Из строки берутся ТОЛЬКО код и пары: имена преподавателей и прочие ячейки — ПДн, в
импорт и отчёт не попадают. Строки, чей код не похож на код группы (итоги,
подзаголовки), пропускаются — как в legacy (CODE_RE).
"""
from __future__ import annotations

import csv
import datetime
import io
import re
from pathlib import Path

from apps.eduson_import.context import ImportDataError

COURSES = ('scratch', 'python', 'vibe', 'groups')
# (год, месяц) вкладок, которые читает импорт: «Июнь 2026» и «Июнь 2027».
TABS = ((2026, 6), (2027, 6))
HEADER_FIRST_CELL = 'Название группы'
FIRST_DAY_COL = 5

_CODE_RE = re.compile(r'^[А-ЯЁ]{2}\d{3,4}$')
_CODE_PARTS = re.compile(r'^([А-ЯЁ]{2})0*(\d+)')
_SPACES = re.compile(r'\s+')
_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?$')

# (курс, код группы) → {дата по вкладке: время из ячейки или None}
SheetMarks = dict[tuple[str, str], dict[datetime.date, datetime.time | None]]


def norm_code(value) -> str:
    """Код группы как сравнивал legacy (sheetsSync.normCode): верхний регистр, без
    пробелов, без ведущих нулей номера («си 01037» ≡ «СИ1037»)."""
    s = _SPACES.sub('', str(value or '')).upper()
    m = _CODE_PARTS.match(s)
    return m.group(1) + m.group(2) if m else s


def file_name(course: str, year: int, month: int) -> str:
    return f'{course}-{year:04d}-{month:02d}.csv'


def _time(value: str) -> datetime.time | None:
    """Время из ячейки; пусто или не «ЧЧ:ММ» — None (шаг возьмёт слот группы)."""
    m = _TIME_RE.fullmatch(value.strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return None
    return datetime.time(int(m.group(1)), int(m.group(2)))


def parse_tab(text: str, year: int, month: int, what: str) -> dict[str, dict[datetime.date, datetime.time | None]]:
    """CSV одной вкладки → {код группы: {дата: время}} только по флажкам TRUE.

    Флажок на дне, которого в месяце нет (31 июня), игнорируется: дату не
    придумываем. Код встретился дважды — отметки объединяются (время первой строки).
    """
    rows = list(csv.reader(io.StringIO(text.lstrip('﻿'))))
    if not rows or not rows[0] or rows[0][0].strip() != HEADER_FIRST_CELL:
        raise ImportDataError(f'{what}: первая ячейка шапки не «{HEADER_FIRST_CELL}» — это не вкладка месяца')
    out: dict[str, dict[datetime.date, datetime.time | None]] = {}
    for row in rows[1:]:
        if not row or not _CODE_RE.fullmatch(_SPACES.sub('', row[0]).upper()):
            continue
        marks = out.setdefault(norm_code(row[0]), {})
        for col in range(FIRST_DAY_COL, len(row), 2):
            if row[col].strip().upper() != 'TRUE':
                continue
            try:
                day = datetime.date(year, month, (col - FIRST_DAY_COL) // 2 + 1)
            except ValueError:
                continue
            marks.setdefault(day, _time(row[col + 1]) if col + 1 < len(row) else None)
    return out


def load_sheets(directory) -> SheetMarks:
    """Все вкладки TABS всех курсов из каталога. Нет каталога или файла — ImportDataError."""
    root = Path(directory)
    if not root.is_dir():
        raise ImportDataError('--sheets-dir: каталог не найден')
    missing = [file_name(c, y, m) for c in COURSES for y, m in TABS if not (root / file_name(c, y, m)).is_file()]
    if missing:
        raise ImportDataError(f'--sheets-dir: нет файлов {", ".join(missing)}')
    marks: SheetMarks = {}
    for course in COURSES:
        for year, month in TABS:
            name = file_name(course, year, month)
            try:
                text = (root / name).read_text(encoding='utf-8')
            except UnicodeDecodeError:
                raise ImportDataError(f'{name}: не UTF-8') from None
            for code, days in parse_tab(text, year, month, name).items():
                target = marks.setdefault((course, code), {})
                for day, when in days.items():
                    target.setdefault(day, when)
    return marks
