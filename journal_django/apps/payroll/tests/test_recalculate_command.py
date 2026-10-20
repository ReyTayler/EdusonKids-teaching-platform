"""
Команда recalculate_payroll: фильтры, --dry-run, итог без ПДн.
Мини-школа и помощники — conftest приложения.
"""
from __future__ import annotations

import datetime
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.payroll.tests.conftest import db_lesson, db_marker, db_version


def _run(*args) -> str:
    out = StringIO()
    call_command('recalculate_payroll', *args, stdout=out)
    return out.getvalue()


def test_dry_run_reports_but_saves_nothing(db_school):
    from apps.payroll.models import CancellationPay, Payroll
    db_version(datetime.date(2026, 6, 1))
    lesson = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    db_marker(db_school, datetime.date(2026, 6, 17))
    gid = str(db_school['group'].id)

    out = _run('--group', gid, '--dry-run')
    assert 'DRY-RUN' in out
    assert 'Уроков: 1, отмен: 1' in out
    assert 'создано 2' in out
    assert not Payroll.objects.filter(lesson=lesson).exists()
    assert not CancellationPay.objects.filter(group=db_school['group']).exists()

    out = _run('--group', gid)
    assert 'DRY-RUN' not in out
    assert Payroll.objects.get(lesson=lesson).payment == Decimal('600.00')
    assert CancellationPay.objects.filter(group=db_school['group']).count() == 1
    # Повторный прогон — менять нечего.
    assert 'Изменено начислений: 0' in _run('--group', gid)


def test_date_range_and_several_groups(db_school):
    from apps.groups.models import Group
    from apps.payroll.models import Payroll
    db_version(datetime.date(2026, 6, 1))
    a = db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    b = db_lesson(db_school, datetime.date(2026, 6, 20), 2)
    other = Group.objects.create(
        name='Тест Движок Группа 2', direction=db_school['direction'], teacher=db_school['t1'],
        is_individual=True, lesson_duration_minutes=90, active=True,
        created_at=db_school['group'].created_at)
    c = db_lesson({**db_school, 'group': other}, datetime.date(2026, 6, 15), 1)

    _run('--group', str(db_school['group'].id), '--group', str(other.id),
         '--from', '2026-06-11', '--to', '2026-06-30')
    done = set(Payroll.objects.filter(lesson__in=[a, b, c]).values_list('lesson_id', flat=True))
    assert done == {b.id, c.id}
    assert Payroll.objects.get(lesson=c).payment == Decimal('800.00')  # индивидуальный


def test_output_has_no_personal_data(db_school):
    db_version(datetime.date(2026, 6, 1))
    db_lesson(db_school, datetime.date(2026, 6, 10), 1)
    out = _run('--group', str(db_school['group'].id))
    assert 'Тест Движок' not in out


@pytest.mark.parametrize('args', [
    ('--from', '2026-13-01'),
    ('--to', 'вчера'),
    ('--from', '2026-07-01', '--to', '2026-06-01'),
])
def test_bad_dates_rejected(db, args):
    with pytest.raises(CommandError):
        _run(*args)
