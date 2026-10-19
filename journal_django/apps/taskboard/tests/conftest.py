"""Фикстуры taskboard: реальная воронка со стадиями, убираем в teardown."""
from __future__ import annotations

import pytest

from apps.taskboard.models import Task, TaskActivity, TaskBoard, TaskStage


@pytest.fixture
def board(db):
    """Воронка с тремя стадиями: две открытых, одна закрытая."""
    b = TaskBoard.objects.create(name='__tb_fixture_board__')
    stages = {
        'new': TaskStage.objects.create(board=b, label='Новая', sort_order=0, category='open'),
        'work': TaskStage.objects.create(board=b, label='В работе', sort_order=1, category='open'),
        'done': TaskStage.objects.create(board=b, label='Готово', sort_order=2, category='closed'),
    }
    yield b, stages
    TaskActivity.objects.filter(task__board=b).delete()
    Task.objects.filter(board=b).delete()
    TaskStage.objects.filter(board=b).delete()
    b.delete()


@pytest.fixture
def admin_account_id(db):
    """Реальная учётка admin — для проверок смены исполнителя."""
    from django.contrib.auth.hashers import make_password

    from apps.accounts.models import Account

    account = Account.objects.create(
        email='__tb_admin__@example.com',
        password=make_password('testpass_sentinel'),
        role='admin',
        first_name='Тестовый',
        last_name='админ',
    )
    yield account.id
    account.delete()


@pytest.fixture
def manager_account_id(db):
    """Вторая реальная учётка — нужна там, где исполнителей несколько."""
    from django.contrib.auth.hashers import make_password

    from apps.accounts.models import Account

    account = Account.objects.create(
        email='__tb_manager__@example.com',
        password=make_password('testpass_sentinel'),
        role='manager',
        first_name='Второй',
        last_name='исполнитель',
        full_name='Второй исполнитель',
    )
    yield account.id
    account.delete()
