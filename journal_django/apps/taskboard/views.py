"""
Вьюхи раздела «Задачи».

DRF по умолчанию AllowAny — permission_classes задан ЯВНО в каждом классе.
Карточки: manager/admin. Физическое удаление задачи: admin/superadmin.
"""
from __future__ import annotations

from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.core.permissions import (
    IsAdminOrSuperAdmin, IsManagerOrAdmin, ReadStaffWriteAdmin, ReadStaffWriteSuperAdmin,
)
from apps.taskboard import repository, services
from apps.taskboard.models import Task, TaskBoard, TaskStage
from apps.taskboard.serializers import TaskCreateSerializer, TaskPatchSerializer


def _paginated(request: Request, queryset) -> Response:
    """Режем в БД: LIMIT/OFFSET на queryset, в словари превращаем только страницу."""
    paginator = StandardPagination()
    page = paginator.paginate_queryset(queryset, request)
    return paginator.get_paginated_response(repository.rows(page))


def _filters_from(request: Request) -> dict:
    """Разобрать и проверить фильтры списка. Мусор → 400, а не 500 из ORM."""
    from apps.taskboard.serializers import TaskFilterSerializer

    serializer = TaskFilterSerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


class TaskCollectionView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return _paginated(request, repository.tasks_queryset(_filters_from(request)))

    def post(self, request: Request) -> Response:
        serializer = TaskCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = dict(serializer.validated_data)
        # Постановщик — текущий пользователь, а не то, что прислал клиент.
        task = services.create_task(author_id=request.user.id, **data)
        return Response(repository.get_task(task.id), status=201)


class TaskDetailView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get_permissions(self):
        # Штатный способ убрать задачу — закрыть с результатом «неактуально».
        # Физическое удаление — только для явного мусора, роль admin/superadmin.
        if self.request.method == 'DELETE':
            return [IsAdminOrSuperAdmin()]
        return [IsManagerOrAdmin()]

    def get(self, request: Request, pk: int) -> Response:
        get_object_or_404(Task, pk=pk)
        return Response(repository.get_task(pk))

    def patch(self, request: Request, pk: int) -> Response:
        task = get_object_or_404(Task, pk=pk)
        serializer = TaskPatchSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        fields = dict(serializer.validated_data)
        assignee_ids = fields.pop('assignee_ids', None)
        # Правка полей и смена исполнителей — ОДНА транзакция. Иначе при падении
        # на валидации клиент получит 400, а изменённый заголовок уже сохранится.
        with transaction.atomic():
            services.update_task(task, author_id=request.user.id, fields=fields)
            if assignee_ids is not None:
                services.set_assignees(
                    task, assignee_ids=assignee_ids, author_id=request.user.id)
        return Response(repository.get_task(pk))

    def delete(self, request: Request, pk: int) -> Response:
        # Лента уходит каскадом (TaskActivity.task = CASCADE), связи с тегами
        # Django чистит сам — ручная зачистка была бы лишними запросами.
        get_object_or_404(Task, pk=pk).delete()
        return Response(status=204)


class TaskMoveView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        from apps.taskboard.serializers import MoveSerializer

        task = get_object_or_404(Task, pk=pk)
        serializer = MoveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.move_task(
            task,
            to_stage_id=serializer.validated_data['to_stage_id'],
            resolution=serializer.validated_data.get('resolution'),
            author_id=request.user.id,
        )
        return Response(repository.get_task(pk))


class TaskCompleteView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        from apps.taskboard.serializers import CompleteSerializer

        task = get_object_or_404(Task, pk=pk)
        serializer = CompleteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.complete_task(
            task,
            resolution=serializer.validated_data['resolution'],
            author_id=request.user.id,
        )
        return Response(repository.get_task(pk))


class TaskCommentView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        from apps.taskboard.serializers import CommentSerializer

        task = get_object_or_404(Task, pk=pk)
        serializer = CommentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        entry = services.add_comment(
            task, body=serializer.validated_data['body'], author_id=request.user.id)
        return Response(repository.activity_row(entry), status=201)


class TaskActivityView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, pk: int) -> Response:
        get_object_or_404(Task, pk=pk)
        return Response(repository.list_activity(pk))


class BoardColumnsView(APIView):
    """Колонки доски со счётчиками — без карточек."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, board_id: int) -> Response:
        return Response(repository.column_counts(board_id, _filters_from(request)))


class ColumnCardsView(APIView):
    """Карточки одной колонки, пагинированно."""
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request, stage_id: int) -> Response:
        params = _filters_from(request)
        params['stage_id'] = stage_id
        return _paginated(request, repository.tasks_queryset(params))


class WeekView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        from apps.taskboard.serializers import WeekQuerySerializer

        serializer = WeekQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        return Response(repository.list_week(
            date_from=serializer.validated_data['date_from'],
            date_to=serializer.validated_data['date_to'],
            params=_filters_from(request),
        ))


def _board_row(b: TaskBoard) -> dict:
    return {
        'id': b.id,
        'name': b.name,
        'description': b.description,
        'sort_order': b.sort_order,
        # Счётчики есть только в списке воронок (там они аннотированы). У только
        # что созданной воронки их взять неоткуда — отдаём нули, а не молчим:
        # клиент иначе получил бы поле то с числом, то без.
        'stages_count': getattr(b, 'stages_count', 0) or 0,
        'open_tasks_count': getattr(b, 'open_tasks_count', 0) or 0,
    }


def _stage_row(s: TaskStage) -> dict:
    return {
        'id': s.id, 'board_id': s.board_id, 'label': s.label,
        'color': s.color, 'category': s.category, 'sort_order': s.sort_order,
    }


class BoardCollectionView(APIView):
    permission_classes = [ReadStaffWriteAdmin]

    def get(self, request: Request) -> Response:
        # Счётчики для полосы выбора воронки. Считаем подзапросами, а не
        # JOIN+GROUP BY: две независимые связи (стадии и задачи) в одном
        # запросе перемножились бы, и число стадий вышло бы кратным числу задач.
        boards = (TaskBoard.objects
                  .annotate(
                      stages_count=Subquery(
                          TaskStage.objects
                          .filter(board_id=OuterRef('id'))
                          .values('board_id')
                          .annotate(n=Count('id'))
                          .values('n'),
                          output_field=IntegerField(),
                      ),
                      open_tasks_count=Subquery(
                          Task.objects
                          # Буквально closed_at IS NULL — иначе не подхватится
                          # частичный индекс task_assignee_open_idx.
                          .filter(board_id=OuterRef('id'), closed_at__isnull=True)
                          .values('board_id')
                          .annotate(n=Count('id'))
                          .values('n'),
                          output_field=IntegerField(),
                      ),
                  )
                  .order_by('sort_order', 'id'))
        return Response([_board_row(b) for b in boards])

    def post(self, request: Request) -> Response:
        from apps.taskboard.serializers import BoardWriteSerializer

        serializer = BoardWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            # Savepoint: без него IntegrityError ломает всю внешнюю транзакцию
            # (в тестах — транзакцию pytest-django), а не только эту вставку.
            with transaction.atomic():
                board = TaskBoard.objects.create(**serializer.validated_data)
        except IntegrityError:
            return Response({'error': 'duplicate_name'}, status=409)
        return Response(_board_row(board), status=201)


class BoardDetailView(APIView):
    permission_classes = [ReadStaffWriteAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        from apps.taskboard.serializers import BoardWriteSerializer

        board = get_object_or_404(TaskBoard, pk=pk)
        serializer = BoardWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        for key, value in serializer.validated_data.items():
            setattr(board, key, value)
        try:
            with transaction.atomic():
                board.save()
        except IntegrityError:
            return Response({'error': 'duplicate_name'}, status=409)
        return Response(_board_row(board))

    def delete(self, request: Request, pk: int) -> Response:
        from django.db.models import RestrictedError

        board = get_object_or_404(TaskBoard, pk=pk)
        try:
            with transaction.atomic():
                # Проверка ВНУТРИ транзакции: иначе между ней и удалением можно
                # успеть завести задачу, и снос стадий упрётся в FK RESTRICT.
                if Task.objects.filter(board=board).exists():
                    return Response({'error': 'has_tasks'}, status=409)
                board.stages.all().delete()
                board.delete()
        except (RestrictedError, IntegrityError):
            # Задачу успели создать между проверкой и удалением — честный 409.
            return Response({'error': 'has_tasks'}, status=409)
        return Response(status=204)


class StageCollectionView(APIView):
    permission_classes = [ReadStaffWriteAdmin]

    def get(self, request: Request, board_id: int) -> Response:
        stages = TaskStage.objects.filter(board_id=board_id).order_by('sort_order', 'id')
        return Response([_stage_row(s) for s in stages])

    def post(self, request: Request, board_id: int) -> Response:
        from apps.taskboard.serializers import StageWriteSerializer

        get_object_or_404(TaskBoard, pk=board_id)
        serializer = StageWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        last = (TaskStage.objects.filter(board_id=board_id)
                .order_by('-sort_order').values_list('sort_order', flat=True).first())
        try:
            with transaction.atomic():
                stage = TaskStage.objects.create(
                    board_id=board_id, sort_order=(last or 0) + 1, **serializer.validated_data)
        except IntegrityError:
            return Response({'error': 'duplicate_label'}, status=409)
        return Response(_stage_row(stage), status=201)


class StageDetailView(APIView):
    permission_classes = [ReadStaffWriteAdmin]

    def patch(self, request: Request, pk: int) -> Response:
        from apps.taskboard.serializers import StageWriteSerializer

        stage = get_object_or_404(TaskStage, pk=pk)
        serializer = StageWriteSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        new_category = serializer.validated_data.get('category')
        if new_category is not None:
            blocker = services.stage_category_change_blocker(stage, new_category)
            if blocker:
                return Response({'error': blocker}, status=409)
        for key, value in serializer.validated_data.items():
            setattr(stage, key, value)
        try:
            with transaction.atomic():
                stage.save()
        except IntegrityError:
            return Response({'error': 'duplicate_label'}, status=409)
        return Response(_stage_row(stage))

    def delete(self, request: Request, pk: int) -> Response:
        stage = get_object_or_404(TaskStage, pk=pk)
        blocker = services.stage_delete_blocker(stage)
        if blocker:
            return Response({'error': blocker}, status=409)
        stage.delete()
        return Response(status=204)


class StageReorderView(APIView):
    permission_classes = [ReadStaffWriteAdmin]

    def post(self, request: Request, board_id: int) -> Response:
        from apps.taskboard.serializers import StageReorderSerializer

        get_object_or_404(TaskBoard, pk=board_id)
        serializer = StageReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = serializer.validated_data['order']
        stages = {s.id: s for s in TaskStage.objects.filter(id__in=order)}
        if len(stages) != len(order):
            return Response({'error': 'unknown_stage'}, status=400)
        # Воронка приходит из адреса, а не выводится из присланных стадий. Прежде
        # её брали через boards.pop() по набору стадий, и на пустом order множество
        # оказывалось пустым — KeyError и 500 вместо 400.
        if any(s.board_id != board_id for s in stages.values()):
            return Response({'error': 'stages_from_different_boards'}, status=400)

        full = set(TaskStage.objects.filter(board_id=board_id).values_list('id', flat=True))
        if full != set(order):
            # Неполный набор раздал бы позиции 0..k-1, конфликтующие с
            # позициями нетронутых стадий — порядок на доске стал бы неопределённым.
            return Response({'error': 'incomplete_stage_set'}, status=400)
        with transaction.atomic():
            for position, stage_id in enumerate(order):
                stage = stages[stage_id]
                stage.sort_order = position
                stage.save(update_fields=['sort_order'])
        refreshed = TaskStage.objects.filter(id__in=order).order_by('sort_order', 'id')
        return Response([_stage_row(s) for s in refreshed])


class AssigneeListView(APIView):
    """
    Кто может быть исполнителем задачи.

    Отдельная ручка, а не общий /api/admin/accounts: тот закрыт суперадмином,
    а карточки ведут менеджеры — пикеру «Исполнитель» иначе неоткуда брать данные.
    Преподаватели исключены: в разделе они не работают.
    """
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        from apps.accounts.models import Account

        accounts = (Account.objects
                    .filter(is_active=True)
                    .exclude(role=Account.Role.TEACHER)
                    .order_by('full_name', 'id')
                    .values('id', 'full_name', 'role'))
        return Response(list(accounts))


