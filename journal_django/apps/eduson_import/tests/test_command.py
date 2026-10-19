"""manage.py import_eduson — одна транзакция, контекст журнала, отказ на непустой
базе, откат при ошибке данных и при несошедшейся сверке, пробный прогон, сброс
кэшей после коммита."""
from __future__ import annotations

import io
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.accounts.models import Account
from apps.directions.models import Direction
from apps.eduson_import.tests.factory import EdusonDB
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group
from apps.lessons.models import Lesson
from apps.renewals.models import RenewalDeal
from apps.students.models import Student
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

AS_OF = '2026-10-18'
CMD = 'apps.eduson_import.management.commands.import_eduson'


def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'okk.db')
    db.teacher(1, 'Анна Тестова')
    db.user('anna@example.com', 'teacher', teacher_id=1)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001', notes='любит роботов')
    db.participant(5, 'Маша', 'Мама Маши', '79990000002')
    db.session(101, 5, '2026-07-06')
    db.session(102, 5, '2026-07-13', type='makeup', duration=45)
    db.session(103, 5, '2026-07-20', status='cancelled')
    db.session(104, 5, '2030-01-07', status='scheduled')
    db.report(101, 5, 1, comment='ок')
    db.renewal(5, 'paused', note='каникулы')
    return db


def _call(path, *args) -> str:
    out = io.StringIO()
    call_command('import_eduson', str(path), '--as-of', AS_OF, *args, stdout=out)
    return out.getvalue()


def _empty() -> bool:
    return not any(m.objects.exists() for m in (Student, Group, Account, Teacher, Direction, Lesson,
                                                LessonFeedback, RenewalDeal))


def test_import_success(tmp_path, django_capture_on_commit_callbacks):
    path = _db(tmp_path).close()
    with mock.patch(f'{CMD}.invalidate_finance_cache') as fin, \
            mock.patch(f'{CMD}.invalidate_registry_cache') as reg, \
            django_capture_on_commit_callbacks(execute=True):
        out = _call(path)
    assert 'Импорт Eduson завершён' in out
    assert 'группы' in out and 'проведённые уроки' in out          # таблица сверки
    assert (Group.objects.count(), Student.objects.count(), Lesson.objects.count()) == (1, 2, 2)
    assert LessonFeedback.objects.count() == 1 and RenewalDeal.objects.filter(stage__key='frozen').count() == 2
    # Кэши дашборда сброшены после коммита: bulk_create не шлёт post_save.
    fin.assert_called_once_with()
    reg.assert_called_once_with()
    # Без ПДн в выводе.
    for secret in ('Миша', 'Маша', 'Мама', '7999', 'example.com', 'роботов', 'каникулы'):
        assert secret not in out


def test_changes_logged_as_one_import_operation(tmp_path):
    import pghistory.models

    _call(_db(tmp_path).close())
    contexts = pghistory.models.Context.objects.filter(metadata__operation='eduson.import')
    assert contexts.count() == 1
    ctx = contexts.get()
    assert ctx.metadata['method'] == 'COMMAND' and ctx.metadata['url'] == 'manual/import-eduson'
    assert pghistory.models.Events.objects.filter(pgh_context_id=ctx.id).count() > 10
    from apps.changelog import labels
    assert labels.resolve_operation('COMMAND', 'manual/import-eduson') == 'eduson.import'


def test_second_run_refused(tmp_path):
    path = _db(tmp_path).close()
    _call(path)
    with pytest.raises(CommandError, match='База не пустая'):
        _call(path)


@pytest.mark.parametrize('model', ['account', 'direction'])
def test_any_reference_data_blocks_import(tmp_path, model):
    if model == 'account':
        Account.objects.create(email='x@example.org', role='admin')
    else:
        Direction.objects.create(name='Тест')
    with pytest.raises(CommandError, match='База не пустая'):
        _call(_db(tmp_path).close())


def test_broken_data_rolls_back_everything(tmp_path):
    db = _db(tmp_path)
    db.participant(77, 'Сирота', 'Кто-то', '79990000099')            # группы #77 нет
    with pytest.raises(CommandError) as exc:
        _call(db.close())
    assert '#77' in str(exc.value) and 'Сирота' not in str(exc.value)
    assert _empty()


def test_integrity_error_becomes_command_error_without_details(tmp_path):
    from django.db import IntegrityError

    with mock.patch(f'{CMD}.import_feedback', side_effect=IntegrityError('Key (email)=(a@b.c) already exists')):
        with pytest.raises(CommandError) as exc:
            _call(_db(tmp_path).close())
    assert 'отзывы' in str(exc.value) and 'a@b.c' not in str(exc.value)
    assert _empty()


def test_reconcile_errors_roll_back(tmp_path):
    with mock.patch(f'{CMD}.reconcile', return_value=(['таблица'], ['группа ПИ1001: расхождение'], [])):
        with pytest.raises(CommandError, match='Сверка не сошлась, всё откатано'):
            _call(_db(tmp_path).close())
    assert _empty()


def test_dry_run_leaves_db_empty(tmp_path, django_capture_on_commit_callbacks):
    with mock.patch(f'{CMD}.invalidate_finance_cache') as fin, \
            django_capture_on_commit_callbacks(execute=True):
        out = _call(_db(tmp_path).close(), '--dry-run')
    assert 'проведённые уроки' in out and 'Пробный прогон' in out
    assert _empty()
    fin.assert_not_called()


def test_missing_file_and_bad_date(tmp_path):
    with pytest.raises(CommandError, match='не найдена'):
        _call(tmp_path / 'nope.db')
    with pytest.raises(CommandError, match='ГГГГ-ММ-ДД'):
        call_command('import_eduson', str(_db(tmp_path).close()), '--as-of', '18.10.2026', stdout=io.StringIO())


def test_data_error_hides_field_values(tmp_path):
    from django.db import DataError

    err = DataError('value too long for type character varying(10): "Миша Иванов 79990000001"')
    with mock.patch(f'{CMD}.import_groups', side_effect=err):
        with pytest.raises(CommandError) as exc:
            _call(_db(tmp_path).close())
    text = str(exc.value)
    assert 'DataError' in text and 'группы и ученики' in text
    assert 'Миша' not in text and '7999' not in text
    assert _empty()


def test_reconcile_crash_becomes_command_error(tmp_path):
    with mock.patch(f'{CMD}.reconcile', side_effect=KeyError(5)):
        with pytest.raises(CommandError, match='сверка'):
            _call(_db(tmp_path).close())
    assert _empty()


def test_as_of_in_future_refused(tmp_path):
    import datetime as dt

    from apps.core.utils.dates import msk_today

    tomorrow = (dt.date.fromisoformat(msk_today()) + dt.timedelta(days=1)).isoformat()
    with pytest.raises(CommandError, match='позже сегодняшнего'):
        call_command('import_eduson', str(_db(tmp_path).close()), '--as-of', tomorrow, stdout=io.StringIO())
    assert _empty()


def _db_with_admins(tmp_path) -> EdusonDB:
    db = _db(tmp_path)
    db.teacher(13, 'Админ Тестов', role='admin')
    db.user('boss@example.org', 'admin', teacher_id=13)
    db.user('helper@example.org', 'admin')
    return db


def test_superadmin_assigned(tmp_path):
    out = _call(_db_with_admins(tmp_path).close(), '--superadmin', '  BOSS@Example.org ')
    roles = dict(Account.objects.values_list('email', 'role'))
    assert roles == {'anna@example.com': 'teacher', 'boss@example.org': 'superadmin',
                     'helper@example.org': 'admin'}
    assert Account.objects.get(email='boss@example.org').teacher_id is None
    assert 'нет суперадмина' not in out and 'boss@' not in out


def test_superadmin_unknown_email_refused(tmp_path):
    with pytest.raises(CommandError, match='--superadmin №1') as exc:
        _call(_db_with_admins(tmp_path).close(), '--superadmin', 'nobody@example.org')
    assert 'nobody' not in str(exc.value)
    assert _empty()


def test_superadmin_teacher_account_refused(tmp_path):
    db = _db_with_admins(tmp_path)
    db.teacher(6, 'Ведущий Админ', role='admin')
    db.user('lead@example.org', 'admin', teacher_id=6)
    db.group(9, 'ПИ1009', 'python', 6)                     # admin ведёт группу → переносится teacher
    with pytest.raises(CommandError, match='№2.*преподавателем') as exc:
        _call(db.close(), '--superadmin', 'boss@example.org', '--superadmin', 'lead@example.org')
    assert 'lead' not in str(exc.value) and 'example' not in str(exc.value)
    assert _empty()
    with pytest.raises(CommandError, match=r'не администратор \(роль teacher\)') as exc:
        _call(db.path, '--superadmin', 'anna@example.com')    # учётка роли teacher
    assert 'anna' not in str(exc.value)


def test_superadmin_disabled_account_refused(tmp_path):
    db = _db_with_admins(tmp_path)
    db.user('gone@example.org', 'admin', active=0)
    with pytest.raises(CommandError, match='отключена в Eduson') as exc:
        _call(db.close(), '--superadmin', 'gone@example.org')
    assert 'gone' not in str(exc.value)
    assert _empty()


def test_without_superadmin_warns(tmp_path):
    out = _call(_db_with_admins(tmp_path).close())
    assert not Account.objects.filter(role='superadmin').exists()
    assert 'нет суперадмина — управлять учётками будет некому' in out
