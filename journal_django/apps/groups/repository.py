"""
GroupsRepository — единственное место доступа к данным раздела groups.

ORM-порт services/repo/groups.js (раздел 09).

Слоты расписания (json_agg в оригинале) собираются в Python из ORM (паттерн
4.6.1): отдельный запрос по group_id + сборка списка словарей в порядке
day_of_week, start_time. start_time форматируется как 'HH:MM:SS' (= start_time::text).

Контракт ответа пагинатора: { rows, total, page, page_size } — совпадает с
services/pagination.js (для admin SPA). create/update возвращают строку группы
БЕЗ slots/joined-полей (RETURNING * у оригинала); get/list — со slots.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any, Optional

from django.db import transaction
from django.db.models import (
    Case, Count, DecimalField, F, OuterRef, Q, Subquery, Sum, Value, When,
)
from django.db.models.functions import Cast, Coalesce, Now

from apps.core.utils.dates import msk_today
from apps.core.utils.orm import dictrow, dictrows
from apps.lessons.models import COURSE_LESSON_TYPES, Lesson

from .course_length import effective_total_lessons_expr
from .exceptions import ImmutableGroupFormat
from .models import Group, GroupScheduleSlot


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------

# Поля строки группы (соответствуют g.* / RETURNING *), в порядке схемы.
# lesson_number_offset — сдвиг нумерации курса для групп-продолжений (перевод в
# пустую персональную группу, Phase 1b): курс начинается не с урока №1, а с
# offset+step. Нужен фронту (LessonGrid), чтобы не рисовать «отсутствующие»
# ячейки 1..offset, на которых переведённый ученик заблокирован.
_GROUP_FIELDS = (
    'id', 'name', 'direction_id', 'teacher_id', 'is_individual',
    'lesson_duration_minutes', 'lessons_per_week', 'group_start_date',
    'vk_chat', 'active', 'created_at', 'lesson_number_offset', 'lessons_total',
)

# Whitelist sort_by → ORM-поле. g.id DESC — вторичная сортировка.
_SORTABLE: dict[str, str] = {
    'id':                      'id',
    'name':                    'name',
    'direction_id':            'direction_id',
    'teacher_id':              'teacher_id',
    'lesson_duration_minutes': 'lesson_duration_minutes',
    'lessons_per_week':        'lessons_per_week',
    'group_start_date':        'group_start_date',
    'active':                  'active',
    # Аннотация, а не колонка таблицы (см. _lessons_done_subquery в list_groups).
    'lessons_done':            'lessons_done',
}

_DEFAULT_SORT_BY = 'name'
_DEFAULT_SORT_DIR = 'asc'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_time(t) -> str:
    """datetime.time → 'HH:MM:SS' (повторяет start_time::text у PostgreSQL)."""
    return t.strftime('%H:%M:%S')


def _slots_by_group(group_ids: list[int]) -> dict[int, list[dict]]:
    """
    Собирает слоты расписания по группам (порядок day_of_week, start_time).

    Возвращает {group_id: [{'day_of_week': int, 'start_time': 'HH:MM:SS'}, ...]}.

    Только слоты, АКТИВНЫЕ на сегодня (МСК): `effective_from <= today <= effective_to`
    (открытый слот — `effective_to IS NULL`). Иначе после версионирующей смены
    расписания (apply_schedule_change закрывает старый слот и открывает новый —
    остаётся ДВЕ строки) группа показывалась бы занимающейся сразу в двух слотах.
    История версий отдаётся отдельно в get_schedule (для экрана редактирования).
    """
    result: dict[int, list[dict]] = {gid: [] for gid in group_ids}
    if not group_ids:
        return result
    today = msk_today()
    slots = (
        GroupScheduleSlot.objects
        .filter(group_id__in=group_ids, effective_from__lte=today)
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gte=today))
        .order_by('day_of_week', 'start_time')
        .values('group_id', 'day_of_week', 'start_time')
    )
    for s in slots:
        result[s['group_id']].append({
            'day_of_week': s['day_of_week'],
            'start_time': _fmt_time(s['start_time']),
        })
    return result


# Вес занятия в уроках курса: 45 мин = 0.5, иначе 1. Ещё одна копия формулы —
# полный список копий и причина, почему они до сих пор не сведены в одно место,
# описаны в apps.teachers.stats (_LESSON_WEIGHT).
_LESSON_WEIGHT = Case(
    When(lesson_duration_minutes=45, then=Value(Decimal('0.5'))),
    default=Value(Decimal('1')),
    output_field=DecimalField(max_digits=6, decimal_places=1),
)

_PROGRESS_FIELD = DecimalField(max_digits=6, decimal_places=1)


def _lessons_done_subquery():
    """Подзапрос «сколько уроков курса группа уже прошла» (колонка «Пройдено»).

    Единица — УРОКИ курса, а не календарные занятия: у 45-минутной группы
    4 занятия = 2 урока (та же единица, что у `lessons_total` и
    `directions.total_lessons`, см. apps.groups.course_length). Ровно ту же
    величину отдаёт apps.teachers.stats.group_progress на карточке
    преподавателя — определение прогресса группы держим единым.

    Считаются только курсовые типы (COURSE_LESSON_TYPES): доп.урок и сгорание
    пропуска идут сверх сетки курса и позицию плана не занимают.

    Коррелированный подзапрос, а не второй агрегат в общем annotate: агрегация
    по второй связи (lessons) перемножилась бы на строки первой (memberships) и
    соврала бы в ОБОИХ счётчиках — классическая ловушка Django ORM. Опирается на
    существующий индекс lessons_group_date_idx (group_id, lesson_date).
    """
    return Subquery(
        Lesson.objects
        .filter(group_id=OuterRef('pk'), lesson_type__in=COURSE_LESSON_TYPES)
        .values('group_id')
        .annotate(done=Sum(_LESSON_WEIGHT))
        .values('done')[:1],
        output_field=_PROGRESS_FIELD,
    )


def _apply_filters(qs, filters: dict[str, Any], include_inactive: bool = False):
    """
    Применяет фильтры (мимикрирует F.*-билдеры services/pagination.js):
      name (LIKE, регистронезависимо), direction_id, teacher_id,
      is_individual (bool), active (bool).

    Архив по умолчанию скрыт (как teachers/directions): если явного фильтра
    filter[active] нет и не передан include_inactive — выборка сужается до
    active=True. Приоритет: явный filter[active] → include_inactive (все) →
    дефолт active-only. Архивные группы доступны в разделе «Архив»
    (filter[active]=false) или через include_inactive=1 (напр. useGroupsAll(true)
    для маппинга имён/счётчиков групп на detail-страницах).
    """
    name = filters.get('name')
    if name not in (None, ''):
        qs = qs.filter(name__icontains=str(name))

    direction_id = filters.get('direction_id')
    if direction_id not in (None, ''):
        qs = qs.filter(direction_id=int(direction_id))

    teacher_id = filters.get('teacher_id')
    if teacher_id not in (None, ''):
        qs = qs.filter(teacher_id=int(teacher_id))

    is_individual = filters.get('is_individual')
    if is_individual not in (None, ''):
        val = is_individual is True or str(is_individual).lower() == 'true'
        qs = qs.filter(is_individual=val)

    active = filters.get('active')
    if active not in (None, ''):
        val = active is True or str(active).lower() == 'true'
        qs = qs.filter(active=val)
    elif not include_inactive:
        qs = qs.filter(active=True)

    return qs


def _group_row(group_id: int) -> Optional[dict]:
    """Строка группы (g.* / RETURNING *) без slots и joined-полей."""
    return dictrow(Group.objects.filter(id=group_id).values(*_GROUP_FIELDS))


# ---------------------------------------------------------------------------
# Repository functions
# ---------------------------------------------------------------------------

def list_groups(
    page: int = 1,
    page_size: int = 50,
    sort_by: str = _DEFAULT_SORT_BY,
    sort_dir: str = _DEFAULT_SORT_DIR,
    filters: Optional[dict] = None,
    include_inactive: bool = False,
) -> dict:
    """
    Возвращает пагинированный список групп со слотами и именами справочников.
    По умолчанию — только активные (архив скрыт); include_inactive=True или явный
    filter[active] расширяют выборку (см. _apply_filters).

    Контракт ответа: { rows, total, page, page_size }.
    """
    if filters is None:
        filters = {}

    sort_field = _SORTABLE.get(sort_by) or _SORTABLE[_DEFAULT_SORT_BY]
    order_prefix = '' if sort_dir == 'asc' else '-'

    qs = _apply_filters(Group.objects.all(), filters, include_inactive=include_inactive)

    total = qs.count()  # COUNT(*) без JOIN/агрегатов — как _GROUPS_COUNT_FROM

    offset = max(0, (page - 1) * page_size)
    # members_count — «Состав группы»: число АКТИВНЫХ членств (учеников сейчас в
    # группе). Одна агрегация с GROUP BY, без N+1. Аннотируем на пагинированной
    # выборке, а не на qs для total (чтобы COUNT(*) групп не превращать в группировку).
    # lessons_done — «Пройдено»: уроки курса, пройденные группой (подзапрос,
    # чтобы не пересекаться джойном с members_count — см. _lessons_done_subquery).
    # Cast — не косметика: без явного ::numeric(6,1) PostgreSQL отдаёт scale
    # операндов «как есть» (2 занятия по 90 мин → '2', а не '2.0'), см. тот же
    # разбор в apps.teachers.stats.group_progress.
    ordered = (
        qs.annotate(
            members_count=Count('memberships', filter=Q(memberships__active=True)),
            lessons_done=Cast(
                Coalesce(
                    _lessons_done_subquery(),
                    Value(Decimal('0')),
                    output_field=_PROGRESS_FIELD,
                ),
                output_field=_PROGRESS_FIELD,
            ),
        )
        .order_by(f'{order_prefix}{sort_field}', '-id')
    )
    rows = dictrows(
        ordered[offset:offset + page_size].values(
            *_GROUP_FIELDS,
            'members_count',
            'lessons_done',
            direction_name=F('direction__name'),
            direction_color=F('direction__color'),
            teacher_name=F('teacher__name'),
        )
    )

    slots_map = _slots_by_group([r['id'] for r in rows])
    for r in rows:
        r['slots'] = slots_map[r['id']]

    return {
        'rows': rows,
        'total': total,
        'page': page,
        'page_size': page_size,
    }


def get_group(group_id: int) -> Optional[dict]:
    """Возвращает одну группу со слотами расписания, либо None."""
    group = _group_row(group_id)
    if group is None:
        return None
    group['slots'] = _slots_by_group([group_id])[group_id]
    return group


def create_group(data: dict) -> dict:
    """
    Создаёт группу + слоты в одной транзакции.

    Возвращает строку группы (g.* / RETURNING *) БЕЗ slots — как оригинал.
    created_at — DB DEFAULT now() через Now(). NULLIF(vk_chat,'') → пустая → None.
    """
    with transaction.atomic():
        obj = Group.objects.create(
            name=data['name'],
            direction_id=data['direction_id'],
            teacher_id=data['teacher_id'],
            is_individual=bool(data.get('is_individual', False)),
            lesson_duration_minutes=data.get('lesson_duration_minutes', 90),
            lessons_per_week=data.get('lessons_per_week', 1),
            group_start_date=data.get('group_start_date') or None,
            vk_chat=data.get('vk_chat') or None,
            lessons_total=data.get('lessons_total'),
            created_at=Now(),
        )
        slots = data.get('slots') or []
        if slots:
            GroupScheduleSlot.objects.bulk_create([
                GroupScheduleSlot(
                    group_id=obj.pk,
                    day_of_week=s['day_of_week'],
                    start_time=s['start_time'],
                )
                for s in slots
            ])

    return _group_row(obj.pk)


def update_group(group_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет поля группы (PATCH через COALESCE). Расписание НЕ трогает.

    Если строки нет — возвращает None.
    Слоты через этот путь НЕ меняются: их версионированием ведает
    apply_schedule_change (закрыть старые / открыть новые с effective_from).
    Приём slots здесь стирал бы версионную историю (delete+recreate на sentinel-
    дате) и разъезжался с материализованным планом — поэтому даже если 'slots'
    попадёт в data, он игнорируется.
    is_individual/active могут быть False (sentinel "ключ присутствует").
    Возвращает строку группы (g.*) БЕЗ slots — как RETURNING * оригинала.
    """
    with transaction.atomic():
        obj = Group.objects.filter(id=group_id).first()
        if obj is None:
            return None

        # Формат группы неизменен: смена is_individual на существующей группе
        # запрещена (влияет на инвариант «≤1 активный membership»). Совпадающее
        # значение или отсутствие ключа — no-op (идемпотентный round-trip).
        if (
            'is_individual' in data
            and data['is_individual'] is not None
            and data['is_individual'] != obj.is_individual
        ):
            raise ImmutableGroupFormat()

        if data.get('name'):
            obj.name = data['name']
        # Неизменяемы после создания (фронт-форма блокирует эти поля при правке;
        # серилайзер их принимает, но PATCH игнорирует): направление,
        # преподаватель, длительность урока. В create они всегда заданы, поэтому
        # обычной смены нет. Преподаватель меняется только операцией «смена
        # преподавателя на все уроки» (apps.scheduling.change_teacher_permanent →
        # напрямую groups.teacher_id), НЕ через update_group.
        if data.get('is_individual') is not None and 'is_individual' in data:
            obj.is_individual = data['is_individual']   # формат: ImmutableGroupFormat выше запрещает СМЕНУ
        if data.get('lessons_per_week'):
            obj.lessons_per_week = data['lessons_per_week']
        # Дата начала: разрешена ТОЛЬКО первичная установка (было NULL) — она
        # завершает настройку группы и запускает автоген плана
        # (scheduling.autogenerate_plan_on_setup). Уже заданную дату менять нельзя
        # (форма её блокирует и не присылает).
        if data.get('group_start_date') and obj.group_start_date is None:
            obj.group_start_date = data['group_start_date']
        if data.get('vk_chat'):                       # NULLIF: пустая строка → не трогаем
            obj.vk_chat = data['vk_chat']
        if data.get('active') is not None and 'active' in data:
            obj.active = data['active']
        # Ручная длина курса: ключ присутствует → пишем как есть, включая None
        # («вернуться к длине направления»). Подгонку плана делает services.
        if 'lessons_total' in data:
            obj.lessons_total = data['lessons_total']

        obj.save()

    return _group_row(group_id)


def soft_delete_group(group_id: int) -> bool:
    """Мягкое удаление группы: active=false. True если строка найдена."""
    updated = Group.objects.filter(id=group_id).update(active=False)
    return updated > 0


# ---------------------------------------------------------------------------
# Расписание: версионные слоты (Ф3)
# ---------------------------------------------------------------------------

def _fmt_time_hm(t) -> Optional[str]:
    return t.strftime('%H:%M') if t else None


def _iso(d) -> Optional[str]:
    return d.isoformat() if d else None


def get_schedule(group_id: int) -> Optional[dict]:
    """
    Расписание группы для редактирования: версионные слоты (с датами действия).
    None если группы нет.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None
    slots = (
        GroupScheduleSlot.objects
        .filter(group_id=group_id)
        .order_by('effective_from', 'day_of_week', 'start_time')
        .values('id', 'day_of_week', 'start_time', 'effective_from', 'effective_to')
    )
    return {
        'slots': [{
            'id': s['id'],
            'day_of_week': s['day_of_week'],
            'start_time': _fmt_time_hm(s['start_time']),
            'effective_from': _iso(s['effective_from']),
            'effective_to': _iso(s['effective_to']),
        } for s in slots],
    }


def apply_schedule_change(group_id: int, effective_from, slots: list[dict]) -> Optional[dict]:
    """
    Постоянная смена расписания с даты effective_from (атомарно):
      - закрыть текущие ОТКРЫТЫЕ слоты (effective_to = effective_from - 1);
        если слот начинался бы позже даты закрытия (нулевой/отрицательный
        интервал) — удалить (он не успел стать активным);
      - вставить новые слоты (effective_from = дата, effective_to = NULL).
    Возвращает обновлённое расписание (get_schedule) или None если группы нет.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None
    eff = (
        effective_from if isinstance(effective_from, datetime.date)
        else datetime.date.fromisoformat(effective_from)
    )
    prev_to = eff - datetime.timedelta(days=1)

    with transaction.atomic():
        for s in GroupScheduleSlot.objects.filter(group_id=group_id, effective_to__isnull=True):
            if s.effective_from <= prev_to:
                s.effective_to = prev_to
                s.save(update_fields=['effective_to'])
            else:
                s.delete()
        GroupScheduleSlot.objects.bulk_create([
            GroupScheduleSlot(
                group_id=group_id,
                day_of_week=s['day_of_week'],
                start_time=s['start_time'],
                effective_from=eff,
            )
            for s in slots
        ])

    return get_schedule(group_id)


# ---------------------------------------------------------------------------
# Прогресс группы: матрица посещаемости «ученик × урок» (вкладка «Прогресс»)
# ---------------------------------------------------------------------------

def get_group_progress(group_id: int) -> Optional[dict]:
    """
    Обзорная матрица посещаемости группы: слоты уроков (столбцы) × ученики (строки).

    Ровно 3 запроса (участники + уроки + посещаемость) без N+1 — считаем на VPS.
    Исключение: строки с transferred_from_id добавляют +1 запрос на хоп цепочки
    переводов (cumulative_transferred_lessons, apps.memberships.repository) —
    переводы редки, намеренно не батчим (YAGNI), см. design doc
    2026-07-13-transfer-progress-matrix-design.md, раздел «Производительность».
    None если группы нет.

    Слот = lesson_number / step, где step = 0.5 для 45-минутных групп (half-lesson),
    иначе 1 (как LessonGrid). Для обычных групп это совпадает с ceil(lesson_number)
    (номера целые); для 45-мин групп каждая половина урока получает СВОЙ слот
    (0.5→1, 1.0→2, 1.5→3, …) — без коллапса пар. Число слотов = max(последний слот
    с уроком, direction.total_lessons / step, последний помеченный пропуском слот)
    — так план 45-мин группы показывает вдвое больше ячеек (2N на total_lessons=N),
    плановые, ещё не проведённые уроки видны пунктиром, а помеченному пропуску
    всегда есть где показаться.

    ОГРАНИЧЕНИЕ коллапса: если на один слот всё же попадает >1 урока (дубль номера
    на другую дату — не половинка), берётся только первый — посещаемость остальных
    в % не попадает. В боевых данных lesson_number уникален на группу (проверено
    SELECT'ом), поэтому на практике коллапс безопасен; зеркалит LessonGrid.

    Ячейка ученика по слоту (cells[i]) + параллельные флаги исходов для раскраски:
      True  — был (present=true); free[i]=true → бесплатное занятие (серый),
              иначе зелёный,
      False — не был (present=false); compensated[i]=true → закрыт доп.уроком/
              сожжён (жёлтый); unpaid_skip[i]=true → неоплачиваемый пропуск (синий);
              иначе красный,
      None  — урок не проведён (плановый слот) ИЛИ ученик не входил в состав на тот
              урок (нет записи посещаемости).

    Неоплачиваемый пропуск берётся ИЗ ДВУХ источников: lesson_attendance.unpaid_skip
    (проведённый урок) И пометка на слоте lesson_skips (LessonSkip) — последняя
    ставится в том числе на ещё не проведённые уроки. Поэтому слот с пометкой
    получает cells=False + unpaid_skip=True сразу, не дожидаясь, пока группа до него
    дойдёт: ученику, добавленному в группу на 38-м уроке, все 37 пропусков видны
    сразу, а не дорисовываются по мере проведения занятий. Реальная посещаемость
    сильнее пометки: если строка lesson_attendance есть, читается она.
    В held/present такие ячейки не идут (как плановый слот и перевод) и не
    расходуют «бюджет» transferred_lessons — явная пометка сильнее разметки перевода.
    Статистика held/present/pct: как ПОСЕЩЕНИЕ засчитываются был, бесплатное занятие
    и компенсированный пропуск (доп.урок/сгорание) — все идут в present (тянут % вверх).
    Обычный «не был» — held без present. Неоплачиваемый пропуск в held НЕ входит (не
    обязательство посещения — как перевод/плановый слот).

    transferred_lessons / transferred_from_group_name — если у ученика есть
    GroupMembership.transferred_from (перевод из другой группы, apps.memberships),
    transferred_lessons = min(floor(lessons_done в старой группе), slot_count) —
    столько ведущих пустых ячеек фронт красит статусом «Перевод» (cells не
    переписываются, разметка — чисто presentational). Направление старой группы
    не проверяется здесь — инвариант «только внутри направления» гарантирует
    apps.memberships.repository.transfer_membership на момент перевода. Если
    transferred_lessons == 0 (в т.ч. floor дал 0), transferred_from_group_name = None.

    ВАЖНО: transferred_lessons не гарантирует ровно N закрашенных ячеек — фронт
    расходует его только на cell === None (никогда не перекрывает реальную
    посещаемость), поэтому число закрашенных ячеек может быть меньше (если
    в первых слотах уже есть реальные True/False у этого ученика) — это
    осознанный компромисс, не баг. Плашка на карточке membership
    (MembershipsBlock) при этом показывает сырое число из старой группы без
    капа/floor — небольшой рассинхром с матрицей ожидаем и допустим.
    """
    import math

    from apps.lessons.models import Lesson, LessonAttendance, LessonSkip
    from apps.memberships.models import GroupMembership
    from apps.memberships.repository import cumulative_transferred_lessons

    grp = (
        Group.objects
        .filter(id=group_id)
        .values('id', 'lesson_duration_minutes',
                total_lessons=effective_total_lessons_expr())
        .first()
    )
    if grp is None:
        return None

    # Half-lesson: 45 минут → шаг 0.5 (см. CLAUDE.md); слот = lesson_number / step.
    step = 0.5 if grp['lesson_duration_minutes'] == 45 else 1.0

    # Ученики группы — строки матрицы, стабильный порядок по имени.
    # Только активные (active=True): membership удаляется мягко (active=false),
    # как и в list_memberships — выбывшие не должны появляться в «Прогрессе».
    members = list(
        GroupMembership.objects
        .filter(group_id=group_id, active=True)
        .order_by('student__full_name')
        .values(
            'student_id', 'transferred_from_id', name=F('student__full_name'),
            transferred_from_group_name=F('transferred_from__group__name'),
        )
    )

    # Реальные (проведённые) уроки: слот = ceil(lesson_number), первый на слот.
    lessons = list(
        Lesson.objects
        .filter(group_id=group_id)
        .order_by('lesson_number', 'id')
        .values('id', 'lesson_number', 'lesson_date')
    )

    lesson_by_slot: dict[int, dict] = {}
    max_slot = 0
    for lesson in lessons:
        slot = max(1, round(float(lesson['lesson_number']) / step))
        if slot not in lesson_by_slot:
            lesson_by_slot[slot] = lesson
        if slot > max_slot:
            max_slot = slot

    # Пометки «неоплачиваемый пропуск» живут на СЛОТЕ группы (lesson_skips), а не
    # на строке посещаемости, и ставятся в том числе на ЕЩЁ НЕ ПРОВЕДЁННЫЕ уроки
    # (см. LessonSkip). Раньше матрица читала только lesson_attendance.unpaid_skip,
    # поэтому у ученика, добавленного в группу на 38-м уроке, синими выходили лишь
    # те пропуски, под которыми уже есть проведённый урок, а остальные дорисовыва-
    # лись по мере того, как группа доходила до этих слотов. Один запрос на матрицу.
    skip_slots: set[tuple[int, int]] = set()
    max_skip_slot = 0
    for skip_student_id, skip_number in (
        LessonSkip.objects
        .filter(group_id=group_id)
        .values_list('student_id', 'lesson_number')
    ):
        skip_slot = max(1, round(float(skip_number) / step))
        skip_slots.add((skip_student_id, skip_slot))
        if skip_slot > max_skip_slot:
            max_skip_slot = skip_slot

    total_lessons = grp['total_lessons'] or 0
    # max_skip_slot в ширине матрицы: помеченный пропуск обязан иметь свой столбец,
    # даже если он выходит за длину курса — иначе часть пометок просто негде показать.
    slot_count = max(max_slot, round(total_lessons / step), max_skip_slot)

    # Посещаемость всех уроков группы разом: (lesson_id, student_id) →
    # (present, is_free, unpaid_skip) — исходы для раскраски матрицы.
    lesson_ids = [lesson['id'] for lesson in lesson_by_slot.values()]
    att_map: dict[tuple[int, int], tuple[bool, bool, bool]] = {}
    if lesson_ids:
        for lid, sid, present, is_free, unpaid_skip in (
            LessonAttendance.objects
            .filter(lesson_id__in=lesson_ids)
            .values_list('lesson_id', 'student_id', 'present', 'is_free', 'unpaid_skip')
        ):
            att_map[(lid, sid)] = (present, is_free, unpaid_skip)

    # Компенсированные пропуски: (missed_lesson_id, student_id), по которым пропуск
    # закрыт доп.уроком (makeup_done) или сожжён (burned). Ячейку такого пропуска
    # фронт красит жёлтым («был через доп.урок / урок сожжён»), а не красным.
    # Один запрос на всю матрицу — без N+1. Ленивый импорт (цикл apps.extra_lessons).
    compensated_map: set[tuple[int, int]] = set()
    if lesson_ids:
        from apps.extra_lessons.models import BURNED, MAKEUP_DONE, AbsenceResolution
        compensated_map = set(
            AbsenceResolution.objects
            .filter(missed_lesson_id__in=lesson_ids, status__in=[MAKEUP_DONE, BURNED])
            .values_list('missed_lesson_id', 'student_id')
        )

    # Столбцы (слоты). held=True — урок проведён (есть запись), иначе плановый.
    slots = []
    held_slots = 0
    for slot in range(1, slot_count + 1):
        lesson = lesson_by_slot.get(slot)
        if lesson is not None:
            held_slots += 1
        slots.append({
            'slot': slot,
            'lesson_id': lesson['id'] if lesson else None,
            'date': lesson['lesson_date'] if lesson else None,
            'held': lesson is not None,
        })

    # Строки учеников: cells выровнены по slots (None — не проведён / не в составе).
    students = []
    for member in members:
        sid = member['student_id']
        cells: list[Optional[bool]] = []
        compensated: list[bool] = []
        free: list[bool] = []
        skip_cells: list[bool] = []
        present = 0
        held = 0
        for col in slots:
            # Пометка на слоте — самостоятельный факт: «этот урок ученик не
            # посещает, денег ноль». Она не ждёт проведения урока, поэтому слот
            # красится синим сразу, а не по факту.
            is_skip_slot = (sid, col['slot']) in skip_slots
            if not col['held']:
                # cells=False (а не None) — фронт красит синим только ячейки
                # cell===false (см. computeCellStatuses); заодно такая ячейка
                # перестаёт расходовать «бюджет» переведённых уроков, и это
                # верно: явная пометка сильнее presentational-разметки перевода.
                cells.append(False if is_skip_slot else None)
                compensated.append(False)
                free.append(False)
                skip_cells.append(is_skip_slot)
                # held/present не трогаем: неоплачиваемый пропуск — не
                # обязательство посещения (как плановый слот и перевод).
                continue
            key = (col['lesson_id'], sid)
            if key not in att_map:
                # Строки-посещения на исходном уроке нет. Но если по этому уроку у
                # ученика есть ЗАКРЫТАЯ резолюция (makeup_done/burned) — пропуск
                # отработан доп.уроком/сожжён (ученик добавлен в группу после
                # проведения урока и догоняет его доп.уроком) → жёлтая ячейка, а не
                # «Не проведён». Иначе — ученик действительно не входил в состав.
                if (col['lesson_id'], sid) in compensated_map:
                    cells.append(False)
                    compensated.append(True)
                    free.append(False)
                    skip_cells.append(False)
                    # Отработан доп.уроком/сожжён — засчитывается как посещение.
                    held += 1
                    present += 1
                elif is_skip_slot:
                    # Урок проведён, строки посещаемости у ученика нет (его не было
                    # в составе на момент записи), но слот помечен пропуском —
                    # это синяя ячейка, а не «Не проведён». Реальная посещаемость
                    # сюда не попадает: ветка работает только при отсутствии att_map.
                    cells.append(False)
                    compensated.append(False)
                    free.append(False)
                    skip_cells.append(True)
                else:
                    cells.append(None)
                    compensated.append(False)
                    free.append(False)
                    skip_cells.append(False)
                continue
            is_present, is_free_c, is_skip_c = att_map[key]
            is_present = bool(is_present)
            is_free_c = bool(is_free_c)
            is_skip_c = bool(is_skip_c)
            is_comp = (not is_present) and (col['lesson_id'], sid) in compensated_map
            # Засчитываются как ПОСЕЩЕНИЕ (present, тянут % вверх): был, бесплатное
            # занятие (is_present=true), доп.урок/сгорание (is_comp). Обычный «не был»
            # — held без present. Неоплачиваемый пропуск — вне held (не обязательство
            # посещения, как перевод/плановый слот).
            if is_present or is_comp:
                held += 1
                present += 1
            elif not is_skip_c:
                held += 1
            cells.append(is_present)
            free.append(is_present and is_free_c)
            # Жёлтая ячейка приоритетнее синей (компенсированный пропуск ≠ неопл.).
            skip_cells.append((not is_present) and is_skip_c and not is_comp)
            compensated.append(is_comp)
        transferred_lessons = 0
        transferred_from_group_name = None
        locked_through = Decimal('0')
        if member['transferred_from_id']:
            cumulative = cumulative_transferred_lessons(member['transferred_from_id'])
            locked_through = cumulative
            transferred_lessons = min(math.floor(float(cumulative)), slot_count)
            if transferred_lessons > 0:
                transferred_from_group_name = member['transferred_from_group_name']

        students.append({
            'student_id': sid,
            'name': member['name'],
            'present': present,
            'held': held,
            'pct': round(present / held * 100) if held else 0,
            'cells': cells,
            'compensated': compensated,
            'free': free,
            'unpaid_skip': skip_cells,
            'transferred_lessons': transferred_lessons,
            'transferred_from_group_name': transferred_from_group_name,
            'locked_through': locked_through,
        })

    return {
        'group_id': group_id,
        'total_slots': slot_count,
        'held_slots': held_slots,
        'slots': slots,
        'students': students,
    }
