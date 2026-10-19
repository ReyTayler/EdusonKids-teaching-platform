"""APIView для /api/admin/renewals. Права: IsManagerOrAdmin (manager/admin/superadmin)."""
from __future__ import annotations

from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import (
    IsAdminOrSuperAdmin,
    IsManagerOrAdmin,
    ReadStaffWriteSuperAdmin,
)
from apps.renewals import services
from apps.renewals.serializers import (
    MoveSerializer,
    OutcomeDateSerializer,
    StageReorderSerializer,
    StageWriteSerializer,
)
from apps.renewals.transitions import InvalidTransition

SORT_FIELDS = ['stage_entered_at', 'cycle_no', 'student_name']
# числовые фильтры, попадающие в SQL как int — нечисловой ввод даёт 400, не 500.
# cycle_no — только для списочного вида (list_deals); board его игнорирует.
INT_FILTERS = ('assignee_id', 'direction_id', 'stage_id', 'cycle_no')


def _int_or_400(value, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{field} must be an integer")


class RenewalCollectionView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request) -> Response:
        """Ручное создание сделки ученику без открытой сделки (из сводки)."""
        from apps.renewals.serializers import DealCreateSerializer
        ser = DealCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        result = services.create_deal(
            ser.validated_data['student_id'],
            author_id=getattr(request.user, 'id', None))
        if result is None:
            raise NotFound({'error': 'Student not found'})
        if result == 'exists':
            return Response({'error': 'У ученика уже есть открытая сделка'},
                            status=status.HTTP_409_CONFLICT)
        if result == 'cycle_taken':
            return Response(
                {'error': 'Текущий цикл ученика уже закрыт сделкой — '
                          'переоткройте её из карточки, а не создавайте новую'},
                status=status.HTTP_409_CONFLICT)
        return Response(result, status=status.HTTP_201_CREATED)

    def get(self, request: Request) -> Response:
        qp = request.query_params
        view = qp.get('view', 'board')
        filters = {k[7:-1]: v for k, v in qp.items()
                   if k.startswith('filter[') and k.endswith(']')}
        # валидируем числовые фильтры на границе (board/list_deals кладут их в SQL как int)
        for key in INT_FILTERS:
            if filters.get(key):
                filters[key] = _int_or_400(filters[key], f'filter[{key}]')
        if view == 'list':
            page = max(1, _int_or_400(qp.get('page', 1) or 1, 'page'))
            page_size = min(200, max(1, _int_or_400(qp.get('page_size', 50) or 50, 'page_size')))
            sort_by = qp.get('sort_by', 'stage_entered_at')
            sort_dir = qp.get('sort_dir', 'asc')
            if sort_by not in SORT_FIELDS:
                raise ValidationError(f"Invalid sort_by. Allowed: {SORT_FIELDS}")
            if sort_dir not in ('asc', 'desc'):
                raise ValidationError("sort_dir must be 'asc' or 'desc'")
            return Response(services.list_deals(
                page=page, page_size=page_size, sort_by=sort_by,
                sort_dir=sort_dir, filters=filters))
        return Response(services.board(filters))


class RenewalColumnCardsView(APIView):
    """Догрузка карточек одной колонки канбана («Показать ещё»)."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, stage_id: int) -> Response:
        qp = request.query_params
        offset = max(0, _int_or_400(qp.get('offset', 0) or 0, 'offset'))
        filters = {k[7:-1]: v for k, v in qp.items()
                   if k.startswith('filter[') and k.endswith(']')}
        for key in INT_FILTERS:
            if filters.get(key):
                filters[key] = _int_or_400(filters[key], f'filter[{key}]')
        return Response(services.column_cards(stage_id, offset, filters))


class RenewalDetailView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        deal = services.get_deal(pk)
        if deal is None:
            raise NotFound({'error': 'Not found'})
        return Response(deal)

    def patch(self, request: Request, pk: int) -> Response:
        from apps.renewals.serializers import DealPatchSerializer
        ser = DealPatchSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        result = services.patch_deal(pk, ser.validated_data)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class RenewalMoveView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        ser = MoveSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            result = services.move_deal(
                pk, ser.validated_data['to_stage_id'],
                ser.validated_data.get('reason_code'),
                author_id=getattr(request.user, 'id', None),
                frozen_until_month=ser.validated_data.get('frozen_until_month'),
                # Admin/superadmin могут отметить «Продлён» при балансе <= 0
                # (продление договорено, оплата придёт позже); менеджер — нет.
                allow_nonpositive_balance=IsAdminOrSuperAdmin().has_permission(request, self))
        except InvalidTransition as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class RenewalReopenView(APIView):
    """Переоткрытие закрытой сделки (won/lost → вычисленная авто-стадия)."""
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            result = services.reopen_deal(pk, author_id=getattr(request.user, 'id', None))
        except InvalidTransition as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if result is None:
            raise NotFound({'error': 'Not found'})
        if result == 'not_closed':
            return Response({'error': 'Сделка не закрыта — переоткрывать нечего'},
                            status=status.HTTP_409_CONFLICT)
        return Response(result)


class RenewalOutcomeDateView(APIView):
    """Ручная правка даты закрытия сделки.

    Только admin/superadmin: дата закрытия — это месяц, в который аналитика и
    «Переходимость» относят продление или уход, то есть правка задним числом
    двигает отчётность. Менеджер, ведущий сделку, такого права не имеет.
    """
    permission_classes = [IsAdminOrSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        ser = OutcomeDateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        result = services.set_outcome_date(
            pk, ser.validated_data['outcome_date'],
            author_id=getattr(request.user, 'id', None))
        if result is None:
            raise NotFound({'error': 'Not found'})
        if result == 'not_closed':
            return Response({'error': 'Сделка не закрыта — менять дату закрытия нечему'},
                            status=status.HTTP_409_CONFLICT)
        return Response(result)


class RenewalUnfreezeView(APIView):
    """«Вернуть в работу»: сделка со стадии-паузы → расчётная авто-стадия.

    Единственный ручной выход со стадии-паузы (спека 2026-07-25 про заморозку,
    с 2026-08-06 — любая стадия с allow_mid_cycle) — авто-возврата по факту
    записанного урока нет, engine.sync_lesson_stage такие стадии осознанно не трогает.
    """
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        result = services.unfreeze_deal(pk, author_id=getattr(request.user, 'id', None))
        if result is None:
            raise NotFound({'error': 'Not found'})
        if result == 'not_frozen':
            return Response({'error': 'Сделку нельзя вернуть в работу с этой стадии'},
                            status=status.HTTP_409_CONFLICT)
        return Response(result)


class RenewalUnassignedView(APIView):
    """Сводка «Ученики без сделок» — источник для ручного создания сделок."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_unassigned())


class RenewalUnassignedCountView(APIView):
    """Только число для бейджа в шапке раздела — читается при каждом входе,
    поэтому не тянет список с направлениями, посещаемостью и балансами."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response({'count': services.count_unassigned()})


class RenewalAssigneesView(APIView):
    """Кандидаты в ответственные (для SelectInput в карточке сделки)."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_assignees())


class RenewalCommentView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        from apps.renewals.serializers import CommentSerializer
        ser = CommentSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        result = services.add_comment(pk, ser.validated_data['body'],
                                       getattr(request.user, 'id', None))
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result, status=status.HTTP_201_CREATED)


class RenewalActivityView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        return Response(services.list_activity(pk))


class RenewalStageListView(APIView):
    permission_classes = [ReadStaffWriteSuperAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.list_stages())

    def post(self, request: Request) -> Response:
        ser = StageWriteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(services.create_stage(ser.validated_data),
                        status=status.HTTP_201_CREATED)


class RenewalStageDetailView(APIView):
    permission_classes = [ReadStaffWriteSuperAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        ser = StageWriteSerializer(data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        result = services.update_stage(pk, ser.validated_data)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)

    def delete(self, request: Request, pk: int) -> Response:
        outcome = services.delete_stage(pk)
        if outcome == 'not_found':
            raise NotFound({'error': 'Not found'})
        if outcome in ('has_open_deals', 'protected'):
            return Response({'error': outcome}, status=status.HTTP_409_CONFLICT)
        return Response(status=status.HTTP_204_NO_CONTENT)


class RenewalStageReorderView(APIView):
    permission_classes = [ReadStaffWriteSuperAdmin]

    def post(self, request: Request) -> Response:
        ser = StageReorderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        return Response(services.reorder_stages(ser.validated_data['order']))


class RenewalAnalyticsView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response(services.analytics_funnel(request.query_params.get('group_by')))
