"""
Юнит-тесты чистых функций планировщика planner.py (без БД).

Операции над материализованными плановыми строками (PlannedRow): генерация плана,
разовый перенос, перенос навсегда, отмена со сдвигом, доп. занятие.

Опорные даты: 2026-06-01 — понедельник. day_of_week в конвенции Вс=0
(Пн=1, Вт=2, Ср=3, Чт=4, Пт=5, Сб=6, Вс=0).
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from apps.scheduling.occurrences import DONE, PENDING, Slot
from apps.scheduling.planner import (
    Fact, PlannedRow, change_teacher, change_teacher_tail, generate,
    generate_from_facts, permanent_change, reschedule,
)

D = datetime.date
T = datetime.time
MON, TUE, WED, THU, FRI, SAT, SUN = 1, 2, 3, 4, 5, 6, 0


def _slot(dow, hh, eff_from=D(2000, 1, 1), eff_to=None):
    return Slot(day_of_week=dow, start_time=T(hh, 0), effective_from=eff_from, effective_to=eff_to)


# --------------------------------------------------------------------------- #
# generate
# --------------------------------------------------------------------------- #

def test_generate_weekly_produces_n_rows():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
        total_lessons=4, duration_minutes=90, default_teacher_id=7,
    )
    assert [r.seq for r in rows] == [1, 2, 3, 4]
    assert [r.scheduled_date for r in rows] == [D(2026, 6, 1), D(2026, 6, 8), D(2026, 6, 15), D(2026, 6, 22)]
    assert [r.lesson_number for r in rows] == [Decimal('1'), Decimal('2'), Decimal('3'), Decimal('4')]
    assert all(r.scheduled_time == T(10, 0) for r in rows)
    assert all(r.teacher_id == 7 for r in rows)
    assert all(r.status == PENDING for r in rows)


def test_generate_half_lesson_step():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
        total_lessons=2, duration_minutes=45, default_teacher_id=1,
    )
    # 2 урока курса × шаг 0.5 → 4 сессии, номера 0.5..2.0
    assert len(rows) == 4
    assert [r.lesson_number for r in rows] == [Decimal('0.5'), Decimal('1.0'), Decimal('1.5'), Decimal('2.0')]


def test_generate_half_lesson_long_course_not_truncated():
    """Регресс: _far_future считал 1 урок/неделю, обрезая полуурочные курсы вдвое.
    45-мин курс (step 0.5) с 1 слотом/неделю и total=6 → 12 сессий (0.5..6.0) на
    12 недель. До фикса генерировалось ~10 (num обрывался ~5.0)."""
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
        total_lessons=6, duration_minutes=45, default_teacher_id=1,
    )
    assert len(rows) == 12
    assert rows[-1].lesson_number == Decimal('6.0')
    # Недельная каденция сохранена (каждая строка на 7 дней позже предыдущей).
    dates = [r.scheduled_date for r in rows]
    assert all((dates[i] - dates[i - 1]).days == 7 for i in range(1, len(dates)))


def test_generate_sunday_slot_maps_to_correct_date():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(SUN, 12)],
        total_lessons=2, duration_minutes=90, default_teacher_id=1,
    )
    # Первое воскресенье на/после старта = 2026-06-07, затем 06-14.
    assert [r.scheduled_date for r in rows] == [D(2026, 6, 7), D(2026, 6, 14)]
    assert all(r.scheduled_time == T(12, 0) for r in rows)


def test_generate_multiple_slots_ordered_by_date():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10), _slot(WED, 14)],
        total_lessons=4, duration_minutes=90, default_teacher_id=1,
    )
    assert [r.scheduled_date for r in rows] == [D(2026, 6, 1), D(2026, 6, 3), D(2026, 6, 8), D(2026, 6, 10)]
    assert [r.seq for r in rows] == [1, 2, 3, 4]


def test_generate_no_total_returns_empty():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
        total_lessons=None, duration_minutes=90, default_teacher_id=1,
    )
    assert rows == []


def test_generate_no_slots_returns_empty():
    rows = generate(
        start_date=D(2026, 6, 1), slots=[],
        total_lessons=4, duration_minutes=90, default_teacher_id=1,
    )
    assert rows == []


def test_generate_is_idempotent_by_value():
    kw = dict(start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
              total_lessons=4, duration_minutes=90, default_teacher_id=7)
    assert generate(**kw) == generate(**kw)


def test_generate_versioned_slots_continuous_numbering():
    """Версионные слоты (закрытый + открытый) → _walk выбирает активный на дату,
    номера сквозные. Покрывает переход effective_from/effective_to в генераторе."""
    slots = [
        _slot(MON, 10, eff_from=D(2026, 6, 1), eff_to=D(2026, 6, 14)),   # первые 2 понедельника
        _slot(WED, 14, eff_from=D(2026, 6, 15)),                          # далее среды
    ]
    rows = generate(
        start_date=D(2026, 6, 1), slots=slots,
        total_lessons=4, duration_minutes=90, default_teacher_id=1,
    )
    assert [r.scheduled_date for r in rows] == [D(2026, 6, 1), D(2026, 6, 8), D(2026, 6, 17), D(2026, 6, 24)]
    assert [r.lesson_number for r in rows] == [Decimal('1'), Decimal('2'), Decimal('3'), Decimal('4')]
    assert rows[2].scheduled_time == T(14, 0)


def test_generate_start_seq_offsets_numbering():
    # Хвост курса: 2 занятия по вторникам, нумерация с seq=5 (номер с 4.0).
    rows = generate(
        start_date=D(2026, 7, 21),  # вторник
        slots=[_slot(TUE, 18, eff_from=D(2026, 7, 21))],
        total_lessons=6,                          # 6 курсовых всего
        duration_minutes=60,
        default_teacher_id=9,
        start_seq=5,
        start_number=Decimal('4'),
    )
    assert [r.seq for r in rows] == [5, 6]
    assert [r.lesson_number for r in rows] == [Decimal('5'), Decimal('6')]
    assert rows[0].scheduled_date == D(2026, 7, 21)
    assert rows[0].status == PENDING
    assert rows[0].teacher_id == 9


def test_generate_default_start_seq_is_one():
    rows = generate(
        start_date=D(2026, 7, 21), slots=[_slot(TUE, 18)],
        total_lessons=2, duration_minutes=60, default_teacher_id=1,
    )
    assert [r.seq for r in rows] == [1, 2]
    assert [r.lesson_number for r in rows] == [Decimal('1'), Decimal('2')]


# --------------------------------------------------------------------------- #
# reschedule
# --------------------------------------------------------------------------- #

def _row(seq=2, ln='2', date=D(2026, 6, 8), time=T(10, 0), teacher=7, status=PENDING):
    return PlannedRow(
        seq=seq, lesson_number=Decimal(ln), scheduled_date=date,
        scheduled_time=time, teacher_id=teacher, status=status,
    )


def test_reschedule_moves_single_row_and_sets_moved_from():
    out = reschedule(_row(), new_date=D(2026, 6, 10), new_time=T(12, 0))
    assert out.scheduled_date == D(2026, 6, 10)
    assert out.scheduled_time == T(12, 0)
    assert out.moved_from_date == D(2026, 6, 8)
    assert out.seq == 2
    assert out.lesson_number == Decimal('2')


def test_reschedule_keeps_time_when_new_time_none():
    out = reschedule(_row(time=T(10, 0)), new_date=D(2026, 6, 10), new_time=None)
    assert out.scheduled_time == T(10, 0)


def test_reschedule_optional_teacher_override():
    out = reschedule(_row(teacher=7), new_date=D(2026, 6, 10), new_teacher_id=9)
    assert out.teacher_id == 9
    keep = reschedule(_row(teacher=7), new_date=D(2026, 6, 10))
    assert keep.teacher_id == 7


def test_reschedule_done_raises():
    with pytest.raises(ValueError):
        reschedule(_row(status=DONE), new_date=D(2026, 6, 10))


def test_reschedule_same_date_does_not_set_moved_from():
    """Перенос на ту же дату (напр. меняем только время/препода через reschedule)
    НЕ должен помечать строку перенесённой — moved_from_date остаётся None."""
    out = reschedule(_row(date=D(2026, 6, 8)), new_date=D(2026, 6, 8), new_time=T(12, 0))
    assert out.moved_from_date is None
    assert out.scheduled_time == T(12, 0)


def test_reschedule_real_date_change_sets_moved_from():
    out = reschedule(_row(date=D(2026, 6, 8)), new_date=D(2026, 6, 15))
    assert out.moved_from_date == D(2026, 6, 8)


# --------------------------------------------------------------------------- #
# change_teacher (разовая замена преподавателя на дату — substitute_teacher_id)
# --------------------------------------------------------------------------- #

def test_change_teacher_sets_substitute_not_content():
    out = change_teacher(_row(teacher=7, date=D(2026, 6, 8), time=T(10, 0)), new_teacher_id=9)
    assert out.substitute_teacher_id == 9        # замена — в отдельном поле
    assert out.teacher_id == 7                   # преподаватель контента не тронут
    assert out.scheduled_date == D(2026, 6, 8)   # дата не тронута
    assert out.scheduled_time == T(10, 0)        # время не тронуто
    assert out.moved_from_date is None           # НЕ помечается перенесённой
    assert out.seq == 2
    assert out.lesson_number == Decimal('2')


def test_change_teacher_done_raises():
    with pytest.raises(ValueError):
        change_teacher(_row(status=DONE), new_teacher_id=9)


# --------------------------------------------------------------------------- #
# change_teacher_tail (смена преподавателя навсегда — teacher_id хвоста)
# --------------------------------------------------------------------------- #

def test_change_teacher_tail_sets_teacher_from_seq_only():
    out = change_teacher_tail(_plan_mon(), from_seq=3, new_teacher_id=9)
    by_seq = {r.seq: r for r in out}
    assert by_seq[1].teacher_id == 7
    assert by_seq[2].teacher_id == 7
    assert by_seq[3].teacher_id == 9
    assert by_seq[4].teacher_id == 9
    # Даты/время не трогаются — только преподаватель.
    assert [by_seq[s].scheduled_date for s in (1, 2, 3, 4)] == [
        D(2026, 6, 1), D(2026, 6, 8), D(2026, 6, 15), D(2026, 6, 22),
    ]


def test_change_teacher_tail_skips_done():
    rows = _plan_mon()
    rows[2].status = DONE  # seq=3 проведён
    out = change_teacher_tail(rows, from_seq=2, new_teacher_id=9)
    by_seq = {r.seq: r for r in out}
    assert by_seq[2].teacher_id == 9
    assert by_seq[3].teacher_id == 7   # done не тронут
    assert by_seq[4].teacher_id == 9


# --------------------------------------------------------------------------- #
# permanent_change
# --------------------------------------------------------------------------- #

def _plan_mon():
    return generate(
        start_date=D(2026, 6, 1), slots=[_slot(MON, 10)],
        total_lessons=4, duration_minutes=90, default_teacher_id=7,
    )


def test_permanent_change_recomputes_tail_to_new_weekday():
    rows = permanent_change(_plan_mon(), from_seq=2, new_day_of_week=WED, new_time=T(14, 0))
    by_seq = {r.seq: r for r in rows}
    assert by_seq[1].scheduled_date == D(2026, 6, 1)   # голова не тронута
    assert by_seq[1].scheduled_time == T(10, 0)
    # Каждая строка сдвигается на среду СВОЕЙ недели, недельная каденция сохранена.
    assert by_seq[2].scheduled_date == D(2026, 6, 10)  # среда недели 06-08
    assert by_seq[3].scheduled_date == D(2026, 6, 17)  # среда недели 06-15
    assert by_seq[4].scheduled_date == D(2026, 6, 24)  # среда недели 06-22
    assert all(by_seq[s].scheduled_time == T(14, 0) for s in (2, 3, 4))


def test_permanent_change_does_not_touch_done():
    rows = _plan_mon()
    rows[1].status = DONE  # seq=2 проведён
    out = permanent_change(rows, from_seq=2, new_day_of_week=WED)
    by_seq = {r.seq: r for r in out}
    assert by_seq[2].scheduled_date == D(2026, 6, 8)   # done остался на понедельнике
    assert by_seq[3].scheduled_date == D(2026, 6, 17)  # pending переехал на среду своей недели
    assert by_seq[4].scheduled_date == D(2026, 6, 24)


def test_permanent_change_sets_teacher_on_tail_only():
    out = permanent_change(_plan_mon(), from_seq=3, new_day_of_week=MON, new_teacher_id=9)
    by_seq = {r.seq: r for r in out}
    assert by_seq[1].teacher_id == 7
    assert by_seq[2].teacher_id == 7
    assert by_seq[3].teacher_id == 9
    assert by_seq[4].teacher_id == 9


# --------------------------------------------------------------------------- #
# generate_from_facts (бэкфилл: прошлое=факты, будущее от последнего факта по слоту)
# --------------------------------------------------------------------------- #

def _gff(*, facts, current_slots, total, duration=90, teacher=7, start=D(2026, 6, 1)):
    return generate_from_facts(
        facts=facts, current_slots=current_slots, total_lessons=total,
        duration_minutes=duration, default_teacher_id=teacher, group_start_date=start,
    )


def test_gff_past_is_facts_collapsed_and_done():
    facts = [
        Fact(lesson_date=D(2026, 6, 2), teacher_id=11, fact_lesson_id=101),   # сдвинут с плана
        Fact(lesson_date=D(2026, 6, 9), teacher_id=12, fact_lesson_id=102),
    ]
    out = _gff(facts=facts, current_slots=[_slot(MON, 10)], total=4)
    by_seq = {r.seq: r for r in out}
    assert by_seq[1].status == DONE
    assert by_seq[1].scheduled_date == D(2026, 6, 2)     # дата = фактическая
    assert by_seq[1].teacher_id == 11                    # препод из факта
    assert by_seq[1].fact_lesson_id == 101
    assert by_seq[1].lesson_number == Decimal('1')
    assert by_seq[2].scheduled_date == D(2026, 6, 9)
    assert by_seq[2].fact_lesson_id == 102


def test_gff_future_starts_after_last_fact_by_slot():
    """Ключевой сценарий: последний урок в СБ 04.07 → следующий в СБ 11.07 (не от today)."""
    facts = [
        Fact(lesson_date=D(2026, 6, 27), teacher_id=7, fact_lesson_id=201),  # СБ
        Fact(lesson_date=D(2026, 7, 4), teacher_id=7, fact_lesson_id=202),   # СБ (последний)
    ]
    out = _gff(facts=facts, current_slots=[_slot(SAT, 18)], total=4)
    by_seq = {r.seq: r for r in out}
    assert by_seq[3].status == PENDING
    assert by_seq[3].scheduled_date == D(2026, 7, 11)    # ближайшая СБ ПОСЛЕ 04.07
    assert by_seq[3].scheduled_time == T(18, 0)
    assert by_seq[3].lesson_number == Decimal('3')       # номер продолжает прошлое
    assert by_seq[4].scheduled_date == D(2026, 7, 18)


def test_gff_future_independent_of_today():
    """Результат today-независим: тот же вход → тот же план (даты от фактов)."""
    facts = [Fact(lesson_date=D(2026, 7, 4), teacher_id=7, fact_lesson_id=1)]
    a = _gff(facts=facts, current_slots=[_slot(SAT, 18)], total=3)
    b = _gff(facts=facts, current_slots=[_slot(SAT, 18)], total=3)
    assert [r.scheduled_date for r in a] == [r.scheduled_date for r in b]
    fut = [r.scheduled_date for r in a if r.status == PENDING]
    assert fut == [D(2026, 7, 11), D(2026, 7, 18)]


def test_gff_zero_facts_future_from_start():
    out = _gff(facts=[], current_slots=[_slot(MON, 10)], total=3, start=D(2026, 6, 1))
    assert all(r.status == PENDING for r in out)
    assert [r.scheduled_date for r in out] == [D(2026, 6, 1), D(2026, 6, 8), D(2026, 6, 15)]
    assert [r.seq for r in out] == [1, 2, 3]


def test_gff_facts_cover_course_no_future():
    facts = [
        Fact(lesson_date=D(2026, 6, 1), teacher_id=7, fact_lesson_id=1),
        Fact(lesson_date=D(2026, 6, 8), teacher_id=7, fact_lesson_id=2),
        Fact(lesson_date=D(2026, 6, 15), teacher_id=7, fact_lesson_id=3),  # фактов >= total
    ]
    out = _gff(facts=facts, current_slots=[_slot(MON, 10)], total=2)
    assert all(r.status == DONE for r in out)
    assert len(out) == 3   # все факты сохранены, будущего нет


def test_gff_no_open_slots_only_past():
    facts = [Fact(lesson_date=D(2026, 6, 1), teacher_id=7, fact_lesson_id=1)]
    out = _gff(facts=facts, current_slots=[], total=4)
    assert len(out) == 1 and out[0].status == DONE


def test_gff_half_lesson_numbering():
    # step 0.5: 1 факт = 0.5 юнита done; remaining = 2 − 0.5 = 1.5 юнита = 3 сессии.
    facts = [Fact(lesson_date=D(2026, 7, 4), teacher_id=7, fact_lesson_id=1)]
    out = _gff(facts=facts, current_slots=[_slot(SAT, 18)], total=2, duration=45)
    done = [r for r in out if r.status == DONE]
    fut = [r for r in out if r.status == PENDING]
    assert done[0].lesson_number == Decimal('0.5')
    assert fut[0].lesson_number == Decimal('1.0')
    assert fut[0].scheduled_date == D(2026, 7, 11)   # СБ после последнего факта
    assert out[-1].lesson_number == Decimal('2.0')
    assert len(fut) == 3


def test_gff_facts_sorted_by_date():
    facts = [
        Fact(lesson_date=D(2026, 7, 4), teacher_id=7, fact_lesson_id=2),   # позже
        Fact(lesson_date=D(2026, 6, 27), teacher_id=7, fact_lesson_id=1),  # раньше
    ]
    out = _gff(facts=facts, current_slots=[_slot(SAT, 18)], total=3)
    by_seq = {r.seq: r for r in out}
    assert by_seq[1].fact_lesson_id == 1   # ранний факт → seq 1
    assert by_seq[2].fact_lesson_id == 2
    assert by_seq[3].scheduled_date == D(2026, 7, 11)   # будущее от 04.07 (последний факт)


# --------------------------------------------------------------------------- #
# renumber_by_date (перенумерация по дате)
# --------------------------------------------------------------------------- #

def test_renumber_by_date_contiguous_from_start():
    # Три pending-строки не по порядку дат → перенумеровать по дате с seq=3.
    from apps.scheduling.planner import renumber_by_date
    rows = [
        PlannedRow(seq=5, lesson_number=Decimal('5'), scheduled_date=D(2026, 8, 4),
                   scheduled_time=T(18, 0)),
        PlannedRow(seq=3, lesson_number=Decimal('3'), scheduled_date=D(2026, 7, 21),
                   scheduled_time=T(18, 0)),
        PlannedRow(seq=4, lesson_number=Decimal('4'), scheduled_date=D(2026, 7, 28),
                   scheduled_time=T(18, 0)),
    ]
    out = renumber_by_date(rows, start_seq=3, start_number=Decimal('2'), step=Decimal('1'))
    ordered = sorted(out, key=lambda r: r.scheduled_date)
    assert [r.seq for r in ordered] == [3, 4, 5]
    assert [str(r.lesson_number) for r in ordered] == ['3.0', '4.0', '5.0']
    # Дата 21.07 → seq 3 (наименьшая), 04.08 → seq 5 (наибольшая).
    assert ordered[0].scheduled_date == D(2026, 7, 21)
    assert ordered[-1].scheduled_date == D(2026, 8, 4)


def test_renumber_by_date_half_lesson_step():
    from apps.scheduling.planner import renumber_by_date
    rows = [
        PlannedRow(seq=2, lesson_number=Decimal('1.0'), scheduled_date=D(2026, 7, 28),
                   scheduled_time=T(18, 0)),
        PlannedRow(seq=1, lesson_number=Decimal('0.5'), scheduled_date=D(2026, 7, 21),
                   scheduled_time=T(18, 0)),
    ]
    out = renumber_by_date(rows, start_seq=1, start_number=Decimal('0'), step=Decimal('0.5'))
    ordered = sorted(out, key=lambda r: r.scheduled_date)
    assert [str(r.lesson_number) for r in ordered] == ['0.5', '1.0']
