"""
URL маршруты для раздела groups.

Монтируются в config/urls.py как:
  path('api/admin/groups', include('apps.groups.urls'))

APPEND_SLASH=False — пути без trailing slash (зеркало Express/Nest).
"""
from django.urls import path

from apps.groups.views import (
    GroupDetailView, GroupLessonSkipView, GroupListCreateView, GroupProgressView,
    GroupScheduleChangeView, GroupScheduleView,
)
from apps.scheduling.views import (
    GroupPlanCancelView, GroupPlanChangeTeacherPermanentView,
    GroupPlanChangeTeacherView, GroupPlanGenerateView, GroupPlanHealthView,
    GroupPlanPermanentChangeView, GroupPlanRescheduleView, GroupPlanResyncView,
    GroupPlanView,
)

urlpatterns = [
    path('', GroupListCreateView.as_view(), name='groups-list-create'),
    path('/<int:pk>', GroupDetailView.as_view(), name='groups-detail'),
    # Прогресс: обзорная матрица посещаемости (слоты × ученики). Read-only.
    # Стоит ДО teacher-guard /api → RBAC IsManagerOrAdmin.
    path('/<int:pk>/progress', GroupProgressView.as_view(), name='groups-progress'),
    # Пометки «неоплачиваемый пропуск» на слоты (в т.ч. будущие) — Вариант A.
    path('/<int:pk>/lesson-skips', GroupLessonSkipView.as_view(), name='groups-lesson-skips'),
    # Расписание (Ф3): версионные слоты
    path('/<int:pk>/schedule', GroupScheduleView.as_view(), name='groups-schedule'),
    path('/<int:pk>/schedule-change', GroupScheduleChangeView.as_view(), name='groups-schedule-change'),
    # План занятий (materialize-on-write, planned_lessons). Смонтирован под
    # /api/admin/groups (стоит ДО teacher-guard /api) → RBAC IsManagerOrAdmin.
    # Заглушки 501; бизнес-логика — шаги 2/4. Числовой <lid> не конфликтует со
    # строковыми generate/permanent-change (int-конвертер их не матчит).
    path('/<int:pk>/plan', GroupPlanView.as_view(), name='groups-plan'),
    path('/<int:pk>/plan/generate', GroupPlanGenerateView.as_view(), name='groups-plan-generate'),
    path('/<int:pk>/plan/permanent-change', GroupPlanPermanentChangeView.as_view(), name='groups-plan-permanent-change'),
    path('/<int:pk>/plan/change-teacher-permanent', GroupPlanChangeTeacherPermanentView.as_view(), name='groups-plan-change-teacher-permanent'),
    # Здоровье плана и его починка (RBAC IsSuperAdmin, в отличие от остальных
    # /plan/* с IsManagerOrAdmin). Строковые health/resync не конфликтуют с
    # /plan/<int:lid>/... — int-конвертер их не матчит.
    path('/<int:pk>/plan/health', GroupPlanHealthView.as_view(), name='groups-plan-health'),
    path('/<int:pk>/plan/resync', GroupPlanResyncView.as_view(), name='groups-plan-resync'),
    path('/<int:pk>/plan/<int:lid>/reschedule', GroupPlanRescheduleView.as_view(), name='groups-plan-reschedule'),
    path('/<int:pk>/plan/<int:lid>/change-teacher', GroupPlanChangeTeacherView.as_view(), name='groups-plan-change-teacher'),
    path('/<int:pk>/plan/<int:lid>/cancel', GroupPlanCancelView.as_view(), name='groups-plan-cancel'),
]
