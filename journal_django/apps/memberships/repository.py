"""
MembershipsRepository — единственное место доступа к данным раздела memberships.

ORM-порт services/repo/memberships.js (раздел 09).

GET список — без пагинации, просто список.
POST — UPSERT: повторный вызов с той же парой (group_id, student_id) реактивирует
(ON CONFLICT DO UPDATE SET active=true, остальные поля не трогаются).
"""
from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from typing import Any, Optional

from django.db import transaction
from django.db.models import F

from apps.core.utils.dates import msk_today
from apps.core.utils.orm import dictrow, dictrows
from apps.finances.repository import balance_for_student, balances_for_students
from apps.groups.models import Group

from .exceptions import (
    AlreadyActiveInGroup,
    DirectionMismatch,
    IndividualGroupFull,
    SameGroupTransfer,
    SourceMembershipInvalid,
    TargetGroupUnavailable,
)
from .models import GroupMembership


# Поля строки membership (gm.* / RETURNING *), в порядке схемы.
_MEMBERSHIP_FIELDS = (
    'id', 'group_id', 'student_id', 'lessons_done',
    'start_date', 'sheet_row', 'active', 'transferred_from_id',
)


_MAX_TRANSFER_CHAIN = 20  # защитный лимит — реальные цепочки в разы короче


def cumulative_transferred_lessons(transferred_from_id: Optional[int]) -> Decimal:
    """
    Сколько уроков курса ученик прошёл ДО текущей группы: сумма lessons_done по
    цепочке переводов, начиная с transferred_from_id (сама текущая membership НЕ
    включается — только предки).

    Ученика могут перевести несколько раз подряд (А→Б→В→...) — transferred_from
    каждой membership указывает на непосредственно предыдущую, образуя связный
    список; функция проходит его назад.

    Обход ОСТАНАВЛИВАЕТСЯ на первой группе-продолжении (lesson_number_offset > 0):
    её lessons_done уже кумулятивен по всем предкам (Фаза 1b засеивает его
    кумулятивом), поэтому суммировать предков поверх него — двойной счёт.
    Подробности и симптом — в комментарии внутри цикла.

    Защита от цикла: если ученика переводят обратно в группу, где он уже был
    раньше в этой же цепочке, add_membership-паттерн (ON CONFLICT DO UPDATE)
    РЕАКТИВИРУЕТ старую membership-строку той же группы и перезаписывает её
    transferred_from на текущую — из-за этого цепочка технически может
    зациклиться (А.transferred_from → В → Б → А). `seen`-множество и
    _MAX_TRANSFER_CHAIN останавливают обход, не давая ему повиснуть; результат
    в этом редком случае — best-effort сумма до точки повторного визита, не
    гарантированно полная, но и не бесконечный цикл.
    """
    total = Decimal('0')
    seen: set[int] = set()
    current_id = transferred_from_id
    while current_id is not None and current_id not in seen and len(seen) < _MAX_TRANSFER_CHAIN:
        seen.add(current_id)
        row = (
            GroupMembership.objects
            .filter(id=current_id)
            .values('lessons_done', 'transferred_from_id', 'group__lesson_number_offset')
            .first()
        )
        if row is None:
            break
        total += row['lessons_done'] or Decimal('0')
        # Группа-продолжение (offset > 0) — точка ОСТАНОВКИ обхода. Фаза 1b засеяла
        # её lessons_done кумулятивом ВСЕХ предков (seed = cumulative), и дальше он
        # только рос на реально проведённых здесь уроках. Значит lessons_done этой
        # membership уже = «всё, что ученик прошёл по курсу к концу этой группы», и
        # идти к предкам НЕЛЬЗЯ — они учтутся дважды.
        # Симптом бага, который это чинит: А(18) → Б(продолжение) → В давало
        # offset(В) = 18 (из Б) + 18 (снова из А) = 36 вместо 18, и курс в В
        # стартовал с урока №37, перепрыгнув 18 уроков программы.
        # Для обычных групп (offset = 0) lessons_done — только «свои» уроки,
        # поэтому обход предков продолжается и суммирование корректно.
        if row['group__lesson_number_offset']:
            break
        current_id = row['transferred_from_id']
    return total


def locked_through_map(group_id: int, student_ids: list[int]) -> dict[int, Decimal]:
    """
    {student_id: B} только для учеников из student_ids, у которых АКТИВНАЯ
    membership в group_id имеет transferred_from (переведённые). Остальные в
    словаре отсутствуют — трактовать как Decimal('0') (не заблокированы).

    Один батч-запрос на переведённых + cumulative_transferred_lessons на каждого —
    переводы редки (тот же паттерн, что apps.groups.repository.get_group_progress
    для transferred-строк матрицы прогресса).
    """
    if not student_ids:
        return {}
    rows = (
        GroupMembership.objects
        .filter(group_id=group_id, student_id__in=student_ids, active=True,
                transferred_from_id__isnull=False)
        .values('student_id', 'transferred_from_id')
    )
    return {
        r['student_id']: cumulative_transferred_lessons(r['transferred_from_id'])
        for r in rows
    }


def locked_through(student_id: int, group_id: int) -> Decimal:
    """B для одного ученика/группы. См. locked_through_map для батча (N студентов)."""
    return locked_through_map(group_id, [student_id]).get(student_id, Decimal('0'))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_dates(row: Optional[dict]) -> Optional[dict]:
    """
    start_date: datetime.date → 'YYYY-MM-DD' in-place (аналог setTypeParser(1082)).
    Защита от timezone drift — дословно повторяет поведение исходного репозитория.
    """
    if row is None:
        return None
    val = row.get('start_date')
    if isinstance(val, _dt.date) and not isinstance(val, _dt.datetime):
        row['start_date'] = val.strftime('%Y-%m-%d')
    return row


def _membership_row(membership_id: int) -> Optional[dict]:
    """Строка membership (gm.* / RETURNING *) с нормализованной датой и вычисленным remaining."""
    row = _normalize_dates(
        dictrow(
            GroupMembership.objects.filter(id=membership_id).values(
                *_MEMBERSHIP_FIELDS,
                transferred_from_group_name=F('transferred_from__group__name'),
            )
        )
    )
    if row is not None:
        row['remaining'] = balance_for_student(row['student_id'])
        row['transferred_from_lessons_done'] = (
            cumulative_transferred_lessons(row['transferred_from_id'])
            if row['transferred_from_id'] else None
        )
    return row


def _assert_individual_capacity(
    group_id: int,
    *,
    exclude_student_id: Optional[int] = None,
    exclude_membership_id: Optional[int] = None,
) -> None:
    """
    Инвариант: в индивидуальной группе ≤1 активного membership.

    Вызывать ТОЛЬКО внутри transaction.atomic() — берёт row-lock на строку
    группы (select_for_update), чтобы два параллельных запроса не создали
    двух активных учеников (проверка+запись атомарны).

    - Группы нет или is_individual ложно → проверку пропускаем (return).
    - Иначе считаем «чужие» активные memberships этой группы, исключая текущую
      операцию (по student_id при добавлении, по membership_id при апдейте).
      Найден хоть один → IndividualGroupFull(active_student_id=<его student_id>).
    """
    grp = (
        Group.objects
        .select_for_update()
        .filter(id=group_id)
        .values('is_individual')
        .first()
    )
    if not grp or not grp['is_individual']:
        return

    qs = GroupMembership.objects.filter(group_id=group_id, active=True)
    if exclude_student_id is not None:
        qs = qs.exclude(student_id=exclude_student_id)
    if exclude_membership_id is not None:
        qs = qs.exclude(id=exclude_membership_id)

    other = qs.values('student_id').first()
    if other is not None:
        raise IndividualGroupFull(active_student_id=other['student_id'])


# ---------------------------------------------------------------------------
# Repository functions (ORM-порт services/repo/memberships.js)
# ---------------------------------------------------------------------------

def list_memberships(
    group_id: Optional[int] = None,
    student_id: Optional[int] = None,
    include_inactive: bool = False,
) -> list[dict]:
    """
    Возвращает список membership без пагинации.

    Фильтры: group_id, student_id, include_inactive (по умолчанию только active=true).
    Порядок: g.name, s.full_name. remaining — вычисляемый общий баланс ученика
    (apps.finances), одним батч-запросом на всех учеников выборки.
    """
    qs = GroupMembership.objects.all()
    if not include_inactive:
        qs = qs.filter(active=True)
    if group_id is not None:
        qs = qs.filter(group_id=group_id)
    if student_id is not None:
        qs = qs.filter(student_id=student_id)

    rows = dictrows(
        qs.order_by('group__name', 'student__full_name').values(
            *_MEMBERSHIP_FIELDS,
            group_name=F('group__name'),
            student_name=F('student__full_name'),
            transferred_from_group_name=F('transferred_from__group__name'),
        )
    )
    balances = balances_for_students({row['student_id'] for row in rows})
    for row in rows:
        _normalize_dates(row)
        row['remaining'] = balances[row['student_id']]
        row['transferred_from_lessons_done'] = (
            cumulative_transferred_lessons(row['transferred_from_id'])
            if row['transferred_from_id'] else None
        )
    return rows


def add_membership(data: dict) -> dict:
    """
    UPSERT membership (ON CONFLICT (group_id, student_id) DO UPDATE SET active=true).

    На вставке: lessons_done дефолтится в 0 (COALESCE(%s,0)). remaining не хранится —
    вычисляется при чтении (общий баланс ученика, apps.finances.repository).
    На конфликте: только active=true, остальные поля сохраняются (паттерн 4.9).
    """
    group_id = data['group_id']
    student_id = data['student_id']
    lessons_done = data.get('lessons_done')

    obj = GroupMembership(
        group_id=group_id,
        student_id=student_id,
        lessons_done=lessons_done if lessons_done is not None else 0,
        start_date=data.get('start_date') or None,
        sheet_row=data.get('sheet_row') or None,
        active=True,
    )
    with transaction.atomic():
        # Инвариант индивидуальной группы: проверяем ДО bulk_create, чтобы
        # pghistory InsertEvent не родился при отклонении (откат снимет lock).
        _assert_individual_capacity(group_id, exclude_student_id=student_id)
        GroupMembership.objects.bulk_create(
            [obj],
            update_conflicts=True,
            unique_fields=['group', 'student'],
            update_fields=['active'],   # ON CONFLICT DO UPDATE SET active=true
        )
    # RETURNING * — перечитываем строку по уникальной паре (id мог не вернуться при конфликте).
    row = _normalize_dates(
        dictrow(
            GroupMembership.objects
            .filter(group_id=group_id, student_id=student_id)
            .values(*_MEMBERSHIP_FIELDS)
        )
    )
    if row is not None:
        row['remaining'] = balance_for_student(student_id)
    return row


def update_membership(membership_id: int, data: dict) -> Optional[dict]:
    """
    Обновляет membership (PATCH через COALESCE, дословно из memberships.js).

    - lessons_done: COALESCE(%s, col) → set если значение не None (вкл. 0/0.5).
    - start_date/sheet_row: COALESCE(%s, col) → set если значение непусто.
    - active: COALESCE(%s, col) → set если ключ есть и значение не None.
    - remaining больше не пишется вручную — вычисляется при чтении (_membership_row).
    """
    with transaction.atomic():
        obj = GroupMembership.objects.filter(id=membership_id).first()
        if obj is None:
            return None

        # Реактивация (active=True) в индивидуальной группе — проверяем инвариант
        # ДО save(), исключая саму эту строку. PATCH без active / active=False
        # проверку не запускает.
        if data.get('active') is True:
            _assert_individual_capacity(obj.group_id, exclude_membership_id=membership_id)

        if data.get('lessons_done') is not None:
            obj.lessons_done = data['lessons_done']
        if data.get('start_date'):
            obj.start_date = data['start_date']
        if data.get('sheet_row'):
            obj.sheet_row = data['sheet_row']
        if data.get('active') is not None and 'active' in data:
            obj.active = data['active']

        obj.save()
    return _membership_row(membership_id)


def get_student_group(membership_id: int) -> Optional[dict]:
    """(student_id, group_id, active) любого membership — для гейта снятия членства
    (enforce_membership_cancellation). None, если строки нет."""
    return (GroupMembership.objects.filter(id=membership_id)
            .values('student_id', 'group_id', 'active').first())


def remove_membership(membership_id: int) -> bool:
    """Мягкое удаление: active=false. True если строка найдена."""
    updated = GroupMembership.objects.filter(id=membership_id).update(active=False)
    return updated > 0


def place_student_in_group(
    student_id: int,
    to_group_id: int,
    from_membership_id: Optional[int] = None,
) -> dict:
    """
    Атомарная запись/перевод ученика в группу. Движок legacy-эндпоинта
    POST /api/admin/memberships/:id/transfer (см. transfer_membership ниже);
    универсальный эндпоинт POST /memberships/place, ради которого функция
    задумывалась, снят — запись в группу идёт обычным POST /memberships.

    Три сценария:

    - **Перевод** — from_membership_id указывает на АКТИВНУЮ membership того же
      направления: она деактивируется (active=false, lessons_done остаётся честной
      историей), новая получает transferred_from = источник.
    - **Запись с историей** — from_membership_id указывает на НЕАКТИВНУЮ membership
      того же направления (ученик вернулся после перерыва): источник не трогаем
      (уже неактивен), только линкуем transferred_from → на карточке видно
      «отработано N ур. ранее».
    - **Запись с нуля** — from_membership_id=None: transferred_from=NULL, в любое
      направление (правило «то же направление» действует только когда источник задан).

    Новая membership создаётся/реактивируется UPSERT-паттерном (start_date=сегодня,
    lessons_done=0 при вставке).

    Бросает:
      TargetGroupUnavailable / SourceMembershipInvalid / SameGroupTransfer /
      DirectionMismatch → 400 во view;
      AlreadyActiveInGroup / IndividualGroupFull → 409 во view.
    Существование ученика проверяет view (→ 404), не эта функция.
    """
    with transaction.atomic():
        target = (
            Group.objects
            .filter(id=to_group_id, active=True)
            .values('direction_id')
            .first()
        )
        if target is None:
            raise TargetGroupUnavailable()

        source = None
        if from_membership_id is not None:
            source = (
                GroupMembership.objects
                .select_related('group')
                .select_for_update(of=('self',))
                .filter(id=from_membership_id)
                .first()
            )
            if source is None or source.student_id != student_id:
                raise SourceMembershipInvalid()
            if source.group_id == to_group_id:
                raise SameGroupTransfer()
            if source.group.direction_id != target['direction_id']:
                raise DirectionMismatch()

        # Уже активен в цели → отказ (иначе UPSERT перезатёр бы transferred_from/
        # start_date существующей активной membership — см. аудит 2026-07-20).
        already_active = (
            GroupMembership.objects
            .filter(group_id=to_group_id, student_id=student_id, active=True)
            .exists()
        )
        if already_active:
            raise AlreadyActiveInGroup()

        # Инвариант индивидуальной группы — до записи, как в add_membership.
        _assert_individual_capacity(to_group_id, exclude_student_id=student_id)

        # Деактивируем источник только если он активен (истинный перевод).
        if source is not None and source.active:
            source.active = False
            source.save(update_fields=['active'])

        new_obj = GroupMembership(
            group_id=to_group_id,
            student_id=student_id,
            active=True,
            transferred_from_id=from_membership_id,
            start_date=msk_today(),
        )
        GroupMembership.objects.bulk_create(
            [new_obj],
            update_conflicts=True,
            unique_fields=['group', 'student'],
            update_fields=['active', 'transferred_from', 'start_date'],
        )
        new_id = (
            GroupMembership.objects
            .filter(group_id=to_group_id, student_id=student_id)
            .values_list('id', flat=True)
            .first()
        )

        _seed_transfer_continuation(to_group_id, student_id, from_membership_id)

    return _membership_row(new_id)


def _seed_continuation(to_group_id: int, student_id: int, b) -> None:
    """
    Ядро «продолжения курса» (Фаза 1b), ИСТОЧНИК-АГНОСТИЧНОЕ: если ученик —
    ЕДИНСТВЕННЫЙ активный участник group_id БЕЗ проведённых
    regular/substitution/reschedule уроков и B>0 — группа продолжает курс с B+1
    вместо того чтобы «спать» первые B уроков.

    Действие: membership.lessons_done = B (не 0 — иначе ad-hoc lesson_number у
    teacher_spa = max(lessons_done)+step стартовал бы с 1, а не с B+1);
    group.lesson_number_offset = B; план группы пересобирается с этим офсетом.

    B выводится из истории переводов (`_seed_transfer_continuation`). Ручной ввод
    B менеджером («начать с урока N без истории») жил на снятом эндпоинте
    POST /memberships/place и удалён вместе с ним.

    No-op: B<=0, в группе есть другой активный участник, в группе уже есть
    проведённый regular/substitution/reschedule урок.

    Вызывать ВНУТРИ той же транзакции, что создание membership.
    """
    if b is None or b <= 0:
        return

    other_active = (
        GroupMembership.objects
        .filter(group_id=to_group_id, active=True)
        .exclude(student_id=student_id)
        .exists()
    )
    if other_active:
        return

    from apps.lessons.models import COURSE_LESSON_TYPES, Lesson
    has_course_lessons = Lesson.objects.filter(
        group_id=to_group_id, lesson_type__in=COURSE_LESSON_TYPES,
    ).exists()
    if has_course_lessons:
        return

    GroupMembership.objects.filter(
        group_id=to_group_id, student_id=student_id, active=True,
    ).update(lessons_done=b)
    Group.objects.filter(id=to_group_id).update(lesson_number_offset=b)

    from apps.scheduling.repository import reset_plan, generate_for_group
    reset_plan(to_group_id)
    generate_for_group(to_group_id)


def _seed_transfer_continuation(to_group_id: int, student_id: int, from_membership_id: Optional[int]) -> None:
    """Тонкая обёртка над `_seed_continuation` с B, выведенным из истории переводов
    (cumulative_transferred_lessons). No-op, если источник не задан."""
    if from_membership_id is None:
        return
    _seed_continuation(to_group_id, student_id, cumulative_transferred_lessons(from_membership_id))


def transfer_membership(membership_id: int, to_group_id: int) -> Optional[dict]:
    """
    Тонкая обёртка над place_student_in_group для legacy-эндпоинта
    POST /api/admin/memberships/:id/transfer.

    Требует АКТИВНУЮ исходную membership (как исторический контракт): если её нет
    или она неактивна — возвращает None (view → 404). Остальную логику (гарды,
    деактивацию, UPSERT) выполняет place_student_in_group.
    """
    student_id = (
        GroupMembership.objects
        .filter(id=membership_id, active=True)
        .values_list('student_id', flat=True)
        .first()
    )
    if student_id is None:
        return None
    return place_student_in_group(
        student_id,
        to_group_id,
        from_membership_id=membership_id,
    )
