"""Чистая перекладка хвоста курсовых строк на новые даты от resume_date по слоту.
seq/lesson_number сохраняются; moved_from_date обнуляется; порядок по seq."""
import datetime
from decimal import Decimal

from apps.scheduling.occurrences import PENDING, Slot
from apps.scheduling.planner import PlannedRow, relay_from_date


def _row(seq, d):
    return PlannedRow(seq=seq, lesson_number=Decimal(seq),
                      scheduled_date=d, scheduled_time=datetime.time(10, 0),
                      status=PENDING, moved_from_date=datetime.date(2000, 1, 1))


def test_relay_lays_tail_weekly_from_resume():
    # Слот: среда (Вс=0 → ср=3), 10:00. resume = среда 2026-08-05.
    slots = [Slot(day_of_week=3, start_time=datetime.time(10, 0),
                  effective_from=datetime.date(2000, 1, 1))]
    tail = [_row(5, datetime.date(2026, 7, 1)),
            _row(6, datetime.date(2026, 7, 8)),
            _row(7, datetime.date(2026, 7, 15))]
    out = relay_from_date(tail, resume_date=datetime.date(2026, 8, 5),
                          slots=slots, duration_minutes=90)
    assert [r.scheduled_date for r in out] == [
        datetime.date(2026, 8, 5), datetime.date(2026, 8, 12), datetime.date(2026, 8, 19)]
    assert [r.seq for r in out] == [5, 6, 7]
    assert all(r.moved_from_date is None for r in out)
    assert [r.lesson_number for r in out] == [Decimal(5), Decimal(6), Decimal(7)]


def test_relay_half_lesson_cadence():
    # 45-минутный курс: step=0.5. Слот пятница (Вс=0 → пт=5), 15:00.
    # Хвост из 3 строк (нечётное N) — проверяем ровно 3 занятия, не 2 и не 4.
    slots = [Slot(day_of_week=5, start_time=datetime.time(15, 0),
                  effective_from=datetime.date(2000, 1, 1))]
    tail = [_row(10, datetime.date(2026, 6, 5)),
            _row(11, datetime.date(2026, 6, 12)),
            _row(12, datetime.date(2026, 6, 19))]
    out = relay_from_date(tail, resume_date=datetime.date(2026, 7, 3),
                          slots=slots, duration_minutes=45)
    assert len(out) == 3
    assert [r.scheduled_date for r in out] == [
        datetime.date(2026, 7, 3), datetime.date(2026, 7, 10), datetime.date(2026, 7, 17)]
    assert [r.seq for r in out] == [10, 11, 12]


def test_relay_empty_tail_returns_empty():
    slots = [Slot(day_of_week=3, start_time=datetime.time(10, 0),
                  effective_from=datetime.date(2000, 1, 1))]
    assert relay_from_date([], resume_date=datetime.date(2026, 8, 5),
                           slots=slots, duration_minutes=90) == []


def test_relay_skips_occupied_dates_contiguously():
    """Пересчёт хвоста обходит занятые даты (skip_dates) без дыр: две подряд
    занятые даты → хвост встаёт на следующие свободные слот-даты."""
    slots = [Slot(day_of_week=1, start_time=datetime.time(10, 0),   # понедельник
                  effective_from=datetime.date(2000, 1, 1))]
    tail = [_row(3, datetime.date(2026, 6, 15)),
            _row(5, datetime.date(2026, 6, 29))]
    # resume с 06-15; заняты 06-15 (маркер отмены) и 06-22 (done-урок).
    out = relay_from_date(
        tail, resume_date=datetime.date(2026, 6, 15), slots=slots,
        duration_minutes=90,
        skip_dates=frozenset({datetime.date(2026, 6, 15), datetime.date(2026, 6, 22)}),
    )
    assert [r.scheduled_date for r in out] == [
        datetime.date(2026, 6, 29), datetime.date(2026, 7, 6)]
    assert [r.seq for r in out] == [3, 5]


def test_relay_without_skip_dates_unchanged():
    slots = [Slot(day_of_week=3, start_time=datetime.time(10, 0),
                  effective_from=datetime.date(2000, 1, 1))]
    tail = [_row(5, datetime.date(2026, 7, 1)), _row(6, datetime.date(2026, 7, 8))]
    out = relay_from_date(tail, resume_date=datetime.date(2026, 8, 5),
                          slots=slots, duration_minutes=90)
    assert [r.scheduled_date for r in out] == [
        datetime.date(2026, 8, 5), datetime.date(2026, 8, 12)]
