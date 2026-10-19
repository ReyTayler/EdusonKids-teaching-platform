"""
ChangelogMiddleware — открывает pghistory-контекст на мутирующих запросах.

Переопределяем get_context: базовый HistoryMiddleware читает request.user,
которого в этом проекте на этапе middleware НЕТ (нет AuthenticationMiddleware,
DRF аутентифицирует лениво внутри вьюхи через CookieJWTAuthentication).
Данные пользователя дописывает CookieJWTAuthentication.authenticate()
вызовом pghistory.context(...) (см. apps/core/authentication.py).
"""
from __future__ import annotations

import pghistory.middleware


class ChangelogMiddleware(pghistory.middleware.HistoryMiddleware):
    def get_context(self, request):
        return {
            'url': request.path,
            'method': request.method,
        }
