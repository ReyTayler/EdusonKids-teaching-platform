"""
Построитель «Отчёта по переходимости» за месяц.

Два листа одинаковой формы — «Направления» и «Преподаватели». Строка блока =
сущность × показатель, колонки = ВСЕ циклы продлений подряд:

    Название | Показатель | Детей за месяц | Итого | Ц1 | Ц2 | … | Ц34

Циклы разворачиваются полностью, включая те, где никого нет: сетка фиксирована
по максимальному циклу в базе, а не по максимуму выбранного месяца. Иначе
ширина таблицы прыгала бы от месяца к месяцу и «Ц12» в июне означала бы не то
же, что «Ц12» в июле — месяцы стало бы нельзя класть рядом.

ТРИ ПОКАЗАТЕЛЯ в блоке каждой сущности:

  • Продлились — сделка закрыта как «Продлён» в этом месяце;
  • Ушли       — сделка закрыта как «Ушёл» в этом месяце;
  • Зависли    — цикл должен был решиться в этом месяце, но сделка до сих пор
                 открыта и просрочена (см. STUCK_AFTER_DAYS).

Третья строка обязательна, и вот почему. Отметок «Ушёл» в базе почти нет —
два десятка на всю историю школы: ушедшего обычно просто перестают вести, а
сделка остаётся висеть в «Ждём продление». Показав только «Ушли», отчёт
выглядел бы так, будто школа не теряет детей вовсе. Настоящая потеря — это
зависшая сделка.

Цикл = один оплаченный абонемент = 4 урока (`apps.renewals.cycle
.LESSONS_PER_CYCLE`), поэтому у каждой колонки в подписи стоит и номер цикла,
и позиция в курсе: цикл 9 — это уроки 33–36, конец стандартного 36-урочного
курса.

ПРИВЯЗКА К НАПРАВЛЕНИЮ И ПРЕПОДАВАТЕЛЮ. `renewal_deal` привязана к ученику и
циклу — ни направления, ни группы в ней нет вовсе. Связываем через занятия
ТОГО ЖЕ МЕСЯЦА: продление засчитывается тем направлениям и преподавателям, у
которых ребёнок в этом месяце реально занимался. Если уроков в месяце не было
(заморозка, оплатил вперёд) — откатываемся на его членства в группах, иначе
такое продление потерялось бы вовсе.

Отсюда следствие, которое надо помнить: ребёнок, занимающийся у двух
преподавателей, засчитывается обоим. Сумма строк по колонке БОЛЬШЕ школьного
итога, складывать её нельзя. Итог школы посчитан отдельным блоком сверху.
"""
from __future__ import annotations

import io
from collections import defaultdict

from django.db import connection

from apps.renewals.cycle import LESSONS_PER_CYCLE

# Открытая просроченная сделка считается потерей, а не «в работе»: цикл
# продления укладывается в месяц, всё что висит дольше — уже не решается.
STUCK_AFTER_DAYS = 30

FONT_NAME = 'Arial'

# Порядок строк в блоке сущности. Ключ → подпись.
MEASURES = [
    ('won', 'Продлились'),
    ('lost', 'Ушли'),
    ('stuck', 'Зависли'),
]

_NOTES = [
    'Циклы развёрнуты полностью, включая пустые. Цикл = 1 абонемент = 4 урока, '
    'поэтому цикл 9 — это уроки 33–36, конец стандартного 36-урочного курса.',
    '«Зависли» — цикл должен был решиться в этом месяце, но сделка до сих пор открыта и '
    f'просрочена больше чем на {STUCK_AFTER_DAYS} дней. Строка обязательна: отметок «Ушёл» '
    'в базе почти нет, ушедшего обычно просто перестают вести — без этой строки отчёт '
    'выглядел бы так, будто школа не теряет детей.',
    'Продление засчитывается тем направлениям и преподавателям, у которых ребёнок занимался '
    'в этом месяце. Занимающийся у двоих попадает в обе строки, поэтому сумма по колонке '
    'больше школьного итога — складывать её нельзя, итог посчитан отдельным блоком сверху.',
]


def month_bounds(month: str) -> tuple[str, str]:
    """'YYYY-MM' → ('YYYY-MM-01', 'YYYY-MM-<последний день>'), обе границы включительно."""
    import datetime

    year, mon = int(month[:4]), int(month[5:7])
    first = datetime.date(year, mon, 1)
    nxt = datetime.date(year + (1 if mon == 12 else 0), (mon % 12) + 1, 1)
    return first.isoformat(), (nxt - datetime.timedelta(days=1)).isoformat()


# ---------------------------------------------------------------------------
# Данные
# ---------------------------------------------------------------------------

def _max_cycle() -> int:
    """Максимальный цикл в базе — ширина сетки, одинаковая для всех месяцев."""
    with connection.cursor() as cur:
        cur.execute('SELECT COALESCE(MAX(cycle_no), 1) FROM renewal_deal')
        return cur.fetchone()[0]


def _events(month: str) -> list[dict]:
    """
    События месяца: продления, уходы и зависания.

    Продление и уход относятся к месяцу закрытия сделки (`outcome_at`).
    Зависание — к месяцу ОПОРНОЙ ДАТЫ (`due_at`, иначе `stage_entered_at`),
    то есть к месяцу, когда цикл должен был решиться: «зависание» — не событие
    с датой, а несостоявшееся решение, и относить его надо туда, где решения
    ждали.
    """
    date_from, date_to = month_bounds(month)
    with connection.cursor() as cur:
        cur.execute(f"""
            SELECT d.student_id, d.cycle_no, st.kind AS measure
              FROM renewal_deal d
              JOIN renewal_stage st ON st.id = d.stage_id
             WHERE st.kind IN ('won', 'lost')
               AND d.outcome_at IS NOT NULL
               AND (d.outcome_at AT TIME ZONE 'Europe/Moscow')::date BETWEEN %s AND %s

            UNION ALL

            SELECT d.student_id, d.cycle_no, 'stuck' AS measure
              FROM renewal_deal d
              JOIN renewal_stage st ON st.id = d.stage_id
             WHERE d.outcome_at IS NULL
               AND COALESCE(d.due_at, d.stage_entered_at::date) BETWEEN %s AND %s
               AND COALESCE(d.due_at, d.stage_entered_at::date)
                   < current_date - INTERVAL '{STUCK_AFTER_DAYS} days'
        """, [date_from, date_to, date_from, date_to])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _links_from_lessons(month: str) -> tuple[dict, dict]:
    """student_id → направления / преподаватели ПО ЗАНЯТИЯМ месяца."""
    date_from, date_to = month_bounds(month)
    directions: dict[int, dict[int, str]] = defaultdict(dict)
    teachers: dict[int, dict[int, str]] = defaultdict(dict)

    with connection.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT la.student_id,
                            dir.id AS direction_id, dir.name AS direction_name,
                            t.id AS teacher_id, t.name AS teacher_name
              FROM lesson_attendance la
              JOIN lessons l       ON l.id = la.lesson_id
              JOIN groups g        ON g.id = l.group_id
              JOIN directions dir  ON dir.id = g.direction_id
              JOIN teachers t      ON t.id = l.teacher_id
             WHERE l.lesson_date BETWEEN %s AND %s
        """, [date_from, date_to])
        for sid, did, dname, tid, tname in cur.fetchall():
            directions[sid][did] = dname
            teachers[sid][tid] = tname
    return directions, teachers


def _links_from_memberships() -> tuple[dict, dict]:
    """
    Запасная привязка — по членствам в группах.

    Нужна тем, у кого в месяце не было ни одного занятия (заморозка, оплата
    вперёд): без отката их продление не попало бы ни в одну строку и итог по
    направлениям разошёлся бы со школьным.
    """
    directions: dict[int, dict[int, str]] = defaultdict(dict)
    teachers: dict[int, dict[int, str]] = defaultdict(dict)

    with connection.cursor() as cur:
        cur.execute("""
            SELECT DISTINCT m.student_id,
                            dir.id AS direction_id, dir.name AS direction_name,
                            t.id AS teacher_id, t.name AS teacher_name
              FROM group_memberships m
              JOIN groups g       ON g.id = m.group_id
              JOIN directions dir ON dir.id = g.direction_id
              JOIN teachers t     ON t.id = g.teacher_id
        """)
        for sid, did, dname, tid, tname in cur.fetchall():
            directions[sid][did] = dname
            teachers[sid][tid] = tname
    return directions, teachers


def _blank_block(name: str, cycles: list[int]) -> dict:
    return {
        'name': name,
        'students': set(),
        'counts': {key: {c: 0 for c in cycles} for key, _label in MEASURES},
    }


def _build_side(events, month_links, fallback_links, month_students, cycles) -> list[dict]:
    """Свернуть события по одному измерению (направления или преподаватели)."""
    blocks: dict[int, dict] = {}

    # Детей за месяц — по фактическим занятиям, а не по членствам: членство
    # остаётся у замороженного, и такой ребёнок раздувал бы базу.
    for sid, entities in month_students.items():
        for eid, name in entities.items():
            blocks.setdefault(eid, _blank_block(name, cycles))['students'].add(sid)

    for event in events:
        sid = event['student_id']
        entities = month_links.get(sid) or fallback_links.get(sid) or {}
        for eid, name in entities.items():
            block = blocks.setdefault(eid, _blank_block(name, cycles))
            block['counts'][event['measure']][event['cycle_no']] += 1

    rows = []
    for eid, block in blocks.items():
        totals = {key: sum(block['counts'][key].values()) for key, _label in MEASURES}
        rows.append({
            'id': eid,
            'name': block['name'],
            'students': len(block['students']),
            'counts': block['counts'],
            'totals': totals,
        })
    rows.sort(key=lambda r: (-r['students'], r['name']))
    return rows


def _school_total(events, cycles) -> dict:
    """Итог школы: каждая сделка считается ОДИН раз, без двойного счёта."""
    counts = {key: {c: 0 for c in cycles} for key, _label in MEASURES}
    students = set()
    for event in events:
        counts[event['measure']][event['cycle_no']] += 1
        students.add(event['student_id'])
    return {
        'name': 'Итого по школе',
        'students': len(students),
        'counts': counts,
        'totals': {key: sum(counts[key].values()) for key, _label in MEASURES},
    }


def collect(month: str) -> dict:
    cycles = list(range(1, _max_cycle() + 1))
    events = _events(month)
    dir_month, teacher_month = _links_from_lessons(month)
    dir_fallback, teacher_fallback = _links_from_memberships()

    return {
        'month': month,
        'cycles': cycles,
        'total': _school_total(events, cycles),
        'directions': _build_side(events, dir_month, dir_fallback, dir_month, cycles),
        'teachers': _build_side(events, teacher_month, teacher_fallback, teacher_month, cycles),
        'event_count': len(events),
    }


# ---------------------------------------------------------------------------
# Рендер
# ---------------------------------------------------------------------------

def _font(bold=False, size=11, color=None, italic=False):
    from openpyxl.styles import Font
    return Font(name=FONT_NAME, bold=bold, size=size, color=color, italic=italic)


_FIXED_COLUMNS = [('Название', 32), ('Показатель', 14), ('Детей за месяц', 15), ('Итого', 10)]

_MEASURE_COLORS = {'won': '15803D', 'lost': 'DC2626', 'stuck': 'B45309'}


def _write_sheet(ws, title: str, cycles: list[int], total: dict, rows: list[dict]) -> None:
    from openpyxl.styles import Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    ncols = len(_FIXED_COLUMNS) + len(cycles)

    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=min(ncols, 12))
    head_cell = ws.cell(row=1, column=1, value=title)
    head_cell.font = _font(bold=True, size=14)

    row = 2
    for note in _NOTES:
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=min(ncols, 12))
        cell = ws.cell(row=row, column=1, value=note)
        cell.font = _font(size=9, italic=True, color='808080')
        cell.alignment = Alignment(wrap_text=True, vertical='top')
        ws.row_dimensions[row].height = 28
        row += 1

    header = row + 1
    for ci, (label, width) in enumerate(_FIXED_COLUMNS, start=1):
        ws.cell(row=header, column=ci, value=label)
        ws.column_dimensions[get_column_letter(ci)].width = width
    for i, cycle in enumerate(cycles):
        ci = len(_FIXED_COLUMNS) + 1 + i
        # Вторая строка подписи — позиция в курсе: без неё номер цикла ни о чём
        # не говорит, а с ней видно, что провал на 9-м — это конец курса.
        ws.cell(row=header, column=ci, value=f'Ц{cycle}\n{cycle * LESSONS_PER_CYCLE} ур.')
        ws.column_dimensions[get_column_letter(ci)].width = 7

    fill = PatternFill('solid', fgColor='4F59F9')
    for ci in range(1, ncols + 1):
        cell = ws.cell(row=header, column=ci)
        cell.fill = fill
        cell.font = _font(bold=True, color='FFFFFF')
        cell.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
    ws.row_dimensions[header].height = 32

    ri = header
    for block in [total, *rows]:
        is_total = block is total
        for mi, (key, label) in enumerate(MEASURES):
            ri += 1
            # Название и число детей — только в первой строке блока: повторять их
            # трижды значит заставить читателя проверять, не разные ли это числа.
            name_cell = ws.cell(row=ri, column=1, value=block['name'] if mi == 0 else None)
            name_cell.font = _font(bold=is_total)
            measure_cell = ws.cell(row=ri, column=2, value=label)
            measure_cell.font = _font(color=_MEASURE_COLORS[key], bold=is_total)
            students_cell = ws.cell(row=ri, column=3,
                                    value=block['students'] if mi == 0 else None)
            students_cell.font = _font(bold=is_total)

            first_cycle_col = len(_FIXED_COLUMNS) + 1
            last_cycle_col = len(_FIXED_COLUMNS) + len(cycles)
            # Итог — формула по строке циклов, а не записанное число: лист обязан
            # сойтись сам с собой, если кто-то поправит ячейку под собой.
            total_cell = ws.cell(
                row=ri, column=4,
                value=f'=SUM({get_column_letter(first_cycle_col)}{ri}:'
                      f'{get_column_letter(last_cycle_col)}{ri})')
            total_cell.font = _font(bold=True)

            for i, cycle in enumerate(cycles):
                value = block['counts'][key][cycle]
                cell = ws.cell(row=ri, column=first_cycle_col + i, value=value)
                cell.font = _font(bold=is_total)
                # Ноль показывается как «–»: явный ноль в 34 колонках создаёт
                # шум, в котором теряются единицы, а пустая ячейка читается как
                # «нет данных». Прочерк означает «данные есть, значение ноль».
                cell.number_format = '0;-0;–'

        # Полоса между блоками: 34 колонки без разделителя сливаются в кашу.
        ws.cell(row=ri, column=1).border = _bottom_border()

    ws.freeze_panes = ws.cell(row=header + 1, column=len(_FIXED_COLUMNS) + 1)


def _bottom_border():
    from openpyxl.styles import Border, Side
    return Border(bottom=Side(style='thin', color='D0D5DD'))


def render_workbook(data: dict) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()

    ws = wb.active
    ws.title = 'Направления'
    _write_sheet(ws, f'Переходимость по направлениям — {data["month"]}',
                 data['cycles'], data['total'], data['directions'])

    _write_sheet(wb.create_sheet('Преподаватели'),
                 f'Переходимость по преподавателям — {data["month"]}',
                 data['cycles'], data['total'], data['teachers'])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build(month: str) -> tuple[bytes, int, str]:
    """(xlsx-байты, число событий, имя файла). month — 'YYYY-MM'."""
    data = collect(month)
    content = render_workbook(data)
    return content, data['event_count'], f'retention_{month}.xlsx'
