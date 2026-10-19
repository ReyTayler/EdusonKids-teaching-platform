"""
URL configuration for journal_django project.

Phase 0: core health endpoint.
Phase 1: groups CRUD (/api/admin/groups).
Phase 2: simple reference sections (teachers, directions, discounts, settings, audit).
"""
from django.urls import include, path

from apps.core.views import HealthView

urlpatterns = [
    path('health', HealthView.as_view(), name='health'),
    # Phase 7 — auth (/api/auth/* — ПЕРВЫМ, как в Express: /api/auth → /api/admin → /api)
    path('api/auth', include('apps.auth_app.urls')),
    # Phase 1 — groups
    path('api/admin/groups', include('apps.groups.urls')),
    # Phase 2 — simple reference sections
    path('api/admin/teachers', include('apps.teachers.urls')),
    path('api/admin/directions', include('apps.directions.urls')),
    path('api/admin/discounts', include('apps.discounts.urls')),
    path('api/admin/audit-log', include('apps.audit.urls')),
    path('api/admin/changelog', include('apps.changelog.urls')),
    # Phase 3 — students
    path('api/admin/students', include('apps.students.urls')),
    # Phase 4 — memberships
    path('api/admin/memberships', include('apps.memberships.urls')),
    # Phase 5 — payments
    path('api/admin/payments', include('apps.payments.urls')),
    # Phase 6 — lessons + attendance
    path('api/admin/lessons', include('apps.lessons.urls')),
    # Phase 7 — payroll
    path('api/admin/payroll', include('apps.payroll.urls')),
    # Доп.уроки (компенсация пропусков) — admin CRUD
    path('api/admin/extra-lessons', include('apps.extra_lessons.urls')),
    # Отзывы преподавателей по урокам (Eduson) — чтение для manager/admin
    path('api/admin/lesson-feedback', include('apps.feedback.urls')),
    # Phase 8 — dashboard (FIFO read-model)
    path('api/admin/dashboard', include('apps.dashboard.urls')),
    # Реестр куратора — операционный список активных учеников (вкладка дашборда)
    path('api/admin/registry', include('apps.dashboard.registry_urls')),
    # Phase 9 — accounts (admin-only RBAC)
    path('api/admin/accounts', include('apps.accounts.urls')),
    # Продления — CRM-воронка продлений (/api/admin/renewals, role=manager/admin)
    path('api/admin/renewals', include('apps.renewals.urls')),
    # Задачи — воронки/стадии/карточки (спека 2026-08-24, role=manager/admin)
    path('api/admin/tasks', include('apps.taskboard.urls')),
    # Календарь (админ, произвольный преподаватель) — /api/admin/calendar
    path('api/admin/calendar', include('apps.scheduling.admin_urls')),
    # Синхро — ручной запуск служебных пересборок по данным БД (только superadmin)
    path('api/admin/sync', include('apps.sync.urls')),
    # Отчёты — генерация Excel-отчётов в Celery (role=manager/admin)
    path('api/admin/reports', include('apps.reports.urls')),
    # База знаний — документация компании (спека 2026-08-06).
    # Чтение — любая аутентифицированная роль, запись — admin/superadmin.
    path('api/admin/knowledge', include('apps.knowledge.urls')),
    # Telegram-уведомления: справочник аккаунтов (manager/admin) и раздел
    # «Уведомления» — журнал доставки + расписание (только admin/superadmin).
    path('api/admin/telegram-users', include('apps.notifications.telegram_users_urls')),
    path('api/admin/notifications', include('apps.notifications.urls')),
    # Служебный API Telegram-бота (закрыт секретом + nginx allow 127.0.0.1).
    # ОБЯЗАН стоять ДО общего path('api', ...) ниже — иначе перехватится им.
    path('api/integrations/telegram', include('apps.notifications.integration_urls')),
    # Phase 10 — teacher SPA (/api, после /api/admin — admin стоит выше, как в Express)
    path('api', include('apps.teacher_spa.urls')),
    # Планирование занятий — календарь плановых occurrences (/api/calendar, role=teacher)
    path('api', include('apps.scheduling.urls')),
    # Доп.уроки — фиксация проведения преподавателем (/api/extra-lessons, role=teacher)
    path('api/extra-lessons', include('apps.extra_lessons.teacher_urls')),
    # Своя зарплата за месяц с расшифровкой (/api/my-payroll, role=teacher)
    path('api/my-payroll', include('apps.payroll.teacher_urls')),
]

# ---------------------------------------------------------------------------
# Статику фронтенда раздаёт nginx — и в проде, и локально (dev/prod parity):
#   - прод:     конфиг nginx в deploy/ (появится на этапе развёртывания Eduson)
#   - локально:  deploy/nginx/local/nginx.conf (Windows) → проксирует /api на runserver
# Django сам статику не отдаёт. См. deploy/README.md (раздел «Локальный запуск»).
# ---------------------------------------------------------------------------
