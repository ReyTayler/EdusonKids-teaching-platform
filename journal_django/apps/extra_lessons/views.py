"""
Тонкие APIView для extra_lessons.

Admin (IsManagerOrAdmin, менеджер/админ/суперадмин — явное требование фичи,
не общий ReadStaffWriteAdmin раздела lessons):
  GET  /api/admin/extra-lessons             → список {rows,total,page,page_size}
  POST /api/admin/extra-lessons             → 201 | 400 (нет такого урока, ИЛИ
                                               кто-то не был отмечен отсутствующим
                                               на нём, ИЛИ у кого-то balance<=0)
  GET  /api/admin/extra-lessons/:id         → 200 | 404
  DELETE /api/admin/extra-lessons/:id       → 204 | 404 | 409 (makeup_scheduled)
      Одно действие «удалить запись», два исхода по статусу (services.delete_resolution):
      pending — строка удаляется из БД целиком (факта и денег за ней нет);
      makeup_done/burned — откат факта (урок обратно на баланс, зарплата снимается).
      ⚠ ReadStaffWriteAdmin, а не IsManagerOrAdmin: откат двигает деньги, а
      удаление заявки безвозвратно — и то, и другое правит админ/суперадмин
      (решение 2026-07-30). Менеджеру GET по-прежнему доступен (карточка
      открывается, пункт меню виден, но неактивен — см. frontend
      permissions.canRollbackExtraLesson).
  POST /api/admin/extra-lessons/:id/cancel  → 200 | 404 | 409 (не scheduled)
  POST /api/admin/extra-lessons/:id/burn    → 200 | 404 | 409 (не pending, ИЛИ
                                               ученик на уроке не отсутствовал) |
                                               400 (balance<=0)

Teacher (IsTeacher, скоуп — своё назначение):
  GET  /api/extra-lessons/:id         → 200 | 404 (чужое = 404, не 403 — не
                                         раскрываем существование чужих назначений)
  POST /api/extra-lessons/:id/record  → 200 | 404 | 403 (чужое) | 409 (не
                                         scheduled, ИЛИ ученик на пропущенном
                                         уроке не отсутствовал) | 400 (present, но у ученика
                                         balance<=0, ИЛИ present=false — неявку
                                         оформляют «Отменой», а не записью)
"""
from __future__ import annotations

from django.db import IntegrityError
from rest_framework import status
from rest_framework.exceptions import NotFound
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import IsManagerOrAdmin, IsTeacher, ReadStaffWriteAdmin
from apps.core.utils.dates import msk_today
from apps.extra_lessons import repository, services
from apps.extra_lessons.exceptions import (
    AbsentStudentNotRecordable, DuplicateAssignment, GroupNotFound, MissedLessonNotFound,
    NotTeachersAssignment, StudentNotAbsent, StudentNotInGroup, StudentWasPresent,
)
from apps.extra_lessons.serializers import (
    ExtraLessonCreateSerializer, ExtraLessonRecordSerializer,
    ManualExtraLessonCreateSerializer,
)
from apps.lessons.exceptions import UnpaidAttendanceBlocked

# Машиночитаемый код конфликта: доп.урок за это назначение уже проведён.
# Без кода фронт не отличает повтор от настоящей ошибки и показывает красное
# сообщение там, где работа фактически сделана (деньги начислены первым
# запросом) — тот же приём, что LESSON_ALREADY_RECORDED у обычного урока.
EXTRA_LESSON_ALREADY_RECORDED = 'extra_lesson_already_recorded'

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 500


def _parse_list_params(request: Request) -> dict:
    qp = request.query_params
    try:
        page = max(1, int(qp.get('page') or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        raw_page_size = int(qp.get('page_size') or 0)
    except (TypeError, ValueError):
        raw_page_size = 0
    page_size = min(_MAX_PAGE_SIZE, max(1, raw_page_size or _DEFAULT_PAGE_SIZE))
    # Пустое значение осмысленно: repository трактует его как порядок очереди
    # разбора. Дублировать здесь имя колонки не надо — дефолт живёт в одном месте.
    sort_by = qp.get('sort_by') or ''
    sort_dir = qp.get('sort_dir')
    if sort_dir not in ('asc', 'desc'):
        sort_dir = 'desc'
    filters = {}
    if qp.get('status'):
        filters['status'] = qp['status']
    if qp.get('teacher_id'):
        filters['teacher_id'] = qp['teacher_id']
    if qp.get('student_name'):
        filters['student_name'] = qp['student_name']
    if qp.get('missed_lesson_group_name'):
        filters['missed_lesson_group_name'] = qp['missed_lesson_group_name']
    return {'page': page, 'page_size': page_size, 'sort_by': sort_by, 'sort_dir': sort_dir, 'filters': filters}


class ExtraLessonListCreateView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        params = _parse_list_params(request)
        return Response(services.list_assignments(**params))

    def post(self, request: Request) -> Response:
        serializer = ExtraLessonCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.create_assignment(serializer.validated_data, request)
        except (MissedLessonNotFound, StudentNotAbsent, UnpaidAttendanceBlocked) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except DuplicateAssignment as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except IntegrityError:
            # Бэкстоп для гонки двух одновременных create на один (пропуск×ученик):
            # частичный uniq-constraint не даст задвоить активную резолюцию —
            # переводим в 409, а не 500 (сервисный guard закрывает обычный путь).
            return Response(
                {'error': 'Доп.урок для этого ученика за этот пропуск уже назначен.'},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(result, status=status.HTTP_201_CREATED)


class ExtraLessonManualCreateView(APIView):
    """POST /api/admin/extra-lessons/manual — назначить доп.урок СВЕРХ курса
    (kind='extra') вручную одному/нескольким ученикам группы. 201 |
    400 (нет группы / кто-то не в группе / у кого-то balance<=0)."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request) -> Response:
        serializer = ManualExtraLessonCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.create_extra_assignment(serializer.validated_data, request)
        except (GroupNotFound, StudentNotInGroup, StudentWasPresent, UnpaidAttendanceBlocked) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except DuplicateAssignment as e:
            # Уже есть активный доп.урок за этот урок у ученика (в т.ч. по авто-заявке).
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        return Response(result, status=status.HTTP_201_CREATED)


class ExtraLessonPendingCountView(APIView):
    """GET /api/admin/extra-lessons/pending-count → {count} — необработанные
    пропуски (pending) для бейджа в сайдбаре. Лёгкий COUNT, без пагинации."""

    permission_classes = [IsManagerOrAdmin]

    def get(self, request: Request) -> Response:
        return Response({'count': repository.pending_count()})


class ExtraLessonDetailView(APIView):
    # GET — весь staff; DELETE (удаление заявки «Ждёт решения» ЛИБО откат факта:
    # проведённый доп.урок или сгорание) — только админ/суперадмин: откат двигает
    # баланс ученика и зарплату, а удаление заявки необратимо.
    permission_classes = [ReadStaffWriteAdmin]

    def get(self, request: Request, pk: int) -> Response:
        full = repository.get_resolution_full(pk)
        if full is None:
            raise NotFound({'error': 'Not found'})
        return Response(full)

    def delete(self, request: Request, pk: int) -> Response:
        try:
            ok = services.delete_resolution(pk, request)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if not ok:
            raise NotFound({'error': 'Not found'})
        return Response(status=status.HTTP_204_NO_CONTENT)


class ExtraLessonCancelView(APIView):
    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            result = services.cancel_assignment(pk, request)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class ExtraLessonBurnView(APIView):
    """POST /api/admin/extra-lessons/:id/burn — «Сжечь» нерешённый пропуск
    (pending → burned): создаёт burned-урок present=true, флет 200₽, списывает
    урок с баланса. 200 | 404 | 409 (не pending) | 400 (balance<=0)."""

    permission_classes = [IsManagerOrAdmin]

    def post(self, request: Request, pk: int) -> Response:
        try:
            result = services.burn(pk, request=request, burn_date=msk_today())
        except UnpaidAttendanceBlocked as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except StudentNotAbsent as e:
            # Ученик на том уроке отмечен присутствовавшим — пропуска нет, жечь нечего.
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except ValueError as e:
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class TeacherExtraLessonDetailView(APIView):
    permission_classes = [IsTeacher]

    def get(self, request: Request, pk: int) -> Response:
        result = services.get_assignment_for_teacher(pk, request.user.teacher_id)
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)


class TeacherExtraLessonRecordView(APIView):
    permission_classes = [IsTeacher]

    def post(self, request: Request, pk: int) -> Response:
        serializer = ExtraLessonRecordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        v = serializer.validated_data
        try:
            result = services.record(
                pk,
                teacher_id=request.user.teacher_id,
                present=v['present'],
                record_url=v.get('record_url') or None,
                submitted_by_token=f'acct:{request.user.id}',
                submit_date=msk_today(),
                request=request,
            )
        except NotTeachersAssignment as e:
            return Response({'error': str(e)}, status=status.HTTP_403_FORBIDDEN)
        except (AbsentStudentNotRecordable, UnpaidAttendanceBlocked) as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except StudentNotAbsent as e:
            # Пропуска нет (ученик отмечен присутствовавшим) — не путать с веткой
            # ValueError ниже: там код «уже проведено», здесь настоящий отказ.
            return Response({'error': str(e)}, status=status.HTTP_409_CONFLICT)
        except ValueError as e:
            # Статус резолюции уже не «назначен» — почти всегда повторная отправка
            # после потерянного ответа: доп.урок записан, деньги начислены. Код
            # нужен, чтобы фронт сказал «уже отмечено» спокойно и обновил экран,
            # а не пугал красной ошибкой на месте фактического успеха.
            return Response(
                {'error': str(e), 'code': EXTRA_LESSON_ALREADY_RECORDED},
                status=status.HTTP_409_CONFLICT,
            )
        if result is None:
            raise NotFound({'error': 'Not found'})
        return Response(result)
