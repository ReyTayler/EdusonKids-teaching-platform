"""ChangelogService — тонкий слой между views и repository (+ откат)."""
from __future__ import annotations

from typing import Optional

from rest_framework.request import Request

from apps.changelog import labels, repository
from apps.changelog import revert as revert_module


def list_operations(page: int = 1, page_size: int = 50, filters: dict | None = None,
                    role: str | None = None) -> dict:
    data = repository.list_operations(
        page=page, page_size=page_size, filters=filters or {},
    )
    if role != 'superadmin':
        # Мотивацию откатывает только superadmin — admin кнопку «Откатить» не видит.
        for row in data['rows']:
            if labels.is_motivation_operation(row['operation']):
                row['revertable'] = False
    return data


def get_operation(context_id) -> dict | None:
    return repository.get_operation(context_id)


def revert_operation(context_id, request: Optional[Request] = None) -> dict:
    """Откатить операцию.

    В журнал ИБ не пишем: сам откат — доменное действие и он фиксируется в
    «Журнале изменений» собственной операцией 'changelog.revert'
    (metadata.operation, см. repository._operation_of)."""
    role = getattr(getattr(request, 'user', None), 'role', None)
    return revert_module.revert_context(context_id, role=role)
