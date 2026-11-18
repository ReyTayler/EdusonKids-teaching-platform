"""
Доменный сервис графика работы преподавателя.

Итоговые окна = недельный шаблон, поверх которого применены исключения на даты
(исключение целиком заменяет день). Сохранения меняют только реально
изменившиеся дни — журнал изменений (pghistory) не засоряется. Параллельные
сохранения одного преподавателя сериализуются SELECT … FOR UPDATE его строки;
перезапись дня — удалить ВСЕ строки дня + вставить новые в одной транзакции
(поэтому «закрыт» и интервалы на одну дату не смешиваются).

След мутаций — «Журнал изменений» (pghistory), НЕ log_event: журнал ИБ — для
событий безопасности (конвенция apps.scheduling).
"""
from __future__ import annotations

import datetime
from collections import defaultdict

from django.db import transaction
from django.utils import timezone

from apps.availability.models import GRID_END, GRID_START, AvailabilityOverride, AvailabilityRule
from apps.teachers.models import Teacher

Interval = tuple[datetime.time, datetime.time]


class ScheduleError(Exception):
    """Нарушение правила графика — вьюха отдаёт 400 с текстом."""


def _today() -> datetime.date:
    # Локальная дата по TIME_ZONE (Europe/Moscow). Отдельная функция — подмена в тестах.
    return timezone.localdate()


def js_weekday(d: datetime.date) -> int:
    """Python Пн=0 → конвенция проекта Вс=0 (JS getDay)."""
    return (d.weekday() + 1) % 7


def normalize(intervals: list[Interval]) -> list[Interval]:
    """Сортировка + склейка пересекающихся и соприкасающихся интервалов."""
    merged: list[list[datetime.time]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def fmt(t: datetime.time) -> str:
    return t.strftime('%H:%M')


def _on_half_hour(t: datetime.time) -> bool:
    return t.minute in (0, 30) and t.second == 0 and t.microsecond == 0


def validate_intervals(intervals: list[Interval]) -> None:
    """
    Правила сетки в доменном слое: команды и бэкфиллы обходят сериализатор,
    а CHECK-и БД дали бы IntegrityError (500) вместо понятного отказа.
    """
    for start, end in intervals:
        label = f'Окно {fmt(start)}–{fmt(end)}'
        if not (_on_half_hour(start) and _on_half_hour(end)):
            raise ScheduleError(f'{label}: границы должны быть кратны 30 минутам.')
        if start >= end:
            raise ScheduleError(f'{label}: начало должно быть раньше конца.')
        if start < GRID_START or end > GRID_END:
            raise ScheduleError(f'{label}: выходит за пределы {fmt(GRID_START)}–{fmt(GRID_END)}.')


def has_schedule(teacher_id: int) -> bool:
    return (AvailabilityRule.objects.filter(teacher_id=teacher_id).exists()
            or AvailabilityOverride.objects.filter(teacher_id=teacher_id).exists())


def get_template(teacher_id: int) -> dict[int, list[Interval]]:
    """Шаблон {day_of_week: [интервалы]}; дни без окон в словарь не попадают."""
    out: dict[int, list[Interval]] = defaultdict(list)
    rows = (AvailabilityRule.objects.filter(teacher_id=teacher_id)
            .order_by('day_of_week', 'start_time')
            .values_list('day_of_week', 'start_time', 'end_time'))
    for dow, s, e in rows:
        out[dow].append((s, e))
    return dict(out)


def _overrides_by_date(teacher_id: int, d_from: datetime.date,
                       d_to: datetime.date | None) -> dict[datetime.date, list[Interval]]:
    """{дата: интервалы} для дат с исключением; [] = день закрыт."""
    qs = AvailabilityOverride.objects.filter(teacher_id=teacher_id, date__gte=d_from)
    if d_to is not None:
        qs = qs.filter(date__lte=d_to)
    out: dict[datetime.date, list[Interval]] = {}
    closed: set[datetime.date] = set()
    for d, s, e in qs.order_by('date', 'start_time').values_list('date', 'start_time', 'end_time'):
        day = out.setdefault(d, [])
        if s is None:
            closed.add(d)
        else:
            day.append((s, e))
    # Смесь «закрыт» + интервалы сервис не создаёт, но БД её не запрещает
    # (ручная правка, старый бэкфилл). Закрытие сильнее: не показываем окна,
    # которые кто-то явно закрыл.
    for d in closed:
        out[d] = []
    return out


def get_effective_windows(teacher_id: int, d_from: datetime.date,
                          d_to: datetime.date) -> dict[datetime.date, list[Interval]]:
    """Итоговые окна на каждую дату [d_from, d_to]. Два запроса, без N+1."""
    template = get_template(teacher_id)
    overrides = _overrides_by_date(teacher_id, d_from, d_to)
    result: dict[datetime.date, list[Interval]] = {}
    d = d_from
    while d <= d_to:
        result[d] = list(overrides[d] if d in overrides else template.get(js_weekday(d), []))
        d += datetime.timedelta(days=1)
    return result


def override_dates(teacher_id: int, d_from: datetime.date, d_to: datetime.date) -> list[datetime.date]:
    return sorted(_overrides_by_date(teacher_id, d_from, d_to).keys())


def upcoming_overrides(teacher_id: int) -> list[dict]:
    """Исключения с сегодняшнего дня — для сводки в редакторе."""
    by_date = _overrides_by_date(teacher_id, _today(), None)
    return [{'date': d, 'intervals': ivs} for d, ivs in sorted(by_date.items())]


def windows_payload(teacher_id: int, d_from: datetime.date, d_to: datetime.date) -> dict | None:
    """JSON-форма итоговых окон для календаря; None — графика нет (фон не рисуем)."""
    if not has_schedule(teacher_id):
        return None
    return {
        d.isoformat(): [[fmt(s), fmt(e)] for s, e in ivs]
        for d, ivs in get_effective_windows(teacher_id, d_from, d_to).items()
    }


def _lock_active_teacher(teacher_id: int) -> Teacher:
    """Блокирует строку преподавателя до конца транзакции. DoesNotExist пробрасывается (→404)."""
    # NO KEY UPDATE: сохранения графика одного преподавателя по-прежнему идут
    # по очереди, но вставки строк с FK на преподавателя (уроки, начисления —
    # они берут FOR KEY SHARE) этой блокировкой не тормозятся.
    teacher = Teacher.objects.select_for_update(no_key=True).get(pk=teacher_id)
    if not teacher.active:
        raise ScheduleError('Преподаватель в архиве — график только для просмотра.')
    return teacher


@transaction.atomic
def replace_template(teacher_id: int, by_day: dict[int, list[Interval]]) -> None:
    """Заменить шаблон целиком. Отсутствующий день = закрыт. Переписываются только изменившиеся дни."""
    # Ключ вне 0–6 молча потерялся бы в цикле ниже (день «закрыт» без ведома
    # вызывающего) — лучше явный отказ.
    for dow, intervals in by_day.items():
        # bool — подкласс int: True прошёл бы как понедельник.
        if not (isinstance(dow, int) and not isinstance(dow, bool) and 0 <= dow <= 6):
            raise ScheduleError(f'Недопустимый день недели: {dow!r}.')
        validate_intervals(intervals)
    _lock_active_teacher(teacher_id)
    current = get_template(teacher_id)
    for dow in range(7):
        wanted = normalize(by_day.get(dow, []))
        if wanted == current.get(dow, []):
            continue
        AvailabilityRule.objects.filter(teacher_id=teacher_id, day_of_week=dow).delete()
        AvailabilityRule.objects.bulk_create([
            AvailabilityRule(teacher_id=teacher_id, day_of_week=dow, start_time=s, end_time=e)
            for s, e in wanted
        ])


@transaction.atomic
def replace_week(teacher_id: int, monday: datetime.date,
                 days: dict[datetime.date, list[Interval]]) -> None:
    """
    Сохранить конкретную неделю: день, совпавший с шаблоном, — без исключения;
    отличающийся — исключение (пустой → «закрыт»). Прошлые даты — отказ.
    Даты, не переданные в days, не трогаются.
    """
    if monday.weekday() != 0:
        raise ScheduleError('monday должен быть понедельником.')
    week_end = monday + datetime.timedelta(days=6)
    today = _today()
    for d, intervals in days.items():
        validate_intervals(intervals)
        if not (monday <= d <= week_end):
            raise ScheduleError(f'Дата {d.isoformat()} вне недели {monday.isoformat()}.')
        if d < today:
            raise ScheduleError(f'Дата {d.isoformat()} уже прошла — её нельзя менять.')
    _lock_active_teacher(teacher_id)
    template = get_template(teacher_id)
    existing = _overrides_by_date(teacher_id, monday, week_end)
    for d, raw in days.items():
        wanted = normalize(raw)
        follows_template = wanted == template.get(js_weekday(d), [])
        # Целевое состояние дня: None — исключения нет (действует шаблон),
        # иначе исключение с этими интервалами ([] = «закрыт»). Совпало с
        # текущим — не трогаем, чтобы не плодить события журнала.
        target = None if follows_template else wanted
        if existing.get(d) == target:
            continue
        # Удаляем ВСЕ строки дня до вставки: иначе «закрыт» и интервалы
        # могли бы оказаться на одной дате (БД это не запрещает).
        AvailabilityOverride.objects.filter(teacher_id=teacher_id, date=d).delete()
        if target is None:
            continue
        if not target:
            AvailabilityOverride.objects.create(teacher_id=teacher_id, date=d)
        else:
            AvailabilityOverride.objects.bulk_create([
                AvailabilityOverride(teacher_id=teacher_id, date=d, start_time=s, end_time=e)
                for s, e in target
            ])


@transaction.atomic
def clear_override(teacher_id: int, d: datetime.date) -> None:
    """Сбросить дату к шаблону."""
    if d < _today():
        raise ScheduleError(f'Дата {d.isoformat()} уже прошла — её нельзя менять.')
    _lock_active_teacher(teacher_id)
    AvailabilityOverride.objects.filter(teacher_id=teacher_id, date=d).delete()
