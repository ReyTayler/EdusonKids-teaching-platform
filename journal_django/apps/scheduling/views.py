"""
Тонкая вьюха календаря плановых занятий (role=teacher).

GET /api/calendar?from=YYYY-MM-DD&to=YYYY-MM-DD
  → { occurrences, unscheduled, window }

Скоуп — ТОЛЬКО группы текущего преподавателя (request.user.teacher_id): кабинет
не показывает чужие расписания. Вся логика — в services.py.
"""
from __future__ import annotations

import datetime

from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin, IsSuperAdmin, IsTeacher
from apps.scheduling import services
from apps.scheduling.exceptions import PlanResyncBlocked
from apps.scheduling.serializers import (
    PlanChangeTeacherPermanentSerializer, PlanChangeTeacherSerializer,
    PlanPermanentChangeSerializer, PlanRescheduleSerializer, PlanResyncSerializer,
)

# Максимальная ширина окна (дней) — календарь просит неделю/месяц; ограничиваем,
# чтобы не генерировать occurrences на произвольно большой диапазон.
_MAX_WINDOW_DAYS = 92


def _parse_window(request: Request) -> tuple[datetime.date, datetime.date] | Response:
    """
    Валидирует ?from=&to= (YYYY-MM-DD, to>=from, ширина ≤ _MAX_WINDOW_DAYS).
    Возвращает (d_from, d_to), либо готовый Response с 400 при ошибке —
    вызывающая сторона проверяет `isinstance(result, Response)`. Общий код
    для CalendarView (teacher) и AdminCalendarView (manager/admin/superadmin).
    """
    raw_from = request.query_params.get('from')
    raw_to = request.query_params.get('to')
    if not raw_from or not raw_to:
        return Response(
            {'error': 'Обязательны параметры from и to (YYYY-MM-DD).'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        d_from = datetime.date.fromisoformat(raw_from)
        d_to = datetime.date.fromisoformat(raw_to)
    except ValueError:
        return Response(
            {'error': 'from/to должны быть датами YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if d_to < d_from:
        return Response(
            {'error': 'to не может быть раньше from.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if (d_to - d_from).days > _MAX_WINDOW_DAYS:
        return Response(
            {'error': f'Слишком широкое окно (максимум {_MAX_WINDOW_DAYS} дней).'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return d_from, d_to


class CalendarView(APIView):
    """GET /api/calendar — плановые занятия преподавателя за окно [from, to]."""

    permission_classes = [IsTeacher]

    def get(self, request: Request) -> Response:
        window = _parse_window(request)
        if isinstance(window, Response):
            return window
        d_from, d_to = window

        result = services.build_calendar(d_from, d_to, teacher_id=request.user.teacher_id)
        return Response(result)


# PostgreSQL int4 (teachers.id) — верхняя граница, чтобы не словить
# NumericValueOutOfRange из БД на слишком большом teacher_id (500 вместо 400).
_MAX_INT4 = 2_147_483_647


class AdminCalendarView(APIView):
    """
    GET /api/admin/calendar — плановые занятия за окно [from, to]
    (role=manager/admin/superadmin). Используется разделом «Календарь» admin SPA.
    teacher_id — НЕОБЯЗАТЕЛЕН: без него отдаём занятия ВСЕХ преподавателей (вся
    школа), с ним — одного (фильтр по преподавателю). Если teacher_id передан, но
    невалиден — 400 (не молча игнорируем «мусорный» ввод).
    """

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        window = _parse_window(request)
        if isinstance(window, Response):
            return window
        d_from, d_to = window

        raw_teacher_id = request.query_params.get('teacher_id')
        teacher_id: int | None = None
        if raw_teacher_id:
            # isdecimal (не isdigit) — isdigit пропускает юникод-цифры вроде '²',
            # которые int() не парсит (ValueError → 500 вместо 400).
            if not raw_teacher_id.isdecimal() or int(raw_teacher_id) > _MAX_INT4:
                return Response(
                    {'error': 'Параметр teacher_id должен быть целым числом.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            teacher_id = int(raw_teacher_id)

        result = services.build_calendar(d_from, d_to, teacher_id=teacher_id)
        return Response(result)


# ---------------------------------------------------------------------------
# Admin-план (RBAC IsManagerOrAdmin). Операции над planned_lessons
# (generate/reschedule/permanent-change/cancel). Смонтированы под
# /api/admin/groups (ДО teacher-guard /api) → доступ проверяется на API, а не
# только на фронте. Мутации проходят DRF SessionAuthentication/CookieJWT →
# требуют X-CSRFToken (@csrf_exempt не ставим). След мутаций — в «Журнале
# изменений» (pghistory, правила plan.*), НЕ в журнале ИБ.
# ---------------------------------------------------------------------------

def _is_unique_violation(exc: Exception) -> bool:
    """Нарушение уникальности PostgreSQL (pgcode 23505) — прямое или в __cause__."""
    if getattr(exc, 'pgcode', None) == '23505':
        return True
    cause = getattr(exc, '__cause__', None)
    return bool(cause and getattr(cause, 'pgcode', None) == '23505')


class GroupPlanView(APIView):
    """GET /api/admin/groups/<pk>/plan — список плановых занятий группы."""

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        plan = services.get_plan(pk)
        if plan is None:
            raise NotFound({'error': 'Not found'})
        return Response(plan)


class GroupPlanGenerateView(APIView):
    """POST /api/admin/groups/<pk>/plan/generate — генерация плана (идемпотентно)."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            plan = services.generate_plan(pk, request)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Conflict'}, status=status.HTTP_409_CONFLICT)
            raise
        if plan is None:
            raise NotFound({'error': 'Not found'})
        return Response(plan)


class GroupPlanRescheduleView(APIView):
    """POST /api/admin/groups/<pk>/plan/<lid>/reschedule — разовый перенос (+опц. teacher)."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int, lid: int) -> Response:
        serializer = PlanRescheduleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = services.reschedule(pk, lid, serializer.validated_data, request)
        except ValueError as exc:
            # Перенос проведённого (status='done') — бизнес-конфликт, не 500.
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Conflict'}, status=status.HTTP_409_CONFLICT)
            raise
        if row is None:
            raise NotFound({'error': 'Not found'})
        return Response(row)


class GroupPlanPermanentChangeView(APIView):
    """POST /api/admin/groups/<pk>/plan/permanent-change — перенос навсегда (с seq/даты)."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        serializer = PlanPermanentChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if data.get('preview'):
            # Дран-режим: ничего не пишем, только repository.preview_affected
            # через services.preview_permanent_change (сама конвертирует
            # effective_from в date, как и permanent_change). None → группы
            # нет (404), тот же контракт, что и у реального пути ниже.
            affected = services.preview_permanent_change(
                pk, data['from_seq'], data['effective_from'])
            if affected is None:
                raise NotFound({'error': 'Not found'})
            return Response({'affected': affected})
        try:
            plan = services.permanent_change(pk, data, request)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except IntegrityError as exc:
            if _is_unique_violation(exc):
                return Response({'error': 'Slot already exists'}, status=status.HTTP_409_CONFLICT)
            raise
        if plan is None:
            raise NotFound({'error': 'Not found'})
        return Response(plan)


class GroupPlanCancelView(APIView):
    """POST /api/admin/groups/<pk>/plan/<lid>/cancel — отмена со сдвигом хвоста."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int, lid: int) -> Response:
        try:
            plan = services.cancel(pk, lid, request)
        except ValueError as exc:
            # Якорь не курсовой/активный (extra/cancelled/moved/done) — бизнес-ошибка.
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if plan is None:
            raise NotFound({'error': 'Not found'})
        return Response(plan)


class GroupPlanChangeTeacherView(APIView):
    """POST /api/admin/groups/<pk>/plan/<lid>/change-teacher — разовая смена препода."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int, lid: int) -> Response:
        serializer = PlanChangeTeacherSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            row = services.change_teacher(pk, lid, serializer.validated_data, request)
        except ValueError as exc:
            # Смена препода проведённого (status='done') — бизнес-конфликт, не 500.
            return Response({'error': str(exc)}, status=status.HTTP_409_CONFLICT)
        if row is None:
            raise NotFound({'error': 'Not found'})
        return Response(row)


class GroupPlanHealthView(APIView):
    """
    GET /api/admin/groups/<pk>/plan/health — проверки здоровья плана + предпросмотр
    починки. ТОЛЬКО ЧТЕНИЕ (health-модуль ничего не пишет по контракту).

    IsSuperAdmin, а не IsManagerOrAdmin: разбор рассогласований план↔факт —
    операция уровня владельца системы, и предпросмотр здесь неотделим от кнопки
    применения (resync ниже).
    """

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request, pk: int) -> Response:
        report = services.plan_health(pk)
        if report is None:
            raise NotFound({'error': 'Not found'})
        return Response(report)


class GroupPlanResyncView(APIView):
    """
    POST /api/admin/groups/<pk>/plan/resync — починка «номер факта = номер позиции».

    Тело: {'expected': [[position_id, fact_lesson_id|null, 'YYYY-MM-DD'], ...]} —
    подтверждение диффа из предпросмотра. 409: сработала граница слоя 3, есть
    факты без позиции своего номера, либо состояние изменилось. «Чинить нечего» —
    это 200 с applied=0, а не конфликт.
    """

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        serializer = PlanResyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.resync_plan(pk, expected=serializer.validated_data['expected'])
        except PlanResyncBlocked as exc:
            # except СНАРУЖИ transaction.atomic (она внутри repository) — иначе
            # был бы TransactionManagementError вместо 409.
            return Response(
                {'error': str(exc), 'blocked_by': exc.blocked_by,
                 'orphan_facts': exc.orphan_facts},
                status=status.HTTP_409_CONFLICT,
            )
        except IntegrityError as exc:
            # Остаточный 23505 (fact_lesson уникален): гонка с записью урока.
            if _is_unique_violation(exc):
                return Response(
                    {'error': 'Состояние изменилось, обновите предпросмотр.',
                     'blocked_by': [], 'orphan_facts': []},
                    status=status.HTTP_409_CONFLICT,
                )
            raise
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class GroupPlanChangeTeacherPermanentView(APIView):
    """POST /api/admin/groups/<pk>/plan/change-teacher-permanent — смена препода хвоста."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        serializer = PlanChangeTeacherPermanentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            plan = services.change_teacher_permanent(pk, serializer.validated_data, request)
        except ValueError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if plan is None:
            raise NotFound({'error': 'Not found'})
        return Response(plan)
