"""
API графика работы. Обработчики — миксины, скоуп преподавателя — отдельный
миксин, и только конкретные классы внизу файла наследуют APIView:
- teacher (/api/work-schedule/…, IsTeacher) — преподаватель ТОЛЬКО из учётки;
- admin (/api/admin/teachers/<id>/work-schedule/…, IsManagerOrAdmin).

Почему миксины, а не базовые APIView: базовый APIView без permission_classes
получает DRF-default AllowAny, и случайное монтирование его в urls открыло бы
эндпоинт всем. Миксин без APIView смонтировать нельзя (нет as_view), а тест
test_every_view_declares_permissions ловит конкретный класс без прав.

Мутации требуют X-CSRFToken (cookie-JWT), @csrf_exempt не ставим.
"""
from __future__ import annotations

import datetime

from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.availability import services
from apps.availability.serializers import TemplateSerializer, WeekSerializer
from apps.core.permissions import IsManagerOrAdmin, IsTeacher
from apps.scheduling.views import _MAX_INT4, _parse_window
from apps.teachers.models import Teacher


def _ivs(intervals) -> list[list[str]]:
    return [[services.fmt(s), services.fmt(e)] for s, e in intervals]


def _error(exc: Exception) -> Response:
    return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)


# Ответы 403/404 собираем сами, а не исключениями DRF: custom_exception_handler
# подменяет detail NotFound/PermissionDenied на безликие 'Not found'/'Forbidden',
# и пользователь не узнал бы причину.
def _not_found() -> Response:
    return Response({'error': 'Преподаватель не найден.'}, status=status.HTTP_404_NOT_FOUND)


def _no_teacher_link() -> Response:
    return Response({'error': 'Учётка не привязана к преподавателю.'},
                    status=status.HTTP_403_FORBIDDEN)


def _mutate(fn, *args) -> Response | None:
    """
    Вызов мутации сервиса: ScheduleError → 400 с текстом правила;
    Teacher.DoesNotExist (преподавателя удалили между проверкой скоупа и
    блокировкой строки) → 404. None — успех.
    """
    try:
        fn(*args)
    except services.ScheduleError as exc:
        return _error(exc)
    except Teacher.DoesNotExist:
        return _not_found()
    return None


# ── Скоупы: чей график ──────────────────────────────────────────────────────
# teacher_id() возвращает id или готовый Response с отказом (как _parse_window).

class _TeacherScope:
    def teacher_id(self, request: Request, kwargs) -> int | Response:
        # Только из учётки: teacher_id из запроса не принимаем вовсе.
        teacher_id = request.user.teacher_id
        if teacher_id is None:
            return _no_teacher_link()
        return teacher_id


class _AdminScope:
    def teacher_id(self, request: Request, kwargs) -> int | Response:
        teacher_id = kwargs['teacher_id']
        # Вне int4 PostgreSQL ответил бы NumericValueOutOfRange (500).
        if teacher_id > _MAX_INT4 or not Teacher.objects.filter(pk=teacher_id).exists():
            return _not_found()
        return teacher_id


# ── Обработчики (миксины, без APIView) ──────────────────────────────────────

class _TemplateMixin:
    def get(self, request: Request, **kwargs) -> Response:
        tid = self.teacher_id(request, kwargs)
        if isinstance(tid, Response):
            return tid
        return Response(self._payload(tid))

    def put(self, request: Request, **kwargs) -> Response:
        tid = self.teacher_id(request, kwargs)
        if isinstance(tid, Response):
            return tid
        ser = TemplateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        failed = _mutate(services.replace_template, tid, ser.validated_data['days'])
        return failed or Response(self._payload(tid))

    @staticmethod
    def _payload(tid: int) -> dict:
        template = services.get_template(tid)
        return {
            'days': {str(d): _ivs(template.get(d, [])) for d in range(7)},
            'upcoming_overrides': [
                {'date': o['date'].isoformat(), 'intervals': _ivs(o['intervals'])}
                for o in services.upcoming_overrides(tid)
            ],
        }


class _RangeMixin:
    def get(self, request: Request, **kwargs) -> Response:
        tid = self.teacher_id(request, kwargs)
        if isinstance(tid, Response):
            return tid
        window = _parse_window(request)
        if isinstance(window, Response):
            return window
        d_from, d_to = window
        windows = services.get_effective_windows(tid, d_from, d_to)
        return Response({
            'has_schedule': services.has_schedule(tid),
            'windows': {d.isoformat(): _ivs(ivs) for d, ivs in windows.items()},
            'override_dates': [d.isoformat() for d in services.override_dates(tid, d_from, d_to)],
        })


class _WeekMixin:
    def put(self, request: Request, **kwargs) -> Response:
        tid = self.teacher_id(request, kwargs)
        if isinstance(tid, Response):
            return tid
        ser = WeekSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        failed = _mutate(services.replace_week, tid,
                         ser.validated_data['monday'], ser.validated_data['days'])
        return failed or Response({'ok': True})


class _OverrideMixin:
    def delete(self, request: Request, date: str, **kwargs) -> Response:
        tid = self.teacher_id(request, kwargs)
        if isinstance(tid, Response):
            return tid
        try:
            d = datetime.date.fromisoformat(date)
        except ValueError:
            return Response({'error': 'Дата — YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)
        failed = _mutate(services.clear_override, tid, d)
        return failed or Response(status=status.HTTP_204_NO_CONTENT)


# ── Конкретные вьюхи: у КАЖДОЙ явные permission_classes ─────────────────────

class TeacherTemplateView(_TeacherScope, _TemplateMixin, APIView):
    """GET/PUT /api/work-schedule/template — шаблон недели своего графика."""
    permission_classes = [IsTeacher]


class TeacherRangeView(_TeacherScope, _RangeMixin, APIView):
    """GET /api/work-schedule?from=&to= — итоговые окна своего графика."""
    permission_classes = [IsTeacher]


class TeacherWeekView(_TeacherScope, _WeekMixin, APIView):
    """PUT /api/work-schedule/week — изменения конкретной недели."""
    permission_classes = [IsTeacher]


class TeacherOverrideView(_TeacherScope, _OverrideMixin, APIView):
    """DELETE /api/work-schedule/overrides/<date> — сбросить день к шаблону."""
    permission_classes = [IsTeacher]


class AdminTemplateView(_AdminScope, _TemplateMixin, APIView):
    """GET/PUT /api/admin/teachers/<id>/work-schedule/template."""
    permission_classes = [IsManagerOrAdmin]


class AdminRangeView(_AdminScope, _RangeMixin, APIView):
    """GET /api/admin/teachers/<id>/work-schedule?from=&to=."""
    permission_classes = [IsManagerOrAdmin]


class AdminWeekView(_AdminScope, _WeekMixin, APIView):
    """PUT /api/admin/teachers/<id>/work-schedule/week."""
    permission_classes = [IsManagerOrAdmin]


class AdminOverrideView(_AdminScope, _OverrideMixin, APIView):
    """DELETE /api/admin/teachers/<id>/work-schedule/overrides/<date>."""
    permission_classes = [IsManagerOrAdmin]


class AdminFreeWindowsView(APIView):
    """
    GET /api/admin/work-schedule/free-windows — свободные окна всех преподавателей
    (шаблон графика минус постоянные уроки): вкладка «Сводка» admin-календаря.
    Права — как у самого календаря (/api/admin/calendar).
    """
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response({'teachers': services.free_windows_summary()})
