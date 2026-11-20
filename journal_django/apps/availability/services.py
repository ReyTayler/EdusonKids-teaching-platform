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
from django.db.models import Q
from django.utils import timezone

from apps.availability.models import (
    GRID_END_MIN,
    GRID_START_MIN,
    SLOT_MIN,
    AvailabilityOverride,
    AvailabilityRule,
)
from apps.groups.models import GroupScheduleSlot
from apps.teachers.models import Teacher

# Интервал — пара минут от полуночи [начало, конец); конец может быть 1440 (24:00).
Interval = tuple[int, int]


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
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def fmt(minute: int) -> str:
    """Минута от полуночи → "HH:MM"; 1440 → "24:00" (конец суток, не 00:00)."""
    h, m = divmod(minute, 60)
    return f'{h:02d}:{m:02d}'


def _is_minute(value) -> bool:
    # bool — подкласс int: True прошёл бы как минута 1.
    return isinstance(value, int) and not isinstance(value, bool)


def _show(value) -> str:
    """Граница для текста ошибки: "HH:MM" в пределах суток, иначе как есть (-30 → "-30")."""
    if _is_minute(value) and GRID_START_MIN <= value <= GRID_END_MIN:
        return fmt(value)
    return repr(value)


def validate_intervals(intervals: list[Interval]) -> None:
    """
    Правила сетки в доменном слое: команды и бэкфиллы обходят сериализатор,
    а CHECK-и БД дали бы IntegrityError (500) вместо понятного отказа.
    """
    for start, end in intervals:
        label = f'Окно {_show(start)}–{_show(end)}'
        if not (_is_minute(start) and _is_minute(end)):
            raise ScheduleError(f'{label}: границы — целое число минут от полуночи.')
        if start < GRID_START_MIN or end > GRID_END_MIN:
            raise ScheduleError(
                f'{label}: выходит за пределы {fmt(GRID_START_MIN)}–{fmt(GRID_END_MIN)}.')
        if start % SLOT_MIN or end % SLOT_MIN:
            raise ScheduleError(f'{label}: границы должны быть кратны 30 минутам.')
        if start >= end:
            raise ScheduleError(f'{label}: начало должно быть раньше конца.')


def has_schedule(teacher_id: int) -> bool:
    return (AvailabilityRule.objects.filter(teacher_id=teacher_id).exists()
            or AvailabilityOverride.objects.filter(teacher_id=teacher_id).exists())


def get_template(teacher_id: int) -> dict[int, list[Interval]]:
    """Шаблон {day_of_week: [интервалы]}; дни без окон в словарь не попадают."""
    out: dict[int, list[Interval]] = defaultdict(list)
    rows = (AvailabilityRule.objects.filter(teacher_id=teacher_id)
            .order_by('day_of_week', 'start_minute')
            .values_list('day_of_week', 'start_minute', 'end_minute'))
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
    for d, s, e in qs.order_by('date', 'start_minute').values_list('date', 'start_minute', 'end_minute'):
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


def windows_payload_many(teacher_ids, d_from: datetime.date,
                         d_to: datetime.date) -> dict[int, dict[str, list[list[str]]]]:
    """
    windows_payload для многих преподавателей сразу (admin-календарь «все
    преподаватели»). Ровно 2 запроса при любом их числе: правила всех + исключения
    всех. Преподаватель без графика (как has_schedule: ни правила, ни исключения
    вообще) в результат не попадает — фон для него не рисуется.
    """
    ids = list(teacher_ids)
    if not ids:
        return {}

    templates: dict[int, dict[int, list[Interval]]] = defaultdict(lambda: defaultdict(list))
    rules = (AvailabilityRule.objects.filter(teacher_id__in=ids)
             .order_by('teacher_id', 'day_of_week', 'start_minute')
             .values_list('teacher_id', 'day_of_week', 'start_minute', 'end_minute'))
    for tid, dow, s, e in rules:
        templates[tid][dow].append((s, e))

    # Исключения окна + по одной строке-«маркеру» на преподавателя из любых дат:
    # маркер нужен только чтобы узнать «график есть» у того, у кого одни исключения
    # вне окна, — без отдельного запроса exists() на каждого (N+1) и без третьего.
    # Отбор маркеров ниже по дате окна, так что в окна они не протекают.
    markers = (AvailabilityOverride.objects.filter(teacher_id__in=ids)
               .order_by('teacher_id').distinct('teacher_id').values('id'))
    overrides = (AvailabilityOverride.objects
                 .filter(teacher_id__in=ids)
                 .filter(Q(date__gte=d_from, date__lte=d_to) | Q(id__in=markers))
                 .order_by('teacher_id', 'date', 'start_minute')
                 .values_list('teacher_id', 'date', 'start_minute', 'end_minute'))
    with_schedule: set[int] = set(templates)
    by_teacher: dict[int, dict[datetime.date, list[Interval]]] = defaultdict(dict)
    closed: set[tuple[int, datetime.date]] = set()
    for tid, d, s, e in overrides:
        with_schedule.add(tid)
        if not (d_from <= d <= d_to):
            continue
        day = by_teacher[tid].setdefault(d, [])
        if s is None:
            closed.add((tid, d))
        else:
            day.append((s, e))
    # «Закрыт» сильнее интервалов на ту же дату — как в _overrides_by_date.
    for tid, d in closed:
        by_teacher[tid][d] = []

    result: dict[int, dict[str, list[list[str]]]] = {}
    for tid in with_schedule:
        template = templates.get(tid, {})
        overrides_t = by_teacher.get(tid, {})
        payload: dict[str, list[list[str]]] = {}
        d = d_from
        while d <= d_to:
            ivs = overrides_t[d] if d in overrides_t else template.get(js_weekday(d), [])
            payload[d.isoformat()] = [[fmt(s), fmt(e)] for s, e in ivs]
            d += datetime.timedelta(days=1)
        result[tid] = payload
    return result


# Кусок свободного времени короче получаса урок не вместит — в сводке он шум.
MIN_FREE_MIN = 30


def subtract(windows: list[Interval], busy: list[Interval],
             min_len: int = MIN_FREE_MIN) -> list[Interval]:
    """Окна минус занятые интервалы (точно по минутам); куски короче min_len отбрасываются."""
    busy = normalize(busy)
    out: list[Interval] = []
    for w_start, w_end in normalize(windows):
        cursor = w_start
        for b_start, b_end in busy:
            if b_end <= cursor or b_start >= w_end:
                continue
            if b_start - cursor >= min_len:
                out.append((cursor, b_start))
            cursor = max(cursor, b_end)
            if cursor >= w_end:
                break
        if w_end - cursor >= min_len:
            out.append((cursor, w_end))
    return out


def free_windows_summary() -> list[dict]:
    """
    Свободные окна всех активных (не служебных) преподавателей — вкладка
    «Сводка» admin-календаря (спека 2026-11-20-admin-calendar-free-windows-summary).

    Окна = недельный шаблон МИНУС постоянные уроки: слоты расписания активных
    групп, действующие сегодня. Исключения на даты не учитываются намеренно —
    сводка о типовой неделе, а не о конкретной; по той же причине не учитываются
    разовые доп.уроки, переносы и замены. Преподаватель урока — group.teacher_id.

    Не больше 3 запросов при любом числе преподавателей и групп: преподаватели,
    правила, слоты (с JOIN на группу; без шаблонов запрос слотов не нужен).
    """
    teachers = list(Teacher.objects.filter(active=True, is_service=False)
                    .order_by('name', 'id').values_list('id', 'name'))
    if not teachers:
        return []
    ids = [tid for tid, _ in teachers]

    templates: dict[int, dict[int, list[Interval]]] = defaultdict(lambda: defaultdict(list))
    for tid, dow, s, e in (AvailabilityRule.objects.filter(teacher_id__in=ids)
                           .values_list('teacher_id', 'day_of_week', 'start_minute', 'end_minute')):
        templates[tid][dow].append((s, e))

    today = _today()
    busy: dict[int, dict[int, list[Interval]]] = defaultdict(lambda: defaultdict(list))
    slots = (GroupScheduleSlot.objects
             # Слоты нужны только тем, у кого есть шаблон: вычитать больше не из чего.
             .filter(group__active=True, group__teacher_id__in=list(templates),
                     effective_from__lte=today)
             .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
             .values_list('group__teacher_id', 'day_of_week', 'start_time',
                          'group__lesson_duration_minutes'))
    for tid, dow, start_time, duration in slots:
        start = start_time.hour * 60 + start_time.minute
        # Урок, уходящий за полночь, обрезаем концом суток: хвост на следующий
        # день — редкость, а сетка графика всё равно кончается в 24:00.
        busy[tid][dow].append((start, min(start + duration, GRID_END_MIN)))

    result: list[dict] = []
    for tid, name in teachers:
        template = templates.get(tid)
        days = None
        if template:
            days = {
                str(dow): [[fmt(s), fmt(e)]
                           for s, e in subtract(template.get(dow, []), busy[tid][dow])]
                for dow in range(7)
            }
        result.append({'id': tid, 'name': name, 'has_schedule': days is not None, 'days': days})
    return result


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
            AvailabilityRule(teacher_id=teacher_id, day_of_week=dow, start_minute=s, end_minute=e)
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
                AvailabilityOverride(teacher_id=teacher_id, date=d, start_minute=s, end_minute=e)
                for s, e in target
            ])


@transaction.atomic
def clear_override(teacher_id: int, d: datetime.date) -> None:
    """Сбросить дату к шаблону."""
    if d < _today():
        raise ScheduleError(f'Дата {d.isoformat()} уже прошла — её нельзя менять.')
    _lock_active_teacher(teacher_id)
    AvailabilityOverride.objects.filter(teacher_id=teacher_id, date=d).delete()
