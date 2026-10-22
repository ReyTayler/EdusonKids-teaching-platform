"""
Тонкие APIView для /api/admin/payroll (права — только superadmin, IsSuperAdmin)
и /api/my-payroll (преподаватель, IsTeacher).

Начисления:
  GET   /api/admin/payroll                          → уроки + отмены {rows,total,page,page_size}
  GET   /api/admin/payroll/summary                  → сводка по преподавателю [...]
  PATCH /api/admin/payroll/<id>                     → ручная правка начисления урока
  POST  /api/admin/payroll/<id>/reset               → сброс правки урока к расчёту
  PATCH /api/admin/payroll/cancellations/<id>       → ручная правка суммы отмены
  POST  /api/admin/payroll/cancellations/<id>/reset → сброс правки отмены

«Настройки → Мотивация» (apps/payroll/motivation.py):
  GET/POST     /api/admin/payroll/rate-versions, PATCH/DELETE …/<id>
  GET/POST     /api/admin/payroll/personal-rates, PATCH/DELETE …/<id>
  POST         /api/admin/payroll/personal-rates/<id>/deactivate | …/activate
Ответ мутации ставок несёт `recalculated` — число изменённых начислений.
Изменения ставок действуют с сегодняшнего дня по МСК и прошлое не меняют (§8).

Сортировка списков — только из whitelist: мусор в sort_by/sort_dir → 400.
"""
from __future__ import annotations

import datetime
import re

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsSuperAdmin, IsTeacher
from apps.core.utils.dates import msk_now
from apps.payroll import motivation, repository, services
from apps.payroll.serializers import (
    CancellationPayUpdateSerializer,
    PayrollUpdateSerializer,
    PersonalRateWriteSerializer,
    RateVersionWriteSerializer,
)

_MONTH_RE = re.compile(r'^(\d{4})-(0[1-9]|1[0-2])$')

_KINDS = (repository.KIND_LESSON, repository.KIND_CANCELLATION)


def _sort_params(request: Request, whitelist, default_by: str, default_dir: str) -> tuple[str, str]:
    """sort_by/sort_dir из whitelist; отсутствие — значение по умолчанию, мусор — 400."""
    qp = request.query_params
    sort_by = qp.get('sort_by') or default_by
    sort_dir = qp.get('sort_dir') or default_dir
    if sort_by not in whitelist:
        raise ValidationError({'sort_by': [f'Допустимо: {", ".join(whitelist)}.']})
    if sort_dir not in ('asc', 'desc'):
        raise ValidationError({'sort_dir': ['Допустимо: asc, desc.']})
    return sort_by, sort_dir


def _optional_int(request: Request, name: str):
    raw = request.query_params.get(name)
    if raw in (None, ''):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: ['Ожидается целое число.']})
    if value < 1:
        raise ValidationError({name: ['Ожидается целое число больше 0.']})
    return value


def _check_date(name: str, raw) -> None:
    """Дата фильтра — только ГГГГ-ММ-ДД: мусор иначе дошёл бы до SQL и упал 500."""
    if raw in (None, ''):
        return
    try:
        datetime.date.fromisoformat(str(raw))
    except ValueError:
        raise ValidationError({name: ['Ожидается дата ГГГГ-ММ-ДД.']})


def _not_found():
    return NotFound({'error': 'Not found'})


class PayrollListView(APIView):
    """GET /api/admin/payroll — начисления за уроки и отмены одним списком.

    Фильтры filter[teacher_id|group_id|group_name|teacher_name|date_from|date_to|kind].
    Строка: kind ('lesson'|'cancellation'), id (Payroll.id или CancellationPay.id —
    уникален в паре с kind), суммы, is_manual, calc_*, rule_note."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        sort_by, sort_dir = _sort_params(request, repository.SORTABLE,
                                         repository.DEFAULT_SORT_BY,
                                         repository.DEFAULT_SORT_DIR)
        filters = {key[7:-1]: value for key, value in request.query_params.items()
                   if key.startswith('filter[') and key.endswith(']')}
        for key in ('teacher_id', 'group_id'):
            if filters.get(key) not in (None, '') and not str(filters[key]).isdigit():
                raise ValidationError({f'filter[{key}]': ['Ожидается целое число.']})
        for key in ('date_from', 'date_to', 'lesson_date_from', 'lesson_date_to'):
            _check_date(f'filter[{key}]', filters.get(key))
        if filters.get('kind') not in (None, '', *_KINDS):
            raise ValidationError({'filter[kind]': [f'Допустимо: {", ".join(_KINDS)}.']})

        qs = repository.payroll_entries(sort_by=sort_by, sort_dir=sort_dir, filters=filters)
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response([repository.entry_row(r) for r in page])


class PayrollSummaryView(APIView):
    """GET /api/admin/payroll/summary — сводка по преподавателю (уроки + отмены);
    фильтры date_from/date_to/teacher_id."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        qp = request.query_params
        teacher_id = _optional_int(request, 'teacher_id')
        date_from = qp.get('date_from') or None
        date_to = qp.get('date_to') or None
        _check_date('date_from', date_from)
        _check_date('date_to', date_to)
        return Response(services.payroll_summary(
            teacher_id=teacher_id, date_from=date_from, date_to=date_to,
        ))


class MyPayrollView(APIView):
    """
    GET /api/my-payroll?month=YYYY-MM — зарплата ТЕКУЩЕГО преподавателя за месяц
    (уроки и отмены).

    Скоуп по teacher_id из JWT (request.user.teacher_id), НИКОГДА из запроса —
    иначе преподаватель прочитает чужую зарплату (тот же принцип, что в
    apps.teacher_spa.views.MyLessonsView).

    month необязателен: без него — текущий месяц по МСК.
    """

    permission_classes = [IsTeacher]

    def get(self, request: Request) -> Response:
        month = request.query_params.get('month') or msk_now().strftime('%Y-%m')
        if not _MONTH_RE.match(month):
            return Response(
                {'error': 'Некорректный параметр month (ожидается YYYY-MM)'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(services.my_payroll_month(request.user.teacher_id, month))


class PayrollDetailView(APIView):
    """PATCH /api/admin/payroll/:id — частичное обновление (правка суммы = ручная)."""

    permission_classes = [IsSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        serializer = PayrollUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        updated = services.update_payroll(pk, serializer.validated_data)
        if updated is None:
            raise _not_found()
        return Response(updated)


class PayrollResetView(APIView):
    """POST /api/admin/payroll/:id/reset — сбросить ручную правку урока к расчёту."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        result = services.reset_payroll(pk)
        if result is None:
            raise _not_found()
        return Response(result)


class CancellationPayDetailView(APIView):
    """PATCH /api/admin/payroll/cancellations/:id — ручная правка суммы отмены."""

    permission_classes = [IsSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        serializer = CancellationPayUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        updated = services.update_cancellation(pk, serializer.validated_data['payment'])
        if updated is None:
            raise _not_found()
        return Response(updated)


class CancellationPayResetView(APIView):
    """POST /api/admin/payroll/cancellations/:id/reset — сброс правки отмены."""

    permission_classes = [IsSuperAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            result = services.reset_cancellation(pk)
        except repository.CancellationPayConflict as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if result is None:
            raise _not_found()
        return Response(result)


# ---------------------------------------------------------------------------
# «Настройки → Мотивация»
# ---------------------------------------------------------------------------

class RateVersionListView(APIView):
    """GET — все версии + таблица направлений + «уроки без ставки»; POST — новая версия."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(motivation.list_versions())

    def post(self, request: Request) -> Response:
        serializer = RateVersionWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = motivation.create_version(dict(serializer.validated_data), request.user)
        return Response(result, status=status.HTTP_201_CREATED)


class RateVersionDetailView(APIView):
    """PATCH — исправить версию (в т.ч. дату); DELETE — удалить (период → предыдущая)."""

    permission_classes = [IsSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        from apps.payroll.models import PayRateVersion
        instance = PayRateVersion.objects.filter(id=pk).first()
        if instance is None:
            raise _not_found()
        serializer = RateVersionWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        result = motivation.update_version(pk, dict(serializer.validated_data))
        if result is None:
            raise _not_found()
        return Response(result)

    def delete(self, request: Request, pk: int) -> Response:
        result = motivation.delete_version(pk)
        if result is None:
            raise _not_found()
        return Response(result)


class PersonalRateListView(APIView):
    """GET — персональные ставки (?teacher=&group=, sort_by teacher|group|lesson_from,
    StandardPagination); POST — новая ставка."""

    permission_classes = [IsSuperAdmin]

    def get(self, request: Request) -> Response:
        sort_by, sort_dir = _sort_params(request, motivation.PERSONAL_SORTS,
                                         motivation.PERSONAL_DEFAULT_SORT, 'asc')
        qs = motivation.personal_rates_queryset(
            teacher_id=_optional_int(request, 'teacher'),
            group_id=_optional_int(request, 'group'),
            sort_by=sort_by, sort_dir=sort_dir,
        )
        paginator = StandardPagination()
        page = paginator.paginate_queryset(qs, request, view=self)
        return paginator.get_paginated_response(motivation.attach_terms(page))

    def post(self, request: Request) -> Response:
        serializer = PersonalRateWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = motivation.create_personal_rate(dict(serializer.validated_data), request.user)
        return Response(result, status=status.HTTP_201_CREATED)


class PersonalRateDetailView(APIView):
    """PATCH — исправить параметры (с сегодняшнего дня) или примечание;
    DELETE — удалить (с сегодняшнего дня, прошлое остаётся по ставке).
    Удалённая ставка — 404."""

    permission_classes = [IsSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        instance = motivation.personal_rate_state(pk)
        if instance is None:
            raise _not_found()
        serializer = PersonalRateWriteSerializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        result = motivation.update_personal_rate(pk, dict(serializer.validated_data))
        if result is None:
            raise _not_found()
        return Response(result)

    def delete(self, request: Request, pk: int) -> Response:
        result = motivation.delete_personal_rate(pk)
        if result is None:
            raise _not_found()
        return Response(result)


class PersonalRateToggleView(APIView):
    """POST …/personal-rates/<id>/deactivate | …/activate — переключатель
    «включена / выключена» с сегодняшнего дня. Отдельные действия, а не PATCH
    is_active: у переключения свои последствия для истории периодов (закрыть /
    открыть период) и своя метка в журнале изменений. Повтор — 200 без
    изменений (recalculated = 0): двойной клик не должен давать ошибку."""

    permission_classes = [IsSuperAdmin]
    toggle = ''  # 'activate' | 'deactivate' — задаётся в urls.py через as_view

    def post(self, request: Request, pk: int) -> Response:
        handler = (motivation.activate_personal_rate if self.toggle == 'activate'
                   else motivation.deactivate_personal_rate)
        result = handler(pk)
        if result is None:
            raise _not_found()
        return Response(result)
