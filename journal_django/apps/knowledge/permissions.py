"""
Права раздела «База знаний».

Класс отвечает только на вопрос «пускать ли в эндпоинт». Вопрос «какие строки
показать» решает repository.visible_documents_qs — иначе фильтрация расползётся
по вьюхам и рано или поздно где-то забудется.
"""
from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView

# Читать раздел может любая аутентифицированная роль. teacher включён заранее:
# читалка в teacher SPA — следующий этап, и бэкенд к нему уже готов.
READ_ROLES = ('teacher', 'manager', 'admin', 'superadmin')
WRITE_ROLES = ('admin', 'superadmin')


class KnowledgeReadStaffWriteAdmin(BasePermission):
    """SAFE-методы — любая аутентифицированная роль; мутации — admin/superadmin."""

    message = 'Read for authenticated staff; write for admin or superadmin.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        # UNAUTHENTICATED_USER=None → без токена request.user is None.
        if not (user and user.is_authenticated):
            return False
        if request.method in SAFE_METHODS:
            return user.role in READ_ROLES
        return user.role in WRITE_ROLES


class KnowledgePersonalMark(BasePermission):
    """
    Личная пометка сотрудника на документе — звёздочка «в избранном».

    Отдельный класс, а не общий KnowledgeReadStaffWriteAdmin: тот запрещает
    мутации всем, кроме администраторов, и правильно делает — он про
    содержимое документа. Избранное же ничего в документе не меняет, это
    закладка читателя, и запирать её за правами на правку бессмысленно:
    преподаватель не смог бы отметить регламент, который ему же и адресован.

    Видимость самого документа проверяет вьюха (404 на недоступный) — здесь
    только роль.
    """

    message = 'Read for authenticated staff.'

    def has_permission(self, request: Request, view: APIView) -> bool:
        user = request.user
        if not (user and user.is_authenticated):
            return False
        return user.role in READ_ROLES
