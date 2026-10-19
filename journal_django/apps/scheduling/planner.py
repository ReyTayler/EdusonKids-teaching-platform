"""
Чистые функции планировщика материализованных плановых занятий — БЕЗ доступа к БД.

Операции над списком плановых строк (PlannedRow): генерация плана из старта/слотов,
разовый перенос, перенос навсегда (сдвиг хвоста на новый день недели), непрерывная
перекладка хвоста (relay_from_date, обходит занятые даты), доп. занятие. Отмена —
не отдельная чистая функция: реализована в repository через маркер + relay_from_date.
Запись результата в БД — в repository.py; здесь только детерминированная логика над
датами, тестируемая в изоляции.

Инвариант всех операций: строки со статусом DONE (проведённые) НЕ трогаются.
`seq`/`lesson_number` (порядок контента) стабильны — двигаются только даты
(и опц. преподаватель). Конвенция day_of_week — Вс=0. Даты — datetime.date/time
без TZ. См. docs/lesson-scheduling.md.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from apps.scheduling.occurrences import (
    PENDING, DONE,
    Slot, _offset_from_monday, _step_for, _walk,
)


@dataclass
class PlannedRow:
    """Материализованная плановая строка (значения, релевантные для логики дат).

    Отражает поля модели PlannedLesson, участвующие в операциях планировщика.
    Запись/чтение ORM — в repository.py; planner оперирует только значениями.
    """
    seq: Optional[int]
    lesson_number: Optional[Decimal]
    scheduled_date: datetime.date
    scheduled_time: datetime.time
    teacher_id: Optional[int] = None
    substitute_teacher_id: Optional[int] = None
    status: str = PENDING
    moved_from_date: Optional[datetime.date] = None
    moved_to_date: Optional[datetime.date] = None
    # Прямая связь план→факт (id проведённого урока). Заполняется бэкфиллом
    # из фактов (link_facts_positional) для done-строк; иначе None.
    fact_lesson_id: Optional[int] = None


@dataclass
class Fact:
    """Лёгкий носитель проведённого урока (значения из lessons.Lesson) для
    позиционной линковки бэкфилла. Собирается в repository, planner не трогает ORM."""
    lesson_date: datetime.date
    teacher_id: Optional[int]
    fact_lesson_id: int


# Активные (непроведённые) статусы, которые операции могут двигать.
_MUTABLE = frozenset({'pending', 'overdue'})


def _far_future(start: datetime.date, total_lessons: int, step: Decimal) -> datetime.date:
    """Верхняя граница генерации для _walk (в неделях, с запасом).

    Число сессий до достижения total = total_lessons / step (полуурок step=0.5 →
    вдвое больше сессий). При >=1 слоте в неделю столько же недель максимум.
    Раньше граница считалась как total_lessons+2 (ошибочно — 1 сессия/неделю),
    из-за чего полуурочные курсы обрезались вдвое."""
    weeks = int(Decimal(total_lessons) / step) + 2
    return start + datetime.timedelta(weeks=weeks)


def generate(
    *,
    start_date: datetime.date,
    slots: list[Slot],
    total_lessons: Optional[int],
    duration_minutes: int,
    default_teacher_id: Optional[int],
    start_seq: int = 1,
    start_number: Decimal = Decimal('0'),
) -> list[PlannedRow]:
    """Развернуть занятия курса от start_date по слотам. С start_seq=1 — полный
    план; с start_seq=k / start_number — регенерация хвоста (продолжение нумерации).
    Число занятий = (total_lessons - start_number) / step, привязка к слоту ≥ start_date.
    total_lessons обязателен и в единицах занятий; None/нет слотов → []."""
    if total_lessons is None or not slots:
        return []
    step = _step_for(duration_minutes)
    remaining = Decimal(total_lessons) - start_number
    if remaining <= 0:
        return []
    occ = _walk(start_date, slots, step, remaining, _far_future(start_date, int(remaining / step) + 2, step))
    return [
        PlannedRow(
            seq=start_seq - 1 + o.seq,
            lesson_number=start_number + o.lesson_number,
            scheduled_date=o.date,
            scheduled_time=o.time,
            teacher_id=default_teacher_id,
            status=PENDING,
        )
        for o in occ
    ]


def generate_from_facts(
    *,
    facts: list[Fact],
    current_slots: list[Slot],
    total_lessons: int,
    duration_minutes: int,
    default_teacher_id: Optional[int],
    group_start_date: Optional[datetime.date],
) -> list[PlannedRow]:
    """Пересборка плана из фактов (бэкфилл, Механизм 2).

    ПРОШЛОЕ = факты: i-й факт (сорт по (lesson_date, id)) → строка seq i, status=done,
    scheduled_date = fact.lesson_date (плановая дата = фактическая), номер по порядку
    (кумулятивный step), преподаватель и fact_lesson_id из факта. Время done-строк —
    время текущего слота (у факта времени нет; на статус done не влияет).

    БУДУЩЕЕ = оставшиеся уроки (total − проведено) разворачиваются по ТЕКУЩЕМУ слоту,
    начиная с ближайшего слот-дня СТРОГО ПОСЛЕ даты последнего проведённого урока
    (а не от «сегодня»): план продолжается непрерывно с того места, где группа
    остановилась. Пример: последний (27-й) урок в СБ 04.07 → 28-й в СБ 11.07.
    Если фактов нет — будущее от group_start_date. Номера/seq продолжают прошлое.

    Непроведённые будущие строки со временем в прошлом читаются как overdue
    (_planned_status на чтении) — отдельного «overdue-прошлого» не материализуем.

    Клэмп: фактов >= total (проведено больше длины курса) или нет открытого слота →
    только done-строки, будущего нет. Вход не мутируется; результат today-независим."""
    step = _step_for(duration_minutes)
    ordered = sorted(facts, key=lambda f: (f.lesson_date, f.fact_lesson_id))
    done_time = current_slots[0].start_time if current_slots else datetime.time(0, 0)

    done: list[PlannedRow] = []
    num = Decimal('0')
    for i, f in enumerate(ordered):
        num += step
        done.append(PlannedRow(
            seq=i + 1,
            lesson_number=num,
            scheduled_date=f.lesson_date,
            scheduled_time=done_time,
            teacher_id=f.teacher_id,
            status=DONE,
            fact_lesson_id=f.fact_lesson_id,
        ))

    remaining = total_lessons - num  # в единицах уроков (Decimal; half-lesson учтён)
    if remaining <= 0 or not current_slots:
        return done  # курс пройден / нет открытого слота → только прошлое

    if ordered:
        anchor = ordered[-1].lesson_date + datetime.timedelta(days=1)  # день ПОСЛЕ последнего факта
    elif group_start_date is not None:
        anchor = group_start_date
    else:
        return done  # некуда ставить будущее

    f_count = len(ordered)
    occ = _walk(anchor, current_slots, step, remaining, _far_future(anchor, remaining, step))
    future = [
        PlannedRow(
            seq=f_count + o.seq,
            lesson_number=num + o.lesson_number,
            scheduled_date=o.date,
            scheduled_time=o.time,
            teacher_id=default_teacher_id,
            status=PENDING,
        )
        for o in occ
    ]
    return done + future


def reschedule(
    row: PlannedRow,
    *,
    new_date: datetime.date,
    new_time: Optional[datetime.time] = None,
    new_teacher_id: Optional[int] = None,
) -> PlannedRow:
    """Разовый перенос одной строки: новые дата/время (+опц. преподаватель).
    seq/lesson_number сохраняются. Проведённое (DONE) переносить нельзя.

    moved_from_date фиксируется ТОЛЬКО при реальной смене даты (new_date отличается
    от текущей). Перенос на ту же дату (напр. правка только времени) не помечает
    строку перенесённой — иначе ↪-значок «Перенесён» появлялся бы без смены даты."""
    if row.status == DONE:
        raise ValueError('Нельзя перенести проведённое занятие (status=done).')
    moved_from = row.scheduled_date if new_date != row.scheduled_date else row.moved_from_date
    return replace(
        row,
        scheduled_date=new_date,
        scheduled_time=new_time if new_time is not None else row.scheduled_time,
        teacher_id=new_teacher_id if new_teacher_id is not None else row.teacher_id,
        moved_from_date=moved_from,
    )


def change_teacher(row: PlannedRow, *, new_teacher_id: int) -> PlannedRow:
    """Разовая замена преподавателя на дату этой строки: пишет substitute_teacher_id
    (НЕ teacher_id — тот остаётся преподавателем контента). Дата/время/moved_from не
    трогаются; при последующем переезде строки замена обнуляется (свойство даты).
    Проведённое (DONE) менять нельзя."""
    if row.status == DONE:
        raise ValueError('Нельзя сменить преподавателя проведённого занятия (status=done).')
    return replace(row, substitute_teacher_id=new_teacher_id)


def change_teacher_tail(
    rows: list[PlannedRow], *, from_seq: int, new_teacher_id: int,
) -> list[PlannedRow]:
    """Смена преподавателя навсегда: проставить teacher_id всем курсовым строкам
    seq>=from_seq со статусом pending/overdue. Даты/время/дни НЕ трогаются (в
    отличие от permanent_change). Проведённые и голова (seq<from_seq) не меняются."""
    out: list[PlannedRow] = []
    for r in rows:
        if r.seq is not None and r.seq >= from_seq and r.status in _MUTABLE:
            out.append(replace(r, teacher_id=new_teacher_id))
        else:
            out.append(replace(r))
    return out


def _shift_to_weekday(d: datetime.date, new_dow_sun0: int) -> datetime.date:
    """Дата того же недельного окна (Пн..Вс), но на новый день недели (Вс=0)."""
    monday = d - datetime.timedelta(days=d.weekday())
    return monday + datetime.timedelta(days=_offset_from_monday(new_dow_sun0))


def permanent_change(
    rows: list[PlannedRow],
    *,
    from_seq: int,
    new_day_of_week: int,
    new_time: Optional[datetime.time] = None,
    new_teacher_id: Optional[int] = None,
) -> list[PlannedRow]:
    """Перенос навсегда: пересчитать дату всех курсовых строк seq>=from_seq со
    статусом pending/overdue на новый день недели, сохраняя недельную каденцию;
    опц. проставить преподавателя. Проведённые и голова (seq<from_seq) не трогаются.
    Версионирование слота (закрыть старый/открыть новый) — эффект repository."""
    out: list[PlannedRow] = []
    for r in rows:
        if r.seq is not None and r.seq >= from_seq and r.status in _MUTABLE:
            out.append(replace(
                r,
                scheduled_date=_shift_to_weekday(r.scheduled_date, new_day_of_week),
                scheduled_time=new_time if new_time is not None else r.scheduled_time,
                teacher_id=new_teacher_id if new_teacher_id is not None else r.teacher_id,
            ))
        else:
            out.append(replace(r))
    return out


def relay_from_date(
    tail: list[PlannedRow],
    *,
    resume_date: datetime.date,
    slots: list[Slot],
    duration_minutes: int,
    skip_dates: frozenset[datetime.date] = frozenset(),
) -> list[PlannedRow]:
    """Переложить хвост курсовых строк (ordered by seq) на новые даты, разворачивая
    слот от resume_date включительно. i-я строка → i-е СВОБОДНОЕ слот-занятие.
    seq/lesson_number сохраняются; moved_from_date обнуляется (разовые переносы
    схлопываются); исходный status сохраняется (НЕ принудительно PENDING —
    вызывающий обязан передавать только pending/overdue строки, DONE тут не
    фильтруются, см. инвариант модуля).

    skip_dates — уже занятые даты (маркеры отмен, проведённые уроки, доп.занятия):
    _walk их пропускает, номер на них не тратится → раскладка остаётся непрерывной
    и не наезжает на существующие пины. Горизонт генерации расширяем на число
    скипов, чтобы всем строкам хватило свободных слотов.

    total для _walk считается ТОЧНО как len(ordered)*step (Decimal, без округления) —
    _walk останавливается строго при num > total, так что выдаёт РОВНО N occurrences
    независимо от полу-урочного шага (0.5). Пустой хвост / нет слотов → без сдвига."""
    if not tail or not slots:
        return [replace(r) for r in tail]
    ordered = sorted(tail, key=lambda r: (r.seq if r.seq is not None else 0))
    step = _step_for(duration_minutes)
    total = Decimal(len(ordered)) * step
    horizon_weeks = len(ordered) + len(skip_dates) + 2
    generate_until = resume_date + datetime.timedelta(weeks=horizon_weeks)
    occ = _walk(resume_date, slots, step, total, generate_until, skip_dates=skip_dates)
    out: list[PlannedRow] = []
    for r, o in zip(ordered, occ):
        out.append(replace(
            r,
            scheduled_date=o.date,
            scheduled_time=o.time,
            moved_from_date=None,
        ))
    return out


def renumber_by_date(
    rows: list[PlannedRow],
    *,
    start_seq: int,
    start_number: Decimal,
    step: Decimal,
) -> list[PlannedRow]:
    """Присвоить курсовым строкам непрерывные seq/lesson_number по возрастанию
    (scheduled_date, scheduled_time), начиная с start_seq/start_number+step.
    seq/номер — по порядку дат; сами даты не трогаются. Вход не мутируется."""
    ordered = sorted(rows, key=lambda r: (r.scheduled_date, r.scheduled_time))
    # Determine quantize precision based on step's decimal places (minimum 1).
    step_exp = step.as_tuple().exponent
    decimal_places = max(-step_exp, 1)
    quantize_val = Decimal(10) ** -decimal_places

    out: list[PlannedRow] = []
    seq = start_seq
    num = start_number
    for r in ordered:
        num += step
        out.append(replace(r, seq=seq, lesson_number=num.quantize(quantize_val)))
        seq += 1
    return out
