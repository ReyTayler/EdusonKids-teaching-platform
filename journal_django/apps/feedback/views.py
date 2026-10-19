"""
Admin API отзывов по урокам.

  GET /api/admin/lesson-feedback — лента отзывов, пагинация StandardPagination.
      Фильтры: group_id, teacher_id, lesson_id (целые > 0; мусор → 400).

Права: manager/admin/superadmin. Записи через API на этапе 1 нет — отзывы
приходят импортом, форма заполнения преподавателем появится позже.
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
