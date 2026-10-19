"""
Доступ к данным для планирования занятий — батч-запросы (без N+1).

Единственное место ORM-доступа раздела scheduling. Все чтения — по спискам
group_id одним запросом, группировка в Python. Даты/время приходят как
date/time объекты (managed-модели), генератор работает с ними напрямую.
"""
from __future__ import annotations

import datetime
from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import Exists, F, Min, OuterRef, Q

from apps.core.utils.dates import msk_now
from apps.groups.course_length import effective_total_lessons_expr
from apps.groups.models import GroupScheduleSlot
from apps.groups.models import Group
from apps.lessons.models import COURSE_LESSON_TYPES, Lesson
from apps.memberships.models import GroupMembership
from apps.teachers.models import Teacher
from apps.scheduling import health, planner
from apps.scheduling.exceptions import PlanHasRecordedLessons, PlanResyncBlocked
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import (
    CANCELLED, DONE, MOVED, OVERDUE, PENDING, Slot, _step_for, _walk,
    planned_status,
)
from apps.scheduling.planner import Fact, PlannedRow


def active_groups(teacher_id: int | None = None) -> list[dict]:
    """Активные группы (+ преподаватель/направление/длина курса). Опц. скоуп по учителю."""
    qs = Group.objects.filter(active=True)
    if teacher_id is not None:
        qs = qs.filter(teacher_id=teacher_id)
    return list(
        qs.values(
            'id', 'name', 'is_individual', 'lesson_duration_minutes', 'group_start_date',
            teacher_pk=F('teacher_id'),
            teacher_name=F('teacher__name'),
            direction_name=F('direction__name'),
            direction_color=F('direction__color'),
            total_lessons=effective_total_lessons_expr(),
        )
    )


def slots_by_group(group_ids: list[int]) -> dict[int, list[Slot]]:
    result: dict[int, list[Slot]] = {}
    if not group_ids:
        return result
    rows = GroupScheduleSlot.objects.filter(group_id__in=group_ids).values(
        'group_id', 'day_of_week', 'start_time', 'effective_from', 'effective_to',
    )
    for r in rows:
        result.setdefault(r['group_id'], []).append(Slot(
            day_of_week=r['day_of_week'],
            start_time=r['start_time'],
            effective_from=r['effective_from'],
            effective_to=r['effective_to'],
        ))
    return result


def student_names_by_group(group_ids: list[int]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    if not group_ids:
        return result
    rows = (
        GroupMembership.objects
        .filter(group_id__in=group_ids, active=True)
        .order_by('student__full_name')
        .values('group_id', name=F('student__full_name'))
    )
    for r in rows:
        result.setdefault(r['group_id'], []).append(r['name'])
    return result


def teacher_names() -> dict[int, str]:
    """id → имя преподавателя (для teacher_override в переносах)."""
    return dict(Teacher.objects.values_list('id', 'name'))


# ---------------------------------------------------------------------------
# ЧТЕНИЕ planned_lessons для календаря (шаг 5 — read-on-materialized).
# Скоуп по planned_lesson.teacher_id (преподаватель КОНКРЕТНОГО занятия), не по
# учителю группы: смена препода занятия перекидывает урок между календарями.
# ---------------------------------------------------------------------------

def planned_lessons_in_window(
    window_from: datetime.date,
    window_to: datetime.date,
    teacher_id: int | None = None,
) -> list[dict]:
    """
    Плановые занятия за окно — источник GET /api/calendar. teacher_id=None →
    ВСЕ преподаватели (admin-календарь без фильтра, вся школа); иначе — один.

    Скоуп (при заданном teacher_id) — по `planned_lesson.teacher_id` (не по учителю
    группы): группа занятия может принадлежать другому учителю (после смены препода
    занятия). Только активные группы (паритет с прежним compute-путём `active_groups`).

    Батч-джойны, без N+1: один запрос строк с join группы/направления. Имена
    преподавателей и учеников по группам подтягивает вызывающий (services) через
    существующие справочники `teacher_names` / `student_names_by_group`.

    Возвращает «сырые» словари (date/time — объекты) для services._planned_occurrence_dict.
    """
    qs = PlannedLesson.objects.filter(
        group__active=True,
        scheduled_date__gte=window_from,
        scheduled_date__lte=window_to,
    )
    if teacher_id is not None:
        qs = qs.filter(
            Q(substitute_teacher_id=teacher_id)
            | Q(substitute_teacher_id__isnull=True, teacher_id=teacher_id)
        )
    return list(
        qs
        .values(
            'id', 'seq', 'lesson_number', 'scheduled_date', 'scheduled_time',
            'teacher_id', 'substitute_teacher_id', 'status', 'fact_lesson_id',
            'moved_from_date',
            group_pk=F('group_id'),
            group_name=F('group__name'),
            is_individual=F('group__is_individual'),
            lesson_duration_minutes=F('group__lesson_duration_minutes'),
            group_teacher_id=F('group__teacher_id'),
            group_vk_chat=F('group__vk_chat'),
            direction_name=F('group__direction__name'),
            direction_color=F('group__direction__color'),
        )
    )


def unfilled_planned_lessons(
    today: datetime.date, teacher_id: int | None = None,
) -> list[dict]:
    """
    Незаполненные плановые занятия ВСЕХ активных групп по школе (или одного
    преподавателя) с датой <= today — источник вкладки «Заполнить».

    Группы БЕЗ активных членств пропускаются: заполнять там нечего (урока не
    было — некому), а их план продолжал бы копить просрочку и маскировать
    настоящие долги преподавателей. Сюда попадают оба случая: группу завели, но
    учеников не набрали, и группа, из которой все ушли. EXISTS-подзапрос, а не
    JOIN + DISTINCT: строк плана на группу много, дубли пришлось бы схлопывать.

    Overdue-порог по времени (момент урока в МСК уже прошёл) досчитывает вызывающий
    (fill_service), как и `_planned_status`/`build_calendar` — здесь только грубый
    DB-срез по дате. `status=PENDING` + `fact_lesson__isnull` исключают done/
    cancelled/уже-связанные с фактом строки. Скоуп преподавателя — по эффективному
    исполнителю (замена ИЛИ штатный препод занятия), как в `planned_lessons_in_window`.

    Возвращает «сырые» словари (date/time — объекты) для fill_service.
    """
    has_students = GroupMembership.objects.filter(
        group_id=OuterRef('group_id'), active=True)
    qs = PlannedLesson.objects.filter(
        Exists(has_students),
        group__active=True,
        status=PENDING,
        fact_lesson__isnull=True,
        scheduled_date__lte=today,
    )
    if teacher_id is not None:
        qs = qs.filter(
            Q(substitute_teacher_id=teacher_id)
            | Q(substitute_teacher_id__isnull=True, teacher_id=teacher_id)
        )
    return list(
        qs.order_by('scheduled_date', 'scheduled_time').values(
            'id', 'seq', 'scheduled_date', 'scheduled_time', 'lesson_number',
            'teacher_id', 'substitute_teacher_id',
            group_pk=F('group_id'),
            group_name=F('group__name'),
            direction_name=F('group__direction__name'),
            direction_color=F('group__direction__color'),
        )
    )


def has_unfilled_before(group_id: int, lesson_date) -> bool:
    """
    Есть ли у группы незакрытое курсовое занятие, чья ПЛАНОВАЯ дата строго раньше
    отмечаемой (lesson_date). True → отметку урока за lesson_date писать нельзя.

    Отметка за дату D закрывает следующий по порядку незакрытый номер урока
    (lesson_number = прогресс + шаг), а link_facts привязывает факт к плановой
    строке по этому номеру. Если самое раннее незакрытое занятие по расписанию
    приходится на день РАНЬШЕ D, факт с датой D сядет на плановую строку того
    раннего дня — план и факт разъедутся между разными днями. Это и блокируем.

    Занятия того же дня (scheduled_date == D) долгом НЕ считаются: у мультислотовых
    групп оба занятия дня стоят в плане на D, их факты тоже получают дату D, и
    расхождения план/факт не возникает при любом порядке отметки. Часы здесь не
    важны — важно только соотношение ДАТ (см. design doc, раздел «мультислот»):
    внутри одного дня блокировка ничего не защищала бы, а два незакрытых занятия
    дня заперли бы друг друга в тупик.

    Учитываются только курсовые строки (seq не NULL): маркеры отмены и разовые
    занятия к последовательности курса не относятся. Один запрос EXISTS, попадает
    в индекс (group_id, scheduled_date).
    """
    return (
        PlannedLesson.objects
        .filter(
            group_id=group_id,
            seq__isnull=False,
            status=PENDING,
            fact_lesson__isnull=True,
            scheduled_date__lt=lesson_date,
        )
        .exists()
    )


def groups_without_plan(teacher_id: int) -> list[dict]:
    """
    Активные группы преподавателя (по `group.teacher_id`) без единой строки
    planned_lessons — data-quality сигнал `unscheduled` для календаря.

    Причина: no_start_date / no_total_lessons / no_slots (нельзя сгенерировать) или
    not_generated (можно, но план ещё не материализован). Батч: active_groups +
    один DISTINCT по planned_lessons + slots_by_group.
    """
    groups = active_groups(teacher_id)
    if not groups:
        return []
    ids = [g['id'] for g in groups]
    with_plan = set(
        PlannedLesson.objects
        .filter(group_id__in=ids)
        .values_list('group_id', flat=True)
        .distinct()
    )
    missing = [g for g in groups if g['id'] not in with_plan]
    if not missing:
        return []
    slots = slots_by_group([g['id'] for g in missing])

    out: list[dict] = []
    for g in missing:
        if g['group_start_date'] is None:
            reason = 'no_start_date'
        elif g['total_lessons'] is None:
            reason = 'no_total_lessons'
        elif not slots.get(g['id']):
            reason = 'no_slots'
        else:
            reason = 'not_generated'
        out.append({'group': g['name'], 'reason': reason})
    return out


# ---------------------------------------------------------------------------
# ЧТЕНИЕ planned_lessons для реестра куратора (admin, вся школа).
# Скоуп НЕ по преподавателю — единственное место, где planned_lessons читаются
# по ВСЕМ активным группам разом. Батч, без N+1. Потребитель —
# apps/dashboard/registry_service.py.
# ---------------------------------------------------------------------------

def occurrences_on_date(target: datetime.date) -> list[dict]:
    """
    Плановые занятия ВСЕХ активных групп на конкретную дату (для «Потока дня»).

    Исключены маркеры отмены/переноса (cancelled/moved) — показываем реальные
    занятия дня. Возвращает value-словари (time — объект); имена преподавателей и
    учеников подтягивает вызывающий через teacher_names / student_names_by_group.
    """
    return list(
        PlannedLesson.objects
        .filter(group__active=True, scheduled_date=target)
        .exclude(status__in=(CANCELLED, MOVED))
        .order_by('scheduled_time', 'group_id')
        .values(
            'id', 'scheduled_time', 'status', 'teacher_id',
            group_pk=F('group_id'),
            group_name=F('group__name'),
            duration_minutes=F('group__lesson_duration_minutes'),
        )
    )


def next_occurrence_by_group(from_date: datetime.date) -> dict[int, datetime.date]:
    """
    group_id → ближайшая плановая дата (>= from_date) среди pending/overdue строк
    активных групп. Один запрос с Min-агрегацией. Для колонки «Ближайший» (min по
    группам ученика вычисляет вызывающий).
    """
    rows = (
        PlannedLesson.objects
        .filter(
            group__active=True,
            scheduled_date__gte=from_date,
            status__in=(PENDING, OVERDUE),
        )
        .values('group_id')
        .annotate(nx=Min('scheduled_date'))
    )
    return {r['group_id']: r['nx'] for r in rows}


def cancellations_count(period_start, period_end) -> int:
    """
    Число отменённых плановых занятий (маркеры status='cancelled') активных групп
    в полуинтервале [period_start, period_end) — KPI «Отмены» реестра.
    Границы — 'YYYY-MM-DD' строки или date (Django принимает оба для DateField).
    """
    return (
        PlannedLesson.objects
        .filter(
            group__active=True,
            status=CANCELLED,
            scheduled_date__gte=period_start,
            scheduled_date__lt=period_end,
        )
        .count()
    )


# ---------------------------------------------------------------------------
# ЗАПИСЬ плана (materialize-on-write). Батч, без N+1; идемпотентно.
# ---------------------------------------------------------------------------

# Поля курсовой строки, которые несёт PlannedRow и которые бэкфилл/генерация
# может обновлять у ещё непроведённых строк (даты/время/препод/статус/номер).
_PLAN_UPDATE_FIELDS = (
    'lesson_number', 'scheduled_date', 'scheduled_time', 'teacher', 'status',
    'updated_at',
)


def reset_plan(group_id: int) -> int:
    """Удалить весь план группы (для чистой перегенерации, backfill --reset).

    Разрушительно: сбрасывает и ручные операции (переносы/отмены), и линковку с
    фактами. Использовать только при полном пересборе плана. Возвращает число
    удалённых строк."""
    deleted, _ = PlannedLesson.objects.filter(group_id=group_id).delete()
    return deleted


def persist_plan(group_id: int, rows: list[PlannedRow], *, create_only: bool = False) -> int:
    """
    Идемпотентно записать/обновить курсовые строки плана группы в planned_lessons.

    Ключ идемпотентности — UniqueConstraint(group, seq): одна строка на позицию
    курса. Строки со status='done' (проведённые) НЕ перезаписываются. Строки, чьи
    значения не изменились, пропускаются (повторный прогон → 0 изменений).

    create_only=True: существующие строки НЕ обновляются — только досоздаются
    недостающие seq (расширение курса). Используется эндпоинтом generate, чтобы
    повторная генерация не затирала ручные операции (переносы/отмены/смену препода)
    над уже материализованным планом. Полный пересбор — backfill --reset (reset_plan).

    Работает только с курсовыми строками (seq задан) — ровно то, что отдаёт
    planner.generate(); extra/маркеры (seq=NULL) сюда не попадают.

    created_at/updated_at заполняются msk_now() (см. миграцию 0001: DB-default
    now() — на случай raw-INSERT; при ORM-вставке значение задаём явно, т.к.
    Django включает колонку в INSERT).

    Возвращает число фактически изменённых строк (созданных + обновлённых).
    """
    if not rows:
        return 0

    now = msk_now()
    with transaction.atomic():
        existing = {
            p.seq: p
            for p in PlannedLesson.objects.filter(
                group_id=group_id, seq__isnull=False,
            )
        }

        to_create: list[PlannedLesson] = []
        to_update: list[PlannedLesson] = []

        for r in rows:
            if r.seq is None:
                # generate() курсовые строки всегда с seq; extra — вне бэкфилла.
                continue
            ex = existing.get(r.seq)
            if ex is not None and create_only:
                # Существующая строка при create_only не трогается (сохраняем
                # ручные операции); досоздаём только отсутствующие seq.
                continue
            if ex is None:
                to_create.append(PlannedLesson(
                    group_id=group_id,
                    seq=r.seq,
                    lesson_number=r.lesson_number,
                    scheduled_date=r.scheduled_date,
                    scheduled_time=r.scheduled_time,
                    teacher_id=r.teacher_id,
                    status=r.status,
                    created_at=now,
                    updated_at=now,
                ))
                continue
            if ex.status == DONE:
                # Проведённые не трогаем.
                continue
            # Обновляем только при реальном изменении — иначе повтор идемпотентен.
            if (
                ex.lesson_number == r.lesson_number
                and ex.scheduled_date == r.scheduled_date
                and ex.scheduled_time == r.scheduled_time
                and ex.teacher_id == r.teacher_id
                and ex.status == r.status
            ):
                continue
            ex.lesson_number = r.lesson_number
            ex.scheduled_date = r.scheduled_date
            ex.scheduled_time = r.scheduled_time
            ex.teacher_id = r.teacher_id
            ex.status = r.status
            ex.updated_at = now
            to_update.append(ex)

        if to_create:
            PlannedLesson.objects.bulk_create(to_create)
        if to_update:
            PlannedLesson.objects.bulk_update(to_update, list(_PLAN_UPDATE_FIELDS))

    return len(to_create) + len(to_update)


def link_facts(group_id: int) -> int:
    """
    Слинковать плановые строки группы с проведёнными уроками (факт) и проставить
    status='done'. Плановая дата позиции ставится равной дате факта — инвариант
    спеки 2026-08-05 §2 (прежде дата НЕ перезаписывалась, из-за чего проведённые
    занятия оставались в календаре не в свой день; так сломались ПИ337/ПИ314/РИ283).

    Порядок матчинга (по надёжности):
      1. по `lesson_number` — позиция урока в курсе, записанная при проведении:
         надёжнее даты (уроки прошлого часто на сдвинутых датах — праздники,
         разовые переносы до этой системы).
      2. по точной дате (fallback) — для фактов без совпадения по номеру
         (напр. lesson_number не записан), но с датой, равной плановой.

    Один факт ↔ одна плановая строка (fact_lesson unique): уже привязанные факты
    исключаются, каждый кандидат используется один раз за прогон. Батч, без N+1.
    Идемпотентно — повторный прогон линкует 0. Возвращает число новых линковок.
    """
    now = msk_now()
    with transaction.atomic():
        already_linked = set(
            PlannedLesson.objects
            .filter(group_id=group_id, fact_lesson_id__isnull=False)
            .values_list('fact_lesson_id', flat=True)
        )

        # Непривязанные факты; по номеру берём самый ранний по дате (детерминизм).
        # Только настоящие занятия курса: сгорание пропуска (burned) — денежное
        # списание без занятия, доп.урок (extra) идёт сверх сетки курса. Без этого
        # фильтра они занимали позицию плана, и курс показывал занятие проведённым.
        facts = [
            f for f in Lesson.objects
            .filter(group_id=group_id, lesson_type__in=COURSE_LESSON_TYPES)
            .order_by('lesson_date', 'id')
            .values('id', 'lesson_date', 'lesson_number')
            if f['id'] not in already_linked
        ]
        if not facts:
            return 0

        by_number: dict[object, list[dict]] = defaultdict(list)
        by_date: dict[datetime.date, list[dict]] = defaultdict(list)
        for f in facts:
            if f['lesson_number'] is not None:
                by_number[f['lesson_number']].append(f)
            by_date[f['lesson_date']].append(f)

        rows = list(
            PlannedLesson.objects
            .filter(group_id=group_id, fact_lesson_id__isnull=True)
            .order_by('seq')
        )

        used_fact_ids: set = set()
        to_update: list[PlannedLesson] = []

        def _take(cands: list[dict]):
            for f in cands:
                if f['id'] not in used_fact_ids:
                    return f
            return None

        # Проход 1: по lesson_number.
        for p in rows:
            if p.lesson_number is None:
                continue
            chosen = _take(by_number.get(p.lesson_number, []))
            if chosen is None:
                continue
            used_fact_ids.add(chosen['id'])
            p.fact_lesson_id = chosen['id']
            p.status = DONE
            p.scheduled_date = chosen['lesson_date']
            p.moved_from_date = None
            p.updated_at = now
            to_update.append(p)

        linked_row_ids = {id(p) for p in to_update}

        # Проход 2: по точной дате (fallback) для ещё не привязанных строк.
        for p in rows:
            if id(p) in linked_row_ids:
                continue
            chosen = _take(by_date.get(p.scheduled_date, []))
            if chosen is None:
                continue
            used_fact_ids.add(chosen['id'])
            p.fact_lesson_id = chosen['id']
            p.status = DONE
            p.scheduled_date = chosen['lesson_date']
            p.moved_from_date = None
            p.updated_at = now
            to_update.append(p)

        if to_update:
            PlannedLesson.objects.bulk_update(
                to_update,
                ['fact_lesson', 'status', 'scheduled_date', 'moved_from_date',
                 'updated_at'],
            )

    return len(to_update)


def relink_fact(lesson_id: int) -> bool:
    """
    Пересчитать привязку ОДНОГО факта по инварианту «номер факта = номер позиции».

    Зачем: link_facts работает только со свободными фактами и пустыми позициями —
    существующую привязку она не переносит никогда. Поэтому после правки урока
    (PATCH /api/admin/lessons/:id) факт мог остаться на чужой позиции, и позиция с
    его номером висела «не проведена» при проведённом занятии (случай ВДГ18).

    Матчинг ТОЛЬКО по lesson_number: цель — найти позицию своего порядкового номера
    в курсе, а не позицию с подходящей датой (факт мог быть записан на дату,
    отличную от любой плановой — например задним числом). После переноса привязки
    целевая позиция встаёт на дату факта (sync_position_date-инвариант, спека §2) —
    это уже не «законное расхождение», как раньше, а обязательная синхронизация.

    Консервативно — ничего не ломаем:
      • не-курсовые факты (extra/burned) игнорируем (позиций они не занимают);
      • если позиции с таким номером нет ИЛИ она занята другим фактом — оставляем
        как есть (иначе выбили бы чужую привязку);
      • факт без номера не трогаем.

    True — привязка переехала. Под select_for_update: конкурирующий record_lesson/
    delete сериализуется на тех же строках.
    """
    lesson = (
        Lesson.objects.filter(id=lesson_id)
        .values('id', 'group_id', 'lesson_number', 'lesson_type', 'lesson_date')
        .first()
    )
    if lesson is None or lesson['lesson_type'] not in COURSE_LESSON_TYPES:
        return False
    if lesson['lesson_number'] is None:
        return False

    with transaction.atomic():
        current = (
            PlannedLesson.objects.select_for_update()
            .filter(fact_lesson_id=lesson_id).first()
        )
        if current is not None and current.lesson_number == lesson['lesson_number']:
            return False  # уже на своей позиции

        target = (
            PlannedLesson.objects.select_for_update()
            .filter(group_id=lesson['group_id'], seq__isnull=False,
                    lesson_number=lesson['lesson_number'])
            .order_by('seq')  # детерминизм: номер обязан быть уникален, но не полагаемся
            .first()
        )
        if target is None or target.fact_lesson_id is not None:
            return False

        now = msk_now()
        # Сначала освободить прежнюю позицию: fact_lesson уникален.
        if current is not None:
            current.fact_lesson_id = None
            current.status = PENDING
            current.updated_at = now
            current.save(update_fields=['fact_lesson', 'status', 'updated_at'])

        target.fact_lesson_id = lesson_id
        target.status = DONE
        target.scheduled_date = lesson['lesson_date']
        target.moved_from_date = None
        target.updated_at = now
        target.save(update_fields=[
            'fact_lesson', 'status', 'scheduled_date', 'moved_from_date',
            'updated_at',
        ])
        return True


def find_course_position_by_date(group_id: int, lesson_date) -> dict | None:
    """
    Позиция курса группы, назначенная на эту дату — для записи урока без явного
    plannedLessonId. Оба входа кабинета (календарь и «Мои уроки») id теперь шлют,
    так что сюда попадают только случаи, когда его не с чего взять: устаревший id
    из закэшированного календаря либо группа без плана занятий.

    Разовый перенос УВОЗИТ позицию вместе с номером (reschedule_lesson не трогает
    seq/lesson_number, меняет только scheduled_date), поэтому поиск по дате
    находит перенесённое занятие на НОВОЙ дате с исходным номером — это и нужно.

    Возвращает позицию ВНЕ ЗАВИСИМОСТИ от того, занята ли она: занятая означает
    «урок за эту дату уже записан», и вызывающий обязан отказать, а не молча
    уйти на фолбэк — иначе повторная отправка снова создаст дубль.

    None означает ТОЛЬКО «неоднозначно» — двух разных случаев под этим именем
    больше нет:
      • позиций на эту дату нет вовсе (группа без плана, дата вне плана) —
        законный откат к прежнему расчёту номера;
      • 2+ свободных позиций в один день (мультислотовая группа) — различимы
        только явным plannedLessonId из календаря.
    «Все позиции дня заняты» (мультислот, обе/все привязаны к фактам) — это
    НЕ None: это повторная отправка, и резолвер обязан вернуть занятую позицию,
    чтобы вызывающий отдал 409. Вернуть здесь None означало бы уйти на расчёт
    номера из прогресса учеников — ровно та механика, что дала три урока в
    инциденте ПГ215 (31.07.2026).
    """
    rows = list(
        PlannedLesson.objects
        .filter(group_id=group_id, seq__isnull=False, scheduled_date=lesson_date)
        .exclude(status=CANCELLED)
        .order_by('seq')
        .values('id', 'seq', 'lesson_number', 'scheduled_date', 'fact_lesson_id')
    )
    if len(rows) == 1:
        return rows[0]
    if not rows:
        return None
    free = [r for r in rows if r['fact_lesson_id'] is None]
    if len(free) == 1:
        return free[0]
    if not free:
        # Все позиции дня заняты — это повторная отправка, а не «плана нет».
        # Возвращаем занятую позицию, чтобы вызывающий отдал 409. Вернуть None
        # здесь означало бы уйти на расчёт номера из прогресса учеников —
        # ровно та механика, что дала три урока в инциденте ПГ215.
        return rows[-1]
    # 2+ свободных позиций в один день различимы только явным plannedLessonId.
    return None


_POSITION_FIELDS = ('id', 'seq', 'lesson_number', 'scheduled_date', 'fact_lesson_id')


def find_course_position_by_date_and_number(
    group_id: int, lesson_date, lesson_number,
) -> dict | None:
    """
    Позиция курса по дате И номеру — резолв для АДМИНСКОГО пути записи.

    Админ, в отличие от преподавателя, всегда указывает номер урока явно, и это
    единственный признак, которым он различает два занятия одного дня
    (мультислот). find_course_position_by_date для него не годится: при двух
    свободных позициях она возвращает None, и обе записи получили бы один и тот
    же ключ отправки slot:<group>:<date> — второе законное занятие дня упёрлось
    бы в конфликт.

    Занятая позиция возвращается наравне со свободной: занятая означает «урок за
    это занятие уже записан», и вызывающий обязан отказать. Именно так админский
    путь попадает в ОДНО пространство ключей с преподавательским — иначе то же
    занятие, записанное с двух сторон, даёт два платных урока (ключи pos:<id> и
    slot:<group>:<date> не конфликтуют по построению).

    None — позиции с таким номером на эту дату нет (группа без плана, запись вне
    плана, номер поправлен вручную): вызывающий уходит на ключ по дате.
    """
    return (
        PlannedLesson.objects
        .filter(
            group_id=group_id,
            scheduled_date=lesson_date,
            lesson_number=Decimal(str(lesson_number)),
            seq__isnull=False,
        )
        .exclude(status=CANCELLED)
        .values(*_POSITION_FIELDS)
        .first()
    )


def _course_position_qs(planned_lesson_id: int, group_id: int):
    """
    Позиция курса по id В ПРЕДЕЛАХ группы. group_id в фильтре — не украшение:
    без него преподаватель, подставив чужой plannedLessonId, записал бы урок на
    позицию другой группы. Не курсовые строки (seq NULL — доп.занятие, маркер
    отмены) и отменённые исключены: захватывать там нечего.
    """
    return (
        PlannedLesson.objects
        .filter(id=planned_lesson_id, group_id=group_id, seq__isnull=False)
        .exclude(status=CANCELLED)
    )


def get_course_position(planned_lesson_id: int, group_id: int) -> dict | None:
    """
    Прочитать позицию курса БЕЗ блокировки — предварительная проверка вне
    транзакции (apps.teacher_spa.services.submit_lesson резолвит номер урока до
    того, как record_lesson откроет atomic). select_for_update здесь нельзя:
    ATOMIC_REQUESTS в проекте не включён, вне atomic-блока он бросает
    TransactionManagementError.

    Решение по захвату принимает lock_course_position внутри транзакции.
    """
    return _course_position_qs(planned_lesson_id, group_id).values(*_POSITION_FIELDS).first()


def lock_course_position(planned_lesson_id: int, group_id: int) -> dict | None:
    """
    Залочить позицию курса под захват (select_for_update) и вернуть её поля.
    Вызывать ТОЛЬКО внутри transaction.atomic (record_lesson).

    Лок держится до конца транзакции вызывающего — этим сериализуются два
    параллельных захвата одной позиции: второй ждёт коммита первого и увидит
    уже проставленный fact_lesson_id. Без лока оба повтора прошли бы
    предварительную проверку до коммита друг друга и создали два урока.
    """
    return (
        _course_position_qs(planned_lesson_id, group_id)
        .select_for_update()
        .values(*_POSITION_FIELDS)
        .first()
    )


def attach_fact(planned_lesson_id: int, lesson_id: int) -> None:
    """
    Закрепить факт за позицией курса: fact_lesson_id + status='done' + плановая
    дата позиции = дата урока (инвариант спеки 2026-08-05 §2).

    Вызывается из record_lesson внутри той же транзакции, СРАЗУ после вставки
    урока и только по позиции, уже залоченной lock_course_position. Заменяет для
    этого факта «угадывающий» link_facts: позиция названа явно, номер урока взят
    из неё, поэтому сопоставлять по номеру нечего.

    Дата урока читается отдельным запросом: record_lesson её знает, но передавать
    через сигнатуру — лишняя связность, а запрос по первичному ключу тривиален.
    """
    fields = {
        'fact_lesson_id': lesson_id,
        'status': DONE,
        'updated_at': msk_now(),
    }
    lesson_date = (
        Lesson.objects.filter(id=lesson_id)
        .values_list('lesson_date', flat=True)
        .first()
    )
    if lesson_date is not None:
        # moved_from_date гасим по той же причине, что в sync_position_date:
        # метка описывала плановое движение, дата приходит от факта.
        fields['scheduled_date'] = lesson_date
        fields['moved_from_date'] = None

    PlannedLesson.objects.filter(id=planned_lesson_id).update(**fields)


def sync_position_date(lesson_id: int) -> bool:
    """
    Привести плановую дату курсовой позиции к дате её факта.

    Инвариант (спека 2026-08-05 §2): позиция, за которой закреплён проведённый
    урок, стоит на дате этого урока. Иначе «проведённое» занятие висит в
    календаре не в свой день, а операции планирования (permanent_change,
    cancel) кладут хвост поверх него — так сломались ПИ337, ПИ314, РИ283.

    Разовый перенос (moved_from_date) гасится: он описывал плановое движение
    занятия, а новая дата приходит от факта и к тому переносу отношения не
    имеет. Время позиции не трогаем — у факта времени нет.

    Не-курсовые занятия (доп.урок, сгорание) позиций не занимают → no-op.
    Идемпотентна: даты уже совпадают → ничего не пишет, возвращает False.
    True — позиция сдвинута.
    """
    lesson = (
        Lesson.objects.filter(id=lesson_id)
        .values('lesson_date', 'lesson_type')
        .first()
    )
    if lesson is None or lesson['lesson_type'] not in COURSE_LESSON_TYPES:
        return False

    with transaction.atomic():
        position = (
            PlannedLesson.objects.select_for_update()
            .filter(fact_lesson_id=lesson_id, seq__isnull=False)
            .first()
        )
        if position is None or position.scheduled_date == lesson['lesson_date']:
            return False
        position.scheduled_date = lesson['lesson_date']
        position.moved_from_date = None
        position.updated_at = msk_now()
        position.save(update_fields=[
            'scheduled_date', 'moved_from_date', 'updated_at',
        ])
        return True


def unlink_fact(lesson_id: int) -> None:
    """
    Отвязать плановую строку от удаляемого факта: fact_lesson_id=NULL,
    status → PENDING. Вызывается ДО удаления Lesson (внутри той же транзакции,
    из apps.lessons.repository.delete_lesson_full) — без этого шага
    fact_lesson_id зануляется каскадом (FK SET_NULL), но status остаётся
    'done', оставляя плановую строку зависшей «проведённой» без факта.

    Read-side (_planned_status) сам пересчитает overdue/pending по
    scheduled_date/scheduled_time при следующем чтении календаря — здесь
    достаточно вернуть status в PENDING, конкретное overdue/pending не разделяем.
    """
    PlannedLesson.objects.filter(fact_lesson_id=lesson_id).update(
        fact_lesson_id=None, status=PENDING,
    )


# ---------------------------------------------------------------------------
# Admin-API операции над planned_lessons (шаг 4). Все write — в transaction.atomic;
# планировщик дат — чистые функции planner.*; версионирование слота при
# «переносе навсегда» переиспользует groups.repository.apply_schedule_change.
# ---------------------------------------------------------------------------

# Статусы, которые операции переноса навсегда пересчитывают (непроведённые).
_MUTABLE_STATUSES = (PENDING, OVERDUE)


def _iso(d: datetime.date | None) -> str | None:
    return d.isoformat() if d else None


def _hhmm(t: datetime.time | None) -> str | None:
    return t.strftime('%H:%M') if t else None


def _plan_row_dict(r: dict, tnames: dict[int, str], now_msk: datetime.datetime) -> dict:
    """Сериализуемая плановая строка из .values()-словаря. is_extra = seq IS NULL.
    teacher_id/teacher_name — ЭФФЕКТИВНЫЙ преподаватель (замена на дату, если есть,
    иначе преподаватель контента) — чтобы admin-план показывал того, кто реально ведёт.

    Статус — ВЫЧИСЛЕННЫЙ (occurrences.planned_status), а не сырой из БД: в базе
    прошлое незаполненное занятие лежит как pending, и отдача сырого значения
    расходилась с /api/calendar — «Обзор» писал «Запланирован», а календарь
    группы не подсвечивал ячейку оранжевым."""
    ln = r['lesson_number']
    effective_teacher_id = r.get('substitute_teacher_id') or r['teacher_id']
    return {
        'id': r['id'],
        'seq': r['seq'],
        'lesson_number': float(ln) if ln is not None else None,
        'scheduled_date': _iso(r['scheduled_date']),
        'scheduled_time': _hhmm(r['scheduled_time']),
        'teacher_id': effective_teacher_id,
        'teacher_name': tnames.get(effective_teacher_id),
        'status': planned_status(r, now_msk),
        'fact_lesson_id': r['fact_lesson_id'],
        # Фактическая дата проведения (из связанного факта) — отдельно от плановой
        # scheduled_date; None если урок ещё не проведён.
        'fact_date': _iso(r.get('fact_date')),
        'record_url': r.get('record_url'),
        'moved_from_date': _iso(r['moved_from_date']),
        'is_extra': r['seq'] is None,
    }


def _plan_row_dict_obj(
    p: PlannedLesson, tnames: dict[int, str], now_msk: datetime.datetime,
) -> dict:
    """Сериализуемая плановая строка из ORM-объекта (после save/create)."""
    return _plan_row_dict({
        'id': p.id,
        'seq': p.seq,
        'lesson_number': p.lesson_number,
        'scheduled_date': p.scheduled_date,
        'scheduled_time': p.scheduled_time,
        'teacher_id': p.teacher_id,
        'substitute_teacher_id': p.substitute_teacher_id,
        'status': p.status,
        'fact_lesson_id': p.fact_lesson_id,
        'moved_from_date': p.moved_from_date,
    }, tnames, now_msk)


def _row_from_model(p: PlannedLesson) -> PlannedRow:
    """ORM-объект → PlannedRow (значения для planner)."""
    return PlannedRow(
        seq=p.seq,
        lesson_number=p.lesson_number,
        scheduled_date=p.scheduled_date,
        scheduled_time=p.scheduled_time,
        teacher_id=p.teacher_id,
        substitute_teacher_id=p.substitute_teacher_id,
        status=p.status,
        moved_from_date=p.moved_from_date,
    )


def plan_exists(group_id: int, *, active_only: bool = False) -> bool:
    """Есть ли у группы плановые строки (быстрый EXISTS).

    active_only=True: неактивную/отсутствующую группу считаем «план есть» (True),
    чтобы автоген её ПРОПУСКАЛ, не меняя поведение ручного /plan/generate (там
    active-фильтра нет). См. services.autogenerate_plan_on_setup."""
    if active_only and not Group.objects.filter(id=group_id, active=True).exists():
        return True
    return PlannedLesson.objects.filter(group_id=group_id).exists()


def get_plan(group_id: int) -> list[dict] | None:
    """
    Плановые строки группы (сериализуемые), упорядоченные по (дата, время).

    None → группы нет (view → 404). [] → группа есть, плана нет. Батч: два
    запроса (строки + справочник имён преподавателей), без N+1.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None
    rows = (
        PlannedLesson.objects
        .filter(group_id=group_id)
        .order_by('scheduled_date', 'scheduled_time')
        .values(
            'id', 'seq', 'lesson_number', 'scheduled_date', 'scheduled_time',
            'teacher_id', 'substitute_teacher_id', 'status', 'fact_lesson_id',
            'moved_from_date',
            fact_date=F('fact_lesson__lesson_date'),
            record_url=F('fact_lesson__record_url'),
        )
    )
    tnames = teacher_names()
    now = msk_now()   # один момент на всю выдачу — иначе строки судят по разным «сейчас»
    return [_plan_row_dict(r, tnames, now) for r in rows]


def get_plan_lesson(group_id: int, lesson_id: int) -> dict | None:
    """Одна плановая строка (group_id + id) с датой-объектом. None если нет.

    Возвращает «сырой» словарь (date/time — объекты) для внутренних операций
    (напр. вычисление from_date отмены по id занятия)."""
    return (
        PlannedLesson.objects
        .filter(group_id=group_id, id=lesson_id)
        .values('id', 'seq', 'scheduled_date', 'scheduled_time',
                'teacher_id', 'substitute_teacher_id', 'status')
        .first()
    )


def notification_context(lesson_id: int) -> dict | None:
    """Данные одной плановой строки для ТЕКСТА уведомления преподавателю.

    Отдельная выборка, а не переиспользование _plan_row_dict: последний отдаёт
    уже сериализованную строку (даты — ISO-строки, названий группы и направления
    в ней нет вовсе), а тексту сообщений нужны объекты date/time и человеческие
    названия. Один запрос по первичному ключу с join группы/направления — без
    N+1; вызывается только на разовых admin-операциях над одной строкой.

    Преподаватели отдаются оба (контента и замена на дату) — кого именно
    уведомлять, решает вызывающий: у переноса и отмены это эффективный
    преподаватель, у замены — обе стороны.
    """
    return (
        PlannedLesson.objects.filter(id=lesson_id).values(
            'id', 'seq', 'scheduled_date', 'scheduled_time',
            'teacher_id', 'substitute_teacher_id',
            # updated_at входит в ключ идемпотентности уведомления: без него
            # возврат занятия на дату, где оно уже было (A→B→A), дал бы тот же
            # ключ, что и первая операция, и сообщение молча не ушло бы.
            'updated_at',
            group_name=F('group__name'),
            direction_name=F('group__direction__name'),
        ).first()
    )


def reschedule_lesson(
    group_id: int,
    lesson_id: int,
    new_date: datetime.date,
    new_time: datetime.time | None,
    new_teacher_id: int | None,
) -> dict | None:
    """
    Разовый перенос одной строки (planner.reschedule): новые дата/время (+опц.
    преподаватель), прежняя дата → moved_from_date. seq/lesson_number сохраняются.

    None → строки нет (404). ValueError → перенос проведённого (status='done').
    """
    now = msk_now()
    with transaction.atomic():
        p = (
            PlannedLesson.objects
            .select_for_update()
            .filter(group_id=group_id, id=lesson_id)
            .first()
        )
        if p is None:
            return None
        if p.status == DONE:
            raise ValueError('Нельзя перенести проведённое занятие (status=done).')

        updated = planner.reschedule(
            _row_from_model(p),
            new_date=new_date,
            new_time=new_time,
            new_teacher_id=new_teacher_id,
        )
        date_changed = updated.scheduled_date != p.scheduled_date
        p.scheduled_date = updated.scheduled_date
        p.scheduled_time = updated.scheduled_time
        p.teacher_id = updated.teacher_id
        p.moved_from_date = updated.moved_from_date
        if date_changed:
            p.substitute_teacher_id = None  # замена — свойство даты, не едет
        p.updated_at = now
        p.save(update_fields=[
            'scheduled_date', 'scheduled_time', 'teacher', 'moved_from_date',
            'substitute_teacher', 'updated_at',
        ])

    return _plan_row_dict_obj(p, teacher_names(), msk_now())


def change_teacher(
    group_id: int, lesson_id: int, new_teacher_id: int,
) -> dict | None:
    """
    Разовая замена преподавателя на дату строки (planner.change_teacher): пишет
    substitute_teacher (замена на дату); teacher (контент) и дату/время не трогает
    и НЕ помечает строку перенесённой.

    None → строки нет (404). ValueError → строка проведена (status='done').
    """
    now = msk_now()
    with transaction.atomic():
        p = (
            PlannedLesson.objects
            .select_for_update()
            .filter(group_id=group_id, id=lesson_id)
            .first()
        )
        if p is None:
            return None
        updated = planner.change_teacher(_row_from_model(p), new_teacher_id=new_teacher_id)
        p.substitute_teacher_id = updated.substitute_teacher_id
        p.updated_at = now
        p.save(update_fields=['substitute_teacher', 'updated_at'])

    return _plan_row_dict_obj(p, teacher_names(), msk_now())


def change_teacher_permanent(
    group_id: int, *, from_seq: int, new_teacher_id: int,
) -> list[dict] | None:
    """
    Смена преподавателя навсегда (planner.change_teacher_tail): проставить teacher_id
    всем курсовым строкам seq>=from_seq в статусе pending/overdue. Даты/дни/слот НЕ
    трогаются (в отличие от permanent_change).

    None → группы нет (404). ValueError → нет курсовых строк с указанной позиции.
    Возвращает новый план (get_plan).

    Преподаватель хвоста становится преподавателем группы по умолчанию
    (groups.teacher_id) — иначе группа продолжает числиться за старым
    преподавателем, хотя все оставшиеся занятия ведёт новый.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None

    now = msk_now()
    with transaction.atomic():
        tail = list(
            PlannedLesson.objects
            .select_for_update()
            .filter(
                group_id=group_id, seq__isnull=False, seq__gte=from_seq,
                status__in=_MUTABLE_STATUSES,
            )
            .order_by('seq')
        )
        if not tail:
            raise ValueError('Нет курсовых строк для смены преподавателя с указанной позиции (seq).')

        by_seq = {p.seq: p for p in tail}
        changed = planner.change_teacher_tail(
            [_row_from_model(p) for p in tail],
            from_seq=from_seq,
            new_teacher_id=new_teacher_id,
        )
        to_update = []
        for cr in changed:
            p = by_seq[cr.seq]
            p.teacher_id = cr.teacher_id
            p.updated_at = now
            to_update.append(p)
        PlannedLesson.objects.bulk_update(to_update, ['teacher', 'updated_at'])
        Group.objects.filter(id=group_id).update(teacher_id=new_teacher_id)

    return get_plan(group_id)


def _parse_hhmm(value) -> datetime.time:
    """'HH:MM'/'HH:MM:SS' → time; datetime.time пропускаем как есть."""
    if isinstance(value, datetime.time):
        return value
    parts = [int(x) for x in value.split(':')]
    return datetime.time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)


def permanent_change(
    group_id: int,
    *,
    from_seq: int,
    effective_from: datetime.date,
    new_slots: list[dict],
    new_teacher_id: int | None = None,
) -> list[dict] | None:
    """
    «Изменить расписание» с позиции from_seq: ЕДИНЫЙ путь — чистая перегенерация
    хвоста от клиентской effective_from по целевому набору new_slots (1..N слотов).
    Никакого легаси-скаляра и никакой относительной перекладки (_relay_tail) —
    только regen, тот же принцип, что и у cancel_lesson/wipe_one_offs.

    Шаги (одна транзакция):
      (1) wipe_one_offs — сбросить разовые операции хвоста (переносы/замены/
          маркеры отмен) от effective_from / seq>=from_seq;
      (2) apply_schedule_change версионирует ВЕСЬ набор слотов от effective_from
          (закрывает старые открытые слоты днём раньше, открывает новые);
      (3) planner.generate разворачивает остаток курса (start_seq=from_seq,
          start_number = голова курса до from_seq) от effective_from по новому
          набору — тот же примитив, что строит план с нуля, поэтому выдаёт РОВНО
          ту же seq-последовательность, что уже лежит в БД хвостом;
      (4) результат мапится на существующие строки хвоста по seq и пишется
          bulk_update (даты/время/препод; moved_from_date/substitute_teacher_id
          сбрасываются явно — хвост уже переехал на новые даты);
      (5) lessons_per_week синхронизируется с числом new_slots; teacher группы —
          с new_teacher_id, если он передан.

    done и голова (seq<from_seq) не трогаются. Баланс/абонемент не затрагиваются.

    None → группы нет (404). ValueError → нет курсовых строк с указанной позиции
    (from_seq). Возвращает новый план (get_plan).
    """
    # Локальный импорт: избегаем циклической зависимости на уровне модуля
    # (groups.urls → scheduling.views → …). Переиспользуем версионирование слота.
    from apps.groups import repository as groups_repo

    if not Group.objects.filter(id=group_id).exists():
        return None

    now = msk_now()
    with transaction.atomic():
        g = Group.objects.filter(id=group_id).values(
            'lesson_duration_minutes', 'teacher_id',
            total_lessons=effective_total_lessons_expr()).first()
        step = _step_for(g['lesson_duration_minutes'])
        start_seq = from_seq
        start_number = (Decimal(from_seq) - 1) * step

        tail = list(
            PlannedLesson.objects
            .select_for_update()
            .filter(
                group_id=group_id, seq__isnull=False, seq__gte=from_seq,
                status__in=_MUTABLE_STATUSES,
            )
            .order_by('seq')
        )
        if not tail:
            raise ValueError('Нет курсовых строк для переноса с указанной позиции (seq).')

        # (1) чистый лист хвоста — сбросить разовые операции.
        wipe_one_offs(group_id, date_from=effective_from, from_seq=from_seq)

        # (2) версионируем ВЕСЬ набор слотов от новой (клиентской) даты.
        target = [
            {'day_of_week': s['day_of_week'],
             'start_time': _parse_hhmm(s['start_time']).strftime('%H:%M')}
            for s in new_slots
        ]
        groups_repo.apply_schedule_change(group_id, effective_from, target)

        # (3) генерируем остаток курса от effective_from по новому набору слотов.
        open_slots = [s for s in slots_by_group([group_id]).get(group_id, [])
                      if s.effective_to is None]
        teacher_for_tail = new_teacher_id if new_teacher_id is not None else g['teacher_id']
        rows = planner.generate(
            start_date=effective_from, slots=open_slots,
            total_lessons=g['total_lessons'], duration_minutes=g['lesson_duration_minutes'],
            default_teacher_id=teacher_for_tail, start_seq=start_seq, start_number=start_number)

        # (4) мапим сгенерированные строки на реальный хвост по seq (та же
        # seq-последовательность — generate(start_seq=...) детерминирован).
        by_seq = {p.seq: p for p in tail}
        to_update = []
        for r in rows:
            p = by_seq.get(r.seq)
            if p is None:
                continue
            p.scheduled_date = r.scheduled_date
            p.scheduled_time = r.scheduled_time
            p.teacher_id = teacher_for_tail
            p.moved_from_date = None
            p.substitute_teacher_id = None
            p.updated_at = now
            to_update.append(p)
        PlannedLesson.objects.bulk_update(to_update, [
            'scheduled_date', 'scheduled_time', 'teacher', 'moved_from_date',
            'substitute_teacher', 'updated_at'])

        # (5) метаданные группы в синхроне.
        Group.objects.filter(id=group_id).update(lessons_per_week=len(target))
        if new_teacher_id is not None:
            Group.objects.filter(id=group_id).update(teacher_id=new_teacher_id)

    return get_plan(group_id)


def wipe_one_offs(
    group_id: int,
    *,
    date_from: datetime.date,
    date_to: datetime.date | None = None,
    from_seq: int | None = None,
) -> None:
    """Сбросить разовые операции в диапазоне (для чистой перегенерации хвоста):
      - удалить маркеры отмен (seq IS NULL, status='cancelled');
      - обнулить moved_from_date (разовые переносы);
      - снять substitute_teacher (разовые замены).
    Диапазон — по дате [date_from, date_to] и/или по позиции seq>=from_seq у
    курсовых строк. done не трогаем. Вызывать внутри открытой транзакции."""
    now = msk_now()
    markers = PlannedLesson.objects.filter(
        group_id=group_id, seq__isnull=True, status=CANCELLED,
        scheduled_date__gte=date_from)
    if date_to is not None:
        markers = markers.filter(scheduled_date__lte=date_to)
    markers.delete()

    course = PlannedLesson.objects.filter(
        group_id=group_id, seq__isnull=False, status__in=_MUTABLE_STATUSES)
    if from_seq is not None:
        course = course.filter(seq__gte=from_seq)
    else:
        course = course.filter(scheduled_date__gte=date_from)
        if date_to is not None:
            course = course.filter(scheduled_date__lte=date_to)
    course.update(moved_from_date=None, substitute_teacher_id=None, updated_at=now)


def preview_affected(
    group_id: int,
    *,
    date_from: datetime.date,
    date_to: datetime.date | None = None,
    from_seq: int | None = None,
) -> list[dict]:
    """Read-only: разовые операции, попадающие под перегенерацию (для превью-модалки).
    kind ∈ {reschedule, substitution, cancellation}. Ничего не пишет."""
    out: list[dict] = []
    markers = PlannedLesson.objects.filter(
        group_id=group_id, seq__isnull=True, status=CANCELLED, scheduled_date__gte=date_from)
    if date_to is not None:
        markers = markers.filter(scheduled_date__lte=date_to)
    for m in markers.values('scheduled_date', 'scheduled_time'):
        out.append({'kind': 'cancellation', 'date': m['scheduled_date'],
                    'time': m['scheduled_time']})

    course = PlannedLesson.objects.filter(
        group_id=group_id, seq__isnull=False, status__in=_MUTABLE_STATUSES)
    if from_seq is not None:
        course = course.filter(seq__gte=from_seq)
    else:
        course = course.filter(scheduled_date__gte=date_from)
        if date_to is not None:
            course = course.filter(scheduled_date__lte=date_to)
    for r in course.values('seq', 'scheduled_date', 'moved_from_date', 'substitute_teacher_id'):
        if r['moved_from_date'] is not None:
            out.append({'kind': 'reschedule', 'seq': r['seq'], 'date': r['scheduled_date'],
                        'from_date': r['moved_from_date']})
        if r['substitute_teacher_id'] is not None:
            out.append({'kind': 'substitution', 'seq': r['seq'], 'date': r['scheduled_date']})
    return out


def _next_slot_after(
    *,
    after_date: datetime.date,
    occupied: frozenset[datetime.date],
    duration_minutes: int,
    open_slots: list[Slot],
) -> tuple[datetime.date, datetime.time] | None:
    """Ближайшая (дата, время) слота СТРОГО после after_date, не входящая в
    occupied. Время берётся из фактически найденного слота (мультислотовая
    группа может иметь разное время по дням недели) — НЕ первого попавшегося.

    None — нет открытого слота / не нашли (нельзя развернуть каденцию). Горизонт
    поиска — запас в неделях сверх числа уже занятых дат (защита от вечного
    цикла, если occupied аномально плотный)."""
    if not open_slots:
        return None
    start = after_date + datetime.timedelta(days=1)
    horizon = start + datetime.timedelta(weeks=len(occupied) + 4)
    occ = _walk(start, open_slots, _step_for(duration_minutes), None, horizon, skip_dates=occupied)
    return (occ[0].date, occ[0].time) if occ else None


def _renumber_persist(
    pending: list[PlannedLesson],
    *,
    start_seq: int,
    start_number: Decimal,
    step: Decimal,
    now: datetime.datetime,
) -> None:
    """Двухфазная запись seq/lesson_number по возрастанию (scheduled_date,
    scheduled_time) (обход UniqueConstraint(group, seq)): сначала временные
    отрицательные seq, потом финальные — вычисленные через planner.renumber_by_date
    (переиспользуем чистую функцию, не дублируем сортировку/нумерацию)."""
    if not pending:
        return
    # Фаза 1: увести в отрицательный диапазон, чтобы не столкнуться с целевыми seq.
    for i, p in enumerate(pending, start=1):
        p.seq = -i
    PlannedLesson.objects.bulk_update(pending, ['seq'])

    # Фаза 2: финальные значения — считаем через renumber_by_date на пред-
    # отсортированном списке, затем сопоставляем позиционно (тот же порядок
    # сортировки внутри функции — сортировка детерминирована и одинакова).
    ordered = sorted(pending, key=lambda p: (p.scheduled_date, p.scheduled_time))
    rows = [_row_from_model(p) for p in ordered]
    renumbered = planner.renumber_by_date(
        rows, start_seq=start_seq, start_number=start_number, step=step)
    for p, r in zip(ordered, renumbered):
        p.seq = r.seq
        p.lesson_number = r.lesson_number
        p.updated_at = now
    PlannedLesson.objects.bulk_update(ordered, ['seq', 'lesson_number', 'updated_at'])


def cancel_lesson(
    group_id: int,
    from_date: datetime.date,
    *,
    marker_time: datetime.time,
    marker_teacher_id: int | None,
    lesson_id: int,
) -> list[dict]:
    """
    Отмена «в конец курса»: (1) на исходную дату вставляется НЕ-курсовой маркер
    status='cancelled' (seq=NULL) — календарь показывает «отменённое» занятие
    зачёркнутым, несёт время/преподавателя отменённого занятия; (2) сама
    отменённая строка (lesson_id) переезжает на ближайший свободный слот СТРОГО
    после текущей последней курсовой даты группы — становится новым последним
    занятием курса; замена (substitute_teacher) и moved_from_date сбрасываются
    (разовая замена/перенос — свойство даты, не едет с содержимым); (3) ВСЕ
    оставшиеся курсовые pending/overdue строки перенумеровываются подряд по
    возрастанию (scheduled_date, scheduled_time), продолжая нумерацию от
    последней 'done' строки (или с 1/0, если done нет) — см. _renumber_persist
    (переиспользует planner.renumber_by_date).

    Проведённые (done) строки не трогаются вовсе. Если свободного слота нет
    (нет открытого слота у группы) — шаг (2) пропускается: строка остаётся на
    месте, перенумерация всё равно проходит (тот же fallback, что раньше у
    relay: «нельзя развернуть каденцию»). Абонемент/баланс не трогаем.

    Возвращает новый план (get_plan).
    """
    now = msk_now()
    with transaction.atomic():
        g = Group.objects.filter(id=group_id).values('lesson_duration_minutes').first()
        if g is None:
            return get_plan(group_id)
        step = _step_for(g['lesson_duration_minutes'])
        open_slots = [
            s for s in slots_by_group([group_id]).get(group_id, [])
            if s.effective_to is None
        ]

        # Курсовые строки группы лочатся ПЕРВЫМИ и в порядке seq — тем же, что у
        # permanent_change/resize_plan/resync_plan_facts. Прежде здесь сначала
        # лочилась одна строка target по id, а весь курс — после и без order_by:
        # порядок захвата получался обратный, и «отменить занятие» могло встать
        # в дедлок с «изменить расписание»/«починить план» на той же группе.
        # target берём из уже залоченного набора — второй лок не нужен.
        course_rows = list(
            PlannedLesson.objects
            .select_for_update()
            .filter(group_id=group_id, seq__isnull=False)
            .order_by('seq')
        )
        target = next(
            (p for p in course_rows
             if p.id == lesson_id and p.status in _MUTABLE_STATUSES),
            None,
        )
        if target is None:
            raise ValueError('Отменить можно только активную курсовую строку.')

        # (1) Маркер отмены на исходной дате (seq=NULL): календарь показывает
        # зачёркнутое занятие. Несёт время/преподавателя отменённого занятия.
        PlannedLesson.objects.create(
            group_id=group_id, seq=None, lesson_number=None,
            scheduled_date=from_date, scheduled_time=marker_time,
            teacher_id=marker_teacher_id, status=CANCELLED,
            created_at=now, updated_at=now,
        )

        # (2) Переносим саму отменённую строку в конец курса — на первый
        # свободный слот после текущей последней курсовой даты.
        # course_rows уже залочены выше — повторный select_for_update не нужен.
        last_date = max(p.scheduled_date for p in course_rows)
        occupied = frozenset(
            PlannedLesson.objects.filter(group_id=group_id)
            .values_list('scheduled_date', flat=True)
        )
        next_slot = _next_slot_after(
            after_date=last_date, occupied=occupied,
            duration_minutes=g['lesson_duration_minutes'], open_slots=open_slots,
        )
        if next_slot is not None:
            new_date, new_time = next_slot
            target.scheduled_date = new_date
            target.scheduled_time = new_time
            target.substitute_teacher_id = None  # замена — свойство даты, не едет
            target.moved_from_date = None
            target.updated_at = now
            target.save(update_fields=[
                'scheduled_date', 'scheduled_time', 'substitute_teacher',
                'moved_from_date', 'updated_at',
            ])

        # (3) Перенумеровываем pending/overdue курсовые строки по дате,
        # продолжая от последней 'done' (или с 1/0, если done нет).
        last_done = (
            PlannedLesson.objects
            .filter(group_id=group_id, status=DONE, seq__isnull=False)
            .order_by('-seq')
            .values('seq', 'lesson_number')
            .first()
        )
        start_seq = (last_done['seq'] + 1) if last_done else 1
        start_number = last_done['lesson_number'] if last_done else Decimal('0')
        pending = list(
            PlannedLesson.objects
            .select_for_update()
            .filter(group_id=group_id, seq__isnull=False, status__in=_MUTABLE_STATUSES)
            .order_by('scheduled_date', 'scheduled_time')
        )
        _renumber_persist(
            pending, start_seq=start_seq, start_number=start_number, step=step, now=now)

    return get_plan(group_id)


def generate_for_group(group_id: int) -> dict | None:
    """
    Идемпотентная генерация плана группы (обёртка над planner.generate +
    persist_plan для эндпоинта generate).

    None → группы нет (404). Иначе {'written': N, 'reason': str|None, 'plan': [...]}:
    reason (no_start_date / no_total_lessons / no_slots) — data-quality сигнал,
    когда план построить нельзя (план остаётся как есть, written=0).
    """
    g = (
        Group.objects
        .filter(id=group_id)
        .values(
            'id', 'lesson_duration_minutes', 'group_start_date', 'teacher_id',
            'lesson_number_offset',
            total_lessons=effective_total_lessons_expr(),
        )
        .first()
    )
    if g is None:
        return None

    reason = None
    g_slots: list[Slot] = []
    if g['group_start_date'] is None:
        reason = 'no_start_date'
    elif g['total_lessons'] is None:
        reason = 'no_total_lessons'
    else:
        g_slots = slots_by_group([group_id]).get(group_id, [])
        if not g_slots:
            reason = 'no_slots'

    written = 0
    if reason is None:
        offset = g['lesson_number_offset'] or Decimal('0')
        # start_seq остаётся 1: у generate_for_group план строится с нуля (это
        # НЕ продолжение хвоста существующего плана, как в apply_schedule_change
        # выше, где start_seq выравнивает новые строки с уже лежащими в БД seq) —
        # seq всегда 1-based позиция в ЭТОМ плане. Офсет сдвигает только
        # lesson_number (через start_number), т.е. с какого урока курса реально
        # начинается план (перевод ученика с накопленным прогрессом).
        rows = planner.generate(
            start_date=g['group_start_date'],
            slots=g_slots,
            total_lessons=g['total_lessons'],
            duration_minutes=g['lesson_duration_minutes'],
            default_teacher_id=g['teacher_id'],
            start_number=offset,
        )
        # create_only: повторный generate не затирает ручные операции над планом —
        # только досоздаёт недостающие seq (напр. при увеличении длины курса).
        written = persist_plan(group_id, rows, create_only=True)  # атомарен сам по себе

    return {'written': written, 'reason': reason, 'plan': get_plan(group_id)}


# ---------------------------------------------------------------------------
# Rebuild-from-facts (новый дефолт бэкфилла): прошлое = факты (коллапс даты в
# фактическую) + overdue-прошлое (позиции без факта на исторических датах) +
# будущее от текущего момента по актуальному открытому слоту. РАЗРУШИТЕЛЬНО
# (reset_plan): перезаписывает ручные операции будущего — для перелива ожидаемо.
# See docs/lesson-scheduling.md. Композиция чистых функций planner — в них же тесты.
# ---------------------------------------------------------------------------

def facts_by_group(group_ids: list[int]) -> dict[int, list[Fact]]:
    """Проведённые уроки (факты) по группам — планировщику как list[Fact].
    Батч (один запрос), упорядочено по (lesson_date, id) для позиционной линковки."""
    result: dict[int, list[Fact]] = {}
    if not group_ids:
        return result
    rows = (
        Lesson.objects
        .filter(group_id__in=group_ids)
        .order_by('lesson_date', 'id')
        .values('group_id', 'id', 'lesson_date', 'teacher_id')
    )
    for r in rows:
        result.setdefault(r['group_id'], []).append(Fact(
            lesson_date=r['lesson_date'],
            teacher_id=r['teacher_id'],
            fact_lesson_id=r['id'],
        ))
    return result


def _build_rebuild_rows(
    g: dict, slots: list[Slot], facts: list[Fact],
) -> tuple[list[PlannedRow] | None, str | None]:
    """Строки rebuild через planner.generate_from_facts: прошлое=факты, будущее от
    последнего факта по текущему слоту.

    (rows, reason). rows=None при блокирующей причине (нельзя построить план):
    no_start_date / no_total_lessons / no_slots. reason='no_open_slots' — прошлое
    (факты) построено, но будущее не развернуть (нет открытого слота)."""
    if g['group_start_date'] is None:
        return None, 'no_start_date'
    if g['total_lessons'] is None:
        return None, 'no_total_lessons'
    if not slots:
        return None, 'no_slots'
    open_slots = [s for s in slots if s.effective_to is None]
    rows = planner.generate_from_facts(
        facts=facts,
        current_slots=open_slots,
        total_lessons=g['total_lessons'],
        duration_minutes=g['lesson_duration_minutes'],
        default_teacher_id=g['teacher_id'],
        group_start_date=g['group_start_date'],
    )
    return rows, (None if open_slots else 'no_open_slots')


def rebuild_group_plan(
    group_id: int, g: dict, slots: list[Slot], facts: list[Fact],
    *, dry_run: bool = False,
) -> dict:
    """Пересобрать план одной группы (reset + generate_from_facts + bulk_create),
    батч-данные передаёт вызывающий (команда) — без N+1. РАЗРУШИТЕЛЬНО.
    {'written', 'reason'}. Результат today-независим (будущее от последнего факта).

    dry_run=True: ничего не пишет, 'written' = сколько строк было бы записано."""
    rows, reason = _build_rebuild_rows(g, slots, facts)
    if rows is None:
        return {'written': 0, 'reason': reason}
    if dry_run:
        return {'written': len(rows), 'reason': reason}
    now = msk_now()
    with transaction.atomic():
        reset_plan(group_id)
        PlannedLesson.objects.bulk_create([
            PlannedLesson(
                group_id=group_id,
                seq=r.seq,
                lesson_number=r.lesson_number,
                scheduled_date=r.scheduled_date,
                scheduled_time=r.scheduled_time,
                teacher_id=r.teacher_id,
                status=r.status,
                fact_lesson_id=r.fact_lesson_id,
                created_at=now,
                updated_at=now,
            )
            for r in rows
        ])
    return {'written': len(rows), 'reason': reason}


def rebuild_from_facts(group_id: int) -> dict | None:
    """Single-group обёртка rebuild_group_plan (сама читает группу/слоты/факты).

    None → группы нет. Результат today-независим (будущее считается от даты
    последнего факта, не от «сегодня»). Команда бэкфилла использует батч-путь
    (active_groups + slots_by_group + facts_by_group + rebuild_group_plan)."""
    g = (
        Group.objects
        .filter(id=group_id)
        .values(
            'id', 'lesson_duration_minutes', 'group_start_date', 'teacher_id',
            total_lessons=effective_total_lessons_expr(),
        )
        .first()
    )
    if g is None:
        return None
    slots = slots_by_group([group_id]).get(group_id, [])
    facts = facts_by_group([group_id]).get(group_id, [])
    return rebuild_group_plan(group_id, g, slots, facts)


# ---------------------------------------------------------------------------
# Починка плана: «номер факта = номер позиции» (resync).
#
# Прошлое чинится ровно одним правилом: каждое проведённое занятие вешается на
# позицию СВОЕГО номера, плановая дата позиции становится датой занятия,
# оставшиеся без факта позиции освобождаются и уходят вперёд. Ни уроков, ни
# позиций не создаём и не удаляем, номера в самих уроках не трогаем.
#
# Разделение как у _build_rebuild_rows/rebuild_group_plan: чистая функция над уже
# прочитанными списками (_plan_resync_changes) + два пути к ней — read-only
# предпросмотр (plan_resync_diff) и запись под локами (resync_plan_facts).
# ---------------------------------------------------------------------------

# Проверки здоровья (health.CHECKS), при которых чинить нельзя: пока занятие не
# привязано НИ К ОДНОЙ позиции или два занятия стоят на одной дате, «правильная»
# раскладка не определена. Это граница слоя 3 из спеки, не предусловие команды.
RESYNC_BLOCKING_CHECKS = ('fact_without_position', 'duplicate_dates')

# Статусы позиций, которые починка вправе трогать. Перенесённые (moved) и
# отменённые курсовые строки исключены по той же причине, что и в
# _MUTABLE_STATUSES у permanent_change: их состояние — результат отдельного
# решения человека, и молча возвращать их в pending/done нельзя.
_RESYNC_STATUSES = (PENDING, OVERDUE, DONE)

_RESYNC_POSITION_FIELDS = (
    'id', 'seq', 'lesson_number', 'scheduled_date', 'fact_lesson_id', 'status',
)


def _number_key(value):
    """Ключ сопоставления по номеру урока.

    Половинный шаг (45 мин) даёт номера 0.5/1.0/1.5 — сравнивать их как строки
    нельзя ('12.0' != '12'), поэтому нормализуем к Decimal. None (урок без
    номера) ключом быть не может: такому факту позицию не найти."""
    if value is None:
        return None
    return Decimal(str(value))


def _resync_position_dicts(group_id: int, *, lock: bool = False) -> list[dict]:
    """ВСЕ строки плана группы (курсовые и маркеры), упорядоченные по seq.

    lock=True — select_for_update тем же порядком (seq), что в permanent_change
    и resize_plan: единый порядок захвата строк защищает от дедлоков."""
    qs = PlannedLesson.objects.filter(group_id=group_id)
    if lock:
        qs = qs.select_for_update()
    return list(qs.order_by('seq').values(*_RESYNC_POSITION_FIELDS))


def _course_facts(group_id: int) -> list[dict]:
    """Проведённые занятия КУРСА группы (extra/burned позиций не занимают)."""
    return list(
        Lesson.objects
        .filter(group_id=group_id, lesson_type__in=COURSE_LESSON_TYPES)
        .order_by('lesson_number', 'lesson_date', 'id')
        .values('id', 'lesson_date', 'lesson_number')
    )


def blockers_from_findings(findings: dict) -> list[str]:
    """Какие из проверок слоя 3 сработали в готовом отчёте health.check_group.

    ЕДИНСТВЕННОЕ место, где записано правило «что блокирует автопочинку».
    services.plan_health зовёт её же на уже прочитанном отчёте, чтобы правило не
    оказалось продублировано в двух файлах и не разошлось при добавлении
    восьмой проверки.
    """
    return [key for key in RESYNC_BLOCKING_CHECKS if findings.get(key)]


def _health_blockers(group_id: int) -> list[str]:
    """Сработавшие проверки слоя 3 (health — только чтение, логики починки в нём нет)."""
    report = health.check_group(group_id)
    if report is None:
        return []
    return blockers_from_findings(report['findings'])


def _plan_resync_changes(facts: list[dict], positions: list[dict]) -> dict:
    """
    ЧИСТАЯ функция: что изменится, если разложить факты по позициям своих номеров.

    facts — курсовые занятия группы (_course_facts), positions — ВСЕ строки плана
    (_resync_position_dicts). Ничего не читает и не пишет: оба пути (предпросмотр
    без локов и запись под локом) считают дифф ровно этим кодом.

    Возвращает:
      changes  — [{'position_id', 'lesson_number', 'from': {...}, 'to': {...}}],
                 только реально меняющиеся позиции, по возрастанию seq;
      freed    — позиции, которые останутся без факта (уйдут вперёд как ближайшие);
      orphan_facts — занятия, из-за которых чинить нельзя (см. reason);
      duplicate_numbers — в плане есть позиции с одинаковым номером (чинить нельзя).

    Даты — ISO-строки, номера — float: структура уходит и в HTTP-ответ, и в
    сверку с expected, поэтому сериализуемая форма нужна обеим сторонам.
    """
    course = [p for p in positions if p['seq'] is not None]
    touchable = [p for p in course if p['status'] in _RESYNC_STATUSES]
    touchable_ids = {p['id'] for p in touchable}

    # Факт, сидящий на позиции, которую починка трогать не вправе (moved/cancelled
    # курсовая строка, маркер): снять его мы не можем, а его номерная позиция
    # ждёт именно его — операция не определена.
    locked_fact_ids = {
        p['fact_lesson_id'] for p in positions
        if p['fact_lesson_id'] is not None and p['id'] not in touchable_ids
    }

    by_number: dict = {}
    duplicate_numbers = False
    for p in touchable:
        key = _number_key(p['lesson_number'])
        if key in by_number:
            duplicate_numbers = True
            continue
        by_number[key] = p

    orphan_facts: list[dict] = []
    wanted: dict[int, dict] = {}      # position_id -> факт
    claimed_by: dict[int, int] = {}   # position_id -> id уже занявшего факта
    for f in facts:
        key = _number_key(f['lesson_number'])
        target = by_number.get(key) if key is not None else None
        if f['id'] in locked_fact_ids:
            reason = 'locked_position'
        elif target is None:
            reason = 'no_position'
        elif target['id'] in claimed_by:
            # Два занятия с одним номером — раскладка неоднозначна.
            reason = 'duplicate_fact_number'
        else:
            claimed_by[target['id']] = f['id']
            wanted[target['id']] = f
            continue
        orphan_facts.append({
            'lesson_id': f['id'],
            'lesson_number': float(f['lesson_number']) if f['lesson_number'] is not None else None,
            'lesson_date': _iso(f['lesson_date']),
            'reason': reason,
        })

    changes: list[dict] = []
    freed: list[dict] = []
    for p in touchable:
        target = wanted.get(p['id'])
        new_fact_id = target['id'] if target else None
        new_date = target['lesson_date'] if target else p['scheduled_date']
        if p['fact_lesson_id'] == new_fact_id and p['scheduled_date'] == new_date:
            continue
        number = float(p['lesson_number']) if p['lesson_number'] is not None else None
        changes.append({
            'position_id': p['id'],
            'lesson_number': number,
            'from': {
                'fact_lesson_id': p['fact_lesson_id'],
                'scheduled_date': _iso(p['scheduled_date']),
            },
            'to': {
                'fact_lesson_id': new_fact_id,
                'scheduled_date': _iso(new_date),
            },
        })
        if new_fact_id is None:
            freed.append({
                'position_id': p['id'],
                'lesson_number': number,
                'scheduled_date': _iso(new_date),
            })

    return {
        'changes': changes,
        'freed': freed,
        'orphan_facts': orphan_facts,
        'duplicate_numbers': duplicate_numbers,
    }


def _blocked_result(blocked_by: list[str], orphan_facts: list[dict]) -> dict:
    """Ответ предпросмотра, когда чинить нельзя.

    changes=None НАМЕРЕННО: показывать план починки, который сервер применять
    откажется, нельзя — интерфейс предложил бы кнопку, дающую 409."""
    return {
        'blocked_by': list(blocked_by),
        'changes': None,
        'orphan_facts': list(orphan_facts),
        'freed': [],
    }


def plan_resync_diff(group_id: int, *, blocked_by: list[str] | None = None) -> dict | None:
    """
    Предпросмотр починки — ЧТЕНИЕ без локов и без записи. None → группы нет.

    blocked_by передаёт вызывающий, если проверки слоя 3 уже посчитаны (services
    берёт их из того же health.check_group, который отдаёт findings) — иначе
    считаем сами. Это единственная причина параметра: не гонять health дважды
    на один GET.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None
    if blocked_by is None:
        blocked_by = _health_blockers(group_id)
    if blocked_by:
        return _blocked_result(blocked_by, [])

    diff = _plan_resync_changes(_course_facts(group_id), _resync_position_dicts(group_id))
    if diff['duplicate_numbers']:
        return _blocked_result(['duplicate_position_numbers'], diff['orphan_facts'])
    if diff['orphan_facts']:
        # Причина та же, что у blocked_by: применять такой план сервер откажется.
        return _blocked_result([], diff['orphan_facts'])
    return {
        'blocked_by': [],
        'changes': diff['changes'],
        'orphan_facts': [],
        'freed': diff['freed'],
    }


def _expected_map(changes: list[dict]) -> dict:
    """Дифф → {position_id: (fact_lesson_id, 'YYYY-MM-DD')} для сверки с expected."""
    return {c['position_id']: (c['to']['fact_lesson_id'], c['to']['scheduled_date'])
            for c in changes}


def _normalize_expected(expected) -> dict:
    """Клиентские тройки [position_id, fact_lesson_id|null, 'YYYY-MM-DD'] → тот же вид.
    Форма уже проверена сериализатором — здесь только приведение типов."""
    return {
        int(pid): (None if fact_id is None else int(fact_id), str(date_str))
        for pid, fact_id, date_str in expected
    }


def resync_plan_facts(group_id: int, *, expected=None) -> dict | None:
    """
    Применить починку. None → группы нет (view → 404).

    Транзакция:
      (1) ПЕРВЫМ действием — лок всего плана группы (select_for_update, порядок
          seq: тот же, что в permanent_change/resize_plan → нет дедлоков);
      (2) факты читаются ПОСЛЕ локов и БЕЗ select_for_update: record_lesson лочит
          позицию, а потом пишет урок — взяв здесь лок на lessons, мы получили бы
          обратный порядок блокировок и настоящий дедлок;
      (3) граница слоя 3 и предусловия перепроверяются ВНУТРИ транзакции (между
          предпросмотром и применением состояние могло измениться);
      (4) сверка с expected — до записи; расхождение → PlanResyncBlocked (409).
          ИСКЛЮЧЕНИЕ: пустой дифф не сверяется вовсе, даже если клиент прислал
          непустой expected. Это осознанный перекос в пользу идемпотентности:
          повторный клик (в т.ч. двойной, когда второй запрос несёт тот же
          expected, а чинить уже нечего) обязан давать 200 applied=0, а не
          конфликт — иначе пользователь получает ошибку на успешно выполненной
          операции. Расхождение «клиент видел изменения, а их уже нет» означает,
          что кто-то починил группу раньше, и цель клиента всё равно достигнута;
      (5) запись в ДВА прохода: сначала снимаются ВСЕ меняющиеся привязки (включая
          освобождаемые), потом ставятся новые. fact_lesson уникален — при
          перестановке двух фактов местами одношаговая запись упёрлась бы в 23505.

    expected=None — сверки нет (путь команды: там нет предпросмотра-рукопожатия).
    HTTP всегда передаёт список.

    Возвращает {'applied': N, 'freed_count': M, 'plan': [...]}; «чинить нечего» —
    applied=0, а не конфликт.
    """
    if not Group.objects.filter(id=group_id).exists():
        return None

    now = msk_now()
    with transaction.atomic():
        positions = _resync_position_dicts(group_id, lock=True)
        facts = _course_facts(group_id)

        blocked_by = _health_blockers(group_id)
        diff = _plan_resync_changes(facts, positions)
        if diff['duplicate_numbers']:
            blocked_by = [*blocked_by, 'duplicate_position_numbers']
        if blocked_by or diff['orphan_facts']:
            raise PlanResyncBlocked(
                'План группы нельзя чинить автоматически — разберитесь вручную.',
                blocked_by=blocked_by, orphan_facts=diff['orphan_facts'],
            )

        changes = diff['changes']
        if changes:
            if expected is not None and _expected_map(changes) != _normalize_expected(expected):
                raise PlanResyncBlocked('Состояние изменилось, обновите предпросмотр.')

            # (5a) снять ВСЕ меняющиеся привязки — включая освобождаемые позиции.
            unbind_ids = [c['position_id'] for c in changes
                          if c['from']['fact_lesson_id'] is not None]
            if unbind_ids:
                PlannedLesson.objects.filter(id__in=unbind_ids).update(
                    fact_lesson_id=None, status=PENDING, updated_at=now)

            # (5b) поставить новые.
            for c in changes:
                new_fact_id = c['to']['fact_lesson_id']
                fields = {
                    'fact_lesson_id': new_fact_id,
                    'status': DONE if new_fact_id else PENDING,
                    'scheduled_date': datetime.date.fromisoformat(c['to']['scheduled_date']),
                    'updated_at': now,
                }
                if new_fact_id is not None:
                    # Дата пришла от факта — метка разового переноса к ней
                    # отношения не имеет (тот же приём, что в attach_fact,
                    # sync_position_date, link_facts, relink_fact).
                    fields['moved_from_date'] = None
                PlannedLesson.objects.filter(id=c['position_id']).update(**fields)

            # Страховка по ЗАТРОНУТЫМ позициям (не по всей группе: чужие
            # moved/cancelled строки мы не чинили и валить из-за них откат нельзя).
            touched = [c['position_id'] for c in changes]
            mismatch = (
                PlannedLesson.objects
                .filter(id__in=touched, fact_lesson__isnull=False)
                .exclude(lesson_number=F('fact_lesson__lesson_number'))
                .count()
            )
            if mismatch:  # pragma: no cover — защита от логической ошибки выше
                raise PlanResyncBlocked(
                    'Проверка после записи не сошлась — изменения отменены.')

    return {
        'applied': len(diff['changes']),
        # freed_count, а не freed: в GET /plan/health это СПИСОК позиций, и
        # одно имя под две формы — готовая ловушка для следующей правки.
        'freed_count': len(diff['freed']),
        'plan': get_plan(group_id),
    }


# ---------------------------------------------------------------------------
# Подгонка плана под изменение длины курса группы (groups.lessons_total).
# ---------------------------------------------------------------------------

def resize_plan(group_id: int) -> int:
    """
    Подогнать план группы под её текущую длину курса (после правки lessons_total).

    Хвост за границей новой длины удаляется — но ТОЛЬКО pending/overdue строки;
    done/moved защищены (PlanHasRecordedLessons). Недостающие позиции дописываются
    от даты последнего планового занятия по текущим открытым слотам, тем же
    примитивом planner.generate, что строит план с нуля, — поэтому seq/номера
    продолжаются непрерывно. Маркеры отмен (seq IS NULL) не трогаются.

    Единицы: длина курса — в УРОКАХ, план — в занятиях. Число занятий =
    уроки / шаг (45 мин → шаг 0.5 → вдвое больше занятий), поэтому граница
    считается через step, а не по числу уроков напрямую.

    Возвращает число изменённых строк (удалённых + созданных). No-op, если длина
    курса не задана нигде, нет открытых слотов или дописывать некуда.
    """
    g = (
        Group.objects
        .filter(id=group_id)
        .values(
            'id', 'lesson_duration_minutes', 'teacher_id', 'group_start_date',
            total_lessons=effective_total_lessons_expr(),
        )
        .first()
    )
    if g is None or g['total_lessons'] is None:
        return 0

    step = _step_for(g['lesson_duration_minutes'])
    target_count = int(Decimal(g['total_lessons']) / step)
    now = msk_now()
    changed = 0

    with transaction.atomic():
        rows = list(
            PlannedLesson.objects
            .select_for_update()
            .filter(group_id=group_id, seq__isnull=False)
            .order_by('seq')
        )
        extra = [p for p in rows if p.seq > target_count]
        blocked = [p for p in extra if p.status not in _MUTABLE_STATUSES]
        if blocked:
            recorded = len([p for p in rows if p.status not in _MUTABLE_STATUSES])
            raise PlanHasRecordedLessons(recorded)

        if extra:
            PlannedLesson.objects.filter(id__in=[p.id for p in extra]).delete()
            changed += len(extra)

        kept = [p for p in rows if p.seq <= target_count]
        if len(kept) >= target_count:
            return changed

        open_slots = [
            s for s in slots_by_group([group_id]).get(group_id, [])
            if s.effective_to is None
        ]
        if not open_slots:
            return changed  # некуда разворачивать хвост — план останется коротким

        if kept:
            last = kept[-1]
            start_seq = last.seq + 1
            start_number = last.lesson_number
            start_date = last.scheduled_date + datetime.timedelta(days=1)
        elif g['group_start_date'] is not None:
            start_seq = 1
            start_number = Decimal('0')
            start_date = g['group_start_date']
        else:
            return changed

        new_rows = planner.generate(
            start_date=start_date,
            slots=open_slots,
            total_lessons=g['total_lessons'],
            duration_minutes=g['lesson_duration_minutes'],
            default_teacher_id=g['teacher_id'],
            start_seq=start_seq,
            start_number=start_number,
        )
        if new_rows:
            PlannedLesson.objects.bulk_create([
                PlannedLesson(
                    group_id=group_id,
                    seq=r.seq,
                    lesson_number=r.lesson_number,
                    scheduled_date=r.scheduled_date,
                    scheduled_time=r.scheduled_time,
                    teacher_id=r.teacher_id,
                    status=r.status,
                    created_at=now,
                    updated_at=now,
                )
                for r in new_rows
            ])
            changed += len(new_rows)

    return changed
