"""
Admin API отзывов по урокам.

  GET /api/admin/lesson-feedback — лента отзывов, пагинация StandardPagination.
      Фильтры: group_id, teacher_id, lesson_id (целые > 0), date_from/date_to
      (YYYY-MM-DD, по дате урока, включительно); мусор → 400. Отзывы группы
      (карточка группы) — та же лента с group_id, отдельный эндпоинт не нужен.

Права: manager/admin/superadmin. Отдельной записи отзыва через admin API нет:
отзыв пишет преподаватель вместе с уроком (POST /api/submitLesson → блок
feedback, apps.lessons.services.record_lesson), исторические — импорт.
"""
from __future__ import annotations

from rest_framework import generics

from apps.core.pagination import StandardPagination
from apps.core.permissions import IsManagerOrAdmin
from apps.feedback import repository
from apps.feedback.serializers import LessonFeedbackFilterSerializer, LessonFeedbackSerializer


class LessonFeedbackListView(generics.ListAPIView):
    permission_classes = [IsManagerOrAdmin]
    pagination_class = StandardPagination
    serializer_class = LessonFeedbackSerializer

    def get_queryset(self):
        filters = LessonFeedbackFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        return repository.list_feedback(**filters.validated_data)
