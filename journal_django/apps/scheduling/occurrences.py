"""
Чистый генератор плановых занятий (occurrences) — БЕЗ доступа к БД, полностью
тестируемый в изоляции.

Модель (см. docs/lesson-scheduling.md):
  RECURRENCE (start_date + версионируемые слоты + длина курса)
    → OCCURRENCES (вычисляемые плановые занятия, выход `_walk`)

Потребитель генератора — `planner.generate` (материализация плана в
planned_lessons). Прежний compute-on-read слой (наложение разовых исключений,
статусы) удалён — план материализуется в БД, статусы читаются в services.

Конвенция day_of_week — **Вс=0** (JS getDay), проверено на данных БД.
Даты — datetime.date; время — datetime.time.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from apps.core.utils.dates import MSK

# Статусы плановых занятий (хранятся в planned_lessons; читаются в services).
PENDING = 'pending'      # факта нет, время ещё не наступило
OVERDUE = 'overdue'      # факта нет, время прошло (надо заполнить)
DONE = 'done'            # есть факт (урок записан) на эту дату
CANCELLED = 'cancelled'  # отменён операцией
MOVED = 'moved'          # исходное занятие перенесено (показываем «перенесён на …»)

# Предохранитель от бесконечного цикла на битых данных (10 лет недель).
_CAP_WEEKS = 520


@dataclass
class Slot:
    """Версионированный слот расписания (день недели Вс=0 + время + период действия)."""
    day_of_week: int
    start_time: datetime.time
    effective_from: datetime.date
    effective_to: Optional[datetime.date] = None

    def active_on(self, d: datetime.date) -> bool:
        return self.effective_from <= d and (self.effective_to is None or d <= self.effective_to)


@dataclass
class Occurrence:
    """Проекция правила на календарь — единственный выход `_walk`.

    Несёт только то, что генератор реально вычисляет; статусы/переносы —
    материализуются и читаются на уровне planned_lessons (services)."""
    date: datetime.date
    time: Optional[datetime.time]
    seq: int                                  # порядковый номер в курсе
    lesson_number: Optional[Decimal]          # seq*step


def planned_status(row: dict, now_msk: datetime.datetime) -> str:
    """
    Статус планового занятия НА ЧТЕНИИ из строки `planned_lessons`.

      done      — status=='done' или есть fact_lesson (связь план→факт);
      cancelled — как хранится (маркер отмены);
      иначе     — overdue, если момент занятия по МСК уже наступил, иначе pending.

    В БД хранится только «записан или нет»: прошлые незаполненные строки лежат
    как pending, а overdue считается здесь, по времени занятия. Перенос
    показывается через moved_from_date — отдельного статуса 'moved' нет.

    Живёт в этом модуле, а не в services, потому что нужна ОБОИМ читателям плана:
    календарю (services.build_calendar) и плану группы (repository.get_plan).
    Репозиторий импортировать services не может — services уже импортирует
    repository. Разъезд этих двух читателей и давал «Запланирован» в «Обзоре» на
    занятии, которое давно пора было заполнить.
    """
    if row['status'] == DONE or row['fact_lesson_id'] is not None:
        return DONE
    if row['status'] == CANCELLED:
        return CANCELLED
    occ_dt = datetime.datetime.combine(
        row['scheduled_date'], row['scheduled_time'] or datetime.time(0, 0), tzinfo=MSK,
    )
    return OVERDUE if now_msk >= occ_dt else PENDING


def _offset_from_monday(dow_sun0: int) -> int:
    """Смещение (в днях) от понедельника недели до дня недели в конвенции Вс=0.
    Пн(1)→0, Вт(2)→1, … Сб(6)→5, Вс(0)→6."""
    return 6 if dow_sun0 == 0 else dow_sun0 - 1


def _step_for(duration_minutes: int) -> Decimal:
    """Half-lesson: 45 мин → 0.5 занятия, иначе 1 (структурно, не из имени группы)."""
    return Decimal('0.5') if duration_minutes == 45 else Decimal('1')


def _walk(
    start: datetime.date,
    slots: list[Slot],
    step: Decimal,
    total: Optional[int],
    generate_until: datetime.date,
    skip_dates: frozenset[datetime.date] = frozenset(),
) -> list[Occurrence]:
    """
    Перебор курса по неделям от даты старта. На каждой неделе берём слоты,
    активные на конкретную дату, упорядоченные по (дата, время), инкрементим
    seq/lesson_number. Останавливаемся, когда номер превысил длину курса
    (total) ИЛИ прошли generate_until (для открытых курсов total=None).

    Даты из skip_dates ПРОПУСКАЮТСЯ как позиции размещения (номер урока на них
    НЕ расходуется) — так пересчёт хвоста обходит уже занятые даты (маркеры
    отмен, проведённые уроки, доп.занятия), оставаясь непрерывным.
    """
    occ: list[Occurrence] = []
    num = Decimal('0')
    seq = 0
    monday = start - datetime.timedelta(days=start.weekday())  # Пн недели старта
    weeks = 0
    while weeks < _CAP_WEEKS:
        week_cands: list[tuple[datetime.date, datetime.time]] = []
        for s in slots:
            d = monday + datetime.timedelta(days=_offset_from_monday(s.day_of_week))
            if d < start:
                continue
            if s.active_on(d):
                week_cands.append((d, s.start_time))
        week_cands.sort()
        for d, t in week_cands:
            if d in skip_dates:
                continue  # занятая дата — не размещаем и не тратим номер
            num += step
            if total is not None and num > total:
                return occ  # курс завершён
            seq += 1
            occ.append(Occurrence(date=d, time=t, seq=seq, lesson_number=num))
        if monday > generate_until:
            break
        monday += datetime.timedelta(days=7)
        weeks += 1
    return occ
