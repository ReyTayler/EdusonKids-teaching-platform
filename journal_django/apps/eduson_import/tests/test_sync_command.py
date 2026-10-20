"""manage.py sync_eduson — мягкое обновление поверх базы, собранной import_eduson:
id групп и учеников не меняются, новое приходит, уроки групп старой системы
заменяются, продления/учётки/записи платформы не трогаются, ошибка — откат всего."""
from __future__ import annotations

import datetime
import io
from unittest import mock

import pghistory.models
import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db.models import Count

from apps.accounts.models import Account
from apps.eduson_import.models import EdusonLink
from apps.eduson_import.tests.factory import EdusonDB
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group, GroupScheduleSlot
from apps.lessons.models import Lesson
from apps.memberships.models import GroupMembership
from apps.renewals.models import RenewalDeal
from apps.scheduling.models import PlannedLesson
from apps.students.models import Student, StudentComment
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

AS_OF = '2026-10-18'
SYNC = 'apps.eduson_import.management.commands.sync_eduson'


@pytest.fixture
def db(tmp_path):
    """Старая система с одной группой; база платформы собрана полным импортом.
    Соединение фабрики остаётся открытым: тест меняет «прод» и запускает обновление."""
    db = EdusonDB(tmp_path / 'okk.db')
    db.teacher(1, 'Анна Тестова')
    db.user('anna@example.com', 'teacher', teacher_id=1)
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001', id=11, notes='любит роботов')
    db.participant(5, 'Маша', 'Мама Маши', '79990000002', id=12)
    db.session(101, 5, '2026-07-06')
    db.session(102, 5, '2026-07-13', status='cancelled')
    db.session(103, 5, '2030-01-07', status='scheduled')
    db.report(101, 5, 1, comment='ок')
    db.renewal(5, 'paused')
    db.commit()
    call_command('import_eduson', str(db.path), '--as-of', AS_OF, stdout=io.StringIO())
    yield db
    db.con.close()


def _sync(db, *args) -> str:
    db.commit()
    out = io.StringIO()
    call_command('sync_eduson', str(db.path), '--as-of', AS_OF, *args, stdout=out)
    return out.getvalue()


def _group(code='ПИ1001') -> Group:
    return Group.objects.get(name=code)


def test_import_writes_links(db):
    kinds = dict(EdusonLink.objects.values_list('kind').annotate(n=Count('id')))
    assert kinds == {'direction': 3, 'teacher': 1, 'account': 1, 'group': 1, 'participant': 2}
    assert EdusonLink.objects.get(kind='group', source_id='5').object_id == _group().id


def test_resync_without_changes_keeps_ids_and_numbers(db):
    group_id = _group().id
    students = sorted(Student.objects.values_list('id', flat=True))
    deals = sorted(RenewalDeal.objects.values_list('id', 'stage_id'))
    counts = (Lesson.objects.count(), PlannedLesson.objects.count(), LessonFeedback.objects.count(),
              GroupMembership.objects.count(), StudentComment.objects.count())

    out = _sync(db)

    assert 'Мягкое обновление Eduson завершено' in out
    assert _group().id == group_id
    assert sorted(Student.objects.values_list('id', flat=True)) == students
    assert sorted(RenewalDeal.objects.values_list('id', 'stage_id')) == deals
    assert (Lesson.objects.count(), PlannedLesson.objects.count(), LessonFeedback.objects.count(),
            GroupMembership.objects.count(), StudentComment.objects.count()) == counts
    for secret in ('Миша', 'Маша', 'Мама', '7999', 'example.com', 'роботов'):
        assert secret not in out


def test_new_data_from_old_system_arrives(db):
    db.session(104, 5, '2026-07-20')
    db.report(104, 5, 1, comment='тоже ок')
    db.participant(5, 'Петя', 'Папа Пети', '79990000003', id=13)
    db.teacher(2, 'Борис Новый')
    db.user('boris@example.com', 'teacher', teacher_id=2)
    db.group(6, 'СИ1002', 'scratch', 2, fmt='individual')
    db.participant(6, 'Оля', 'Мама Оли', '79990000004', id=14)
    db.session(201, 6, '2026-07-21')

    _sync(db)

    assert Lesson.objects.filter(group=_group()).count() == 2
    assert LessonFeedback.objects.count() == 2
    assert GroupMembership.objects.filter(group=_group()).count() == 3
    new = _group('СИ1002')
    assert new.teacher.name == 'Борис Новый' and new.is_individual
    assert Lesson.objects.filter(group=new).count() == 1
    assert Account.objects.filter(email='boris@example.com', role='teacher').exists()
    assert Student.objects.count() == 4
    # Новые ученики — с комментариями (возраст), как в импорте; у старых не задвоились.
    assert StudentComment.objects.filter(student__full_name='Миша').count() == 2


def test_platform_edits_to_lessons_are_replaced(db):
    lesson = Lesson.objects.get(group=_group())
    Lesson.objects.filter(id=lesson.id).update(record_url='https://example.org/recorded-in-platform')
    LessonFeedback.objects.update(comment='правка в платформе')

    _sync(db)

    lesson = Lesson.objects.get(group=_group())
    assert lesson.record_url is None
    assert LessonFeedback.objects.get().comment == 'ок'


def test_changed_fields_update_in_place(db):
    group_id = _group().id
    misha = Student.objects.get(full_name='Миша')
    db.con.execute("UPDATE student_groups SET code='ПИ1009', default_time='17:30' WHERE id=5")
    db.con.execute("UPDATE participants SET parent_phone='79990000099' WHERE id=11")
    db.con.execute("UPDATE teachers SET phone='70000000000' WHERE id=1")

    _sync(db)

    group = Group.objects.get(id=group_id)
    assert group.name == 'ПИ1009'
    assert set(GroupScheduleSlot.objects.filter(group=group).values_list('start_time', flat=True)) == {
        datetime.time(17, 30)}
    assert Student.objects.get(id=misha.id).parent1_phone == '79990000099'
    assert Teacher.objects.get(name='Анна Тестова').phone == '70000000000'


def test_empty_source_value_keeps_platform_value(db):
    """Имя, вписанное в платформе, пустым значением Eduson не стирается."""
    misha = Student.objects.get(full_name='Миша')
    Student.objects.filter(id=misha.id).update(email='misha@example.org')
    _sync(db)
    assert Student.objects.get(id=misha.id).email == 'misha@example.org'


def test_vanished_group_goes_to_archive_without_facts(db):
    db.group(6, 'СИ1002', 'scratch', 1, fmt='individual')
    db.participant(6, 'Оля', 'Мама Оли', '79990000004', id=14)
    db.session(201, 6, '2026-07-21')
    _sync(db)
    gone = _group('СИ1002')
    db.con.execute('DELETE FROM sessions WHERE group_id=6')
    db.con.execute('DELETE FROM participants WHERE group_id=6')
    db.con.execute('DELETE FROM student_groups WHERE id=6')

    _sync(db)

    gone.refresh_from_db()
    assert not gone.active
    assert not Lesson.objects.filter(group=gone).exists()
    assert not GroupMembership.objects.filter(group=gone).exists()
    assert Student.objects.filter(full_name='Оля').exists()          # ученик остаётся


def test_renewals_accounts_and_platform_groups_untouched(db):
    deal = RenewalDeal.objects.first()
    account = Account.objects.get(email='anna@example.com')
    RenewalDeal.objects.filter(id=deal.id).update(assignee=account)
    Account.objects.filter(id=account.id).update(is_active=False, password='platform-password')
    own = Group.objects.create(name='Платформенная', direction_id=_group().direction_id,
                               teacher_id=_group().teacher_id, is_individual=True,
                               lesson_duration_minutes=60, lessons_per_week=1, active=True,
                               created_at=_group().created_at)

    _sync(db)

    assert RenewalDeal.objects.get(id=deal.id).assignee_id == account.id
    account.refresh_from_db()
    assert (account.is_active, account.password) == (False, 'platform-password')
    assert Group.objects.filter(id=own.id, name='Платформенная', active=True).exists()


def test_lesson_replacement_skips_change_history(db):
    _sync(db)
    contexts = pghistory.models.Context.objects.filter(metadata__operation='eduson.sync')
    models = set(pghistory.models.Events.objects.filter(pgh_context_id__in=contexts)
                 .values_list('pgh_model', flat=True))
    assert not any(m.startswith(('lessons.', 'scheduling.', 'feedback.', 'memberships.')) for m in models)


def test_reconcile_error_rolls_back_everything(db):
    Lesson.objects.update(record_url='https://example.org/kept')
    db.session(104, 5, '2026-07-20')
    with mock.patch(f'{SYNC}.reconcile', return_value=(['x'], ['поломка'], [])):
        with pytest.raises(CommandError, match='Сверка не сошлась'):
            _sync(db)
    assert Lesson.objects.count() == 1
    assert Lesson.objects.get().record_url == 'https://example.org/kept'


def test_dry_run_changes_nothing(db):
    db.session(104, 5, '2026-07-20')
    out = _sync(db, '--dry-run')
    assert 'Пробный прогон' in out
    assert Lesson.objects.count() == 1


def test_refuses_without_links(db):
    EdusonLink.objects.all().delete()
    with pytest.raises(CommandError, match='нет связей'):
        _sync(db)


def test_caches_invalidated_after_commit(db, django_capture_on_commit_callbacks):
    with mock.patch(f'{SYNC}.invalidate_finance_cache') as fin, \
            mock.patch(f'{SYNC}.invalidate_registry_cache') as reg, \
            django_capture_on_commit_callbacks(execute=True):
        _sync(db)
    fin.assert_called_once_with()
    reg.assert_called_once_with()
