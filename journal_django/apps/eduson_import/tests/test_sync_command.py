"""manage.py sync_eduson — мягкое обновление поверх базы, собранной import_eduson:
id групп и учеников не меняются, новое приходит, уроки групп старой системы
заменяются, продления/учётки/записи платформы не трогаются, ошибка — откат всего."""
from __future__ import annotations

import datetime
import io
from decimal import Decimal
from unittest import mock

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import Count
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Account
from apps.eduson_import.models import EdusonLink
from apps.eduson_import.sync import HISTORYLESS_MODELS, purge_group_facts
from apps.eduson_import.tests.factory import EdusonDB
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group, GroupScheduleSlot
from apps.lessons.models import Lesson
from apps.memberships.models import GroupMembership
from apps.payroll.models import CancellationPay, PayRateVersion, Payroll
from apps.payroll.repository import update_payroll
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


def test_teacher_call_url_follows_eduson_but_empty_keeps_platform_value(db):
    teacher = Teacher.objects.get(name='Анна Тестова')
    Teacher.objects.filter(id=teacher.id).update(call_url='https://example.org/platform-call')
    _sync(db)
    assert Teacher.objects.get(id=teacher.id).call_url == 'https://example.org/platform-call'

    db.con.execute("UPDATE teachers SET telemost='https://telemost.example/j/2' WHERE id=1")
    _sync(db)
    assert Teacher.objects.get(id=teacher.id).call_url == 'https://telemost.example/j/2'


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


def _history_counts() -> list[int]:
    return [m.pgh_event_model.objects.count() for m in HISTORYLESS_MODELS]


def test_replacement_skips_change_history(db, payroll_rates):
    """Замена фактов и пересчёт зарплаты в синке не пишут в журнал изменений ни
    одной из моделей HISTORYLESS_MODELS (включая начисления и доп.уроки)."""
    _sync(db)
    update_payroll(Payroll.objects.get().id, {'payment': Decimal('777')})
    CancellationPay.objects.update(payment=Decimal('123'), is_manual=True)
    # Положительный контроль: вне синка журнал этих моделей пишется.
    before = _history_counts()
    Lesson.objects.update(record_url='https://example.org/control')
    assert sum(_history_counts()) == sum(before) + 1
    before = _history_counts()
    assert before[HISTORYLESS_MODELS.index(Payroll)] and before[HISTORYLESS_MODELS.index(CancellationPay)]

    _sync(db)

    assert _history_counts() == before


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


# ── зарплата (спека 2026-10-20-eduson-motivation-payroll-design, §3) ─────────

def _lesson_pay() -> dict:
    """Начисления за уроки по естественному ключу урока (id урока меняется на синке)."""
    return {
        (p['lesson__group_id'], p['lesson__lesson_date'], p['lesson__lesson_number']):
            (p['payment'], p['penalty'], p['calc_payment'], p['is_manual'])
        for p in Payroll.objects.values('lesson__group_id', 'lesson__lesson_date', 'lesson__lesson_number',
                                        'payment', 'penalty', 'calc_payment', 'is_manual')
    }


def _cancel_pay() -> list[tuple]:
    # id маркера меняется на синке — сравниваем лишь факт привязки.
    return sorted((*row[:-1], row[-1] is not None) for row in CancellationPay.objects.values_list(
        'group_id', 'lesson_date', 'scheduled_time', 'payment', 'calc_payment', 'is_manual',
        'planned_lesson_id'))


def _make_manual(payment='777', penalty=None, group=None) -> Payroll:
    pay = Payroll.objects.get(lesson__group=group or _group())
    fields = {'payment': Decimal(payment)}
    if penalty is not None:
        fields['penalty'] = Decimal(penalty)
    update_payroll(pay.id, fields)
    return pay


def test_resync_keeps_payroll(db, payroll_rates):
    _sync(db)
    lessons, cancels = _lesson_pay(), _cancel_pay()
    assert lessons and cancels                                    # есть что сравнивать

    out = _sync(db)

    assert _lesson_pay() == lessons
    assert _cancel_pay() == cancels
    assert ('зарплата: начислений 2, ручных восстановлено 0, не найдено 0, '
            'удалено начислений за отмены 0') in out


def test_imported_lessons_paid_by_rate_after_sync(db):
    """Без версии ставок — 0 «Нет ставки»; версия появилась — синк считает по ставке."""
    PayRateVersion.objects.all().delete()
    _sync(db)
    assert set(Payroll.objects.values_list('payment', flat=True)) == {Decimal('0')}
    PayRateVersion.objects.create(
        effective_from=datetime.date(2000, 1, 1), group_rate=Decimal('500'),
        individual_rate=Decimal('700'), cancel_mode='percent', cancel_value=Decimal('50'))

    _sync(db)

    pay = Payroll.objects.get(lesson__group=_group())
    assert (pay.payment, pay.calc_payment, pay.is_manual) == (Decimal('500'), Decimal('500'), False)
    cancel = CancellationPay.objects.get()
    assert (cancel.lesson_date, cancel.payment) == (datetime.date(2026, 7, 13), Decimal('250'))
    assert cancel.planned_lesson is not None


def test_manual_lesson_pay_survives_sync(db, payroll_rates):
    _sync(db)
    old = _make_manual('777', '10')

    out = _sync(db)

    new = Payroll.objects.get(lesson__group=_group())
    assert new.lesson_id != old.lesson_id                         # урок заменён синком
    assert (new.payment, new.penalty, new.is_manual) == (Decimal('777'), Decimal('10'), True)
    assert (new.calc_payment, new.calc_penalty) == (Decimal('500'), Decimal('0'))
    assert 'ручных восстановлено 1, не найдено 0' in out


def test_manual_pay_follows_lesson_when_numbers_shift(db, payroll_rates):
    """В старой системе раньше появился урок — номера сдвинулись; в этот день у
    группы один урок и до, и после — правка находит его по дате."""
    _sync(db)
    _make_manual('777')
    db.session(100, 5, '2026-06-29')
    db.report(100, 5, 1)

    out = _sync(db)

    pay = Payroll.objects.get(lesson__group=_group(), lesson__lesson_date=datetime.date(2026, 7, 6))
    assert pay.lesson.lesson_number == 2
    assert (pay.payment, pay.is_manual) == (Decimal('777'), True)
    assert 'ручных восстановлено 1, не найдено 0' in out


def test_manual_pay_not_moved_when_teacher_changed(db, payroll_rates):
    _sync(db)
    _make_manual('777')
    db.teacher(2, 'Борис Новый')
    db.user('boris@example.com', 'teacher', teacher_id=2)
    # Группу передали другому преподавателю: урок в новой заливке — его.
    db.con.execute('UPDATE student_groups SET teacher_id=2 WHERE id=5')

    out = _sync(db)

    pay = Payroll.objects.get(lesson__group=_group())
    assert pay.lesson.teacher.name == 'Борис Новый'
    assert (pay.payment, pay.is_manual) == (Decimal('500'), False)
    assert 'ручных восстановлено 0, не найдено 1' in out
    assert ('ручная правка зарплаты не перенесена: сменился преподаватель урока '
            '(ПИ1001, 2026-07-06, урок 1)') in out
    assert 'Борис' not in out


def test_manual_cancellation_pay_survives_sync(db, payroll_rates):
    _sync(db)
    marker_id = CancellationPay.objects.get().planned_lesson_id
    CancellationPay.objects.update(payment=Decimal('123'), is_manual=True)

    _sync(db)

    pay = CancellationPay.objects.get()
    assert pay.planned_lesson_id is not None and pay.planned_lesson_id != marker_id
    assert (pay.payment, pay.calc_payment, pay.is_manual) == (Decimal('123'), Decimal('250'), True)


def test_manual_cancellation_pay_survives_time_change(db, payroll_rates):
    """В старой системе сменили время отменённого занятия: прошлое начисление не
    остаётся сиротой рядом с новым (двойная оплата), правка переезжает."""
    _sync(db)
    CancellationPay.objects.update(payment=Decimal('123'), is_manual=True)
    db.con.execute("UPDATE sessions SET time='17:30' WHERE id=102")

    _sync(db)

    pay = CancellationPay.objects.get()
    assert pay.planned_lesson_id is not None
    assert pay.scheduled_time == datetime.time(17, 30)
    assert (pay.payment, pay.is_manual) == (Decimal('123'), True)


def test_manual_pay_of_vanished_lesson_reported(db, payroll_rates):
    _sync(db)
    _make_manual('777')
    db.session(104, 5, '2026-07-20')
    db.report(104, 5, 1)
    db.con.execute('DELETE FROM lesson_reports WHERE session_id=101')
    db.con.execute('DELETE FROM sessions WHERE id=101')

    out = _sync(db)

    assert not Payroll.objects.filter(is_manual=True).exists()
    assert 'ручных восстановлено 0, не найдено 1' in out
    assert 'ручная правка зарплаты не перенесена: урок не найден (ПИ1001, 2026-07-06, урок 1)' in out


def test_manual_pay_in_vanished_group_reported(db, payroll_rates):
    db.group(6, 'СИ1002', 'scratch', 1, fmt='individual')
    db.participant(6, 'Оля', 'Мама Оли', '79990000004', id=14)
    db.session(201, 6, '2026-07-21')
    _sync(db)
    _make_manual('999', group=_group('СИ1002'))
    db.con.execute('DELETE FROM sessions WHERE group_id=6')
    db.con.execute('DELETE FROM participants WHERE group_id=6')
    db.con.execute('DELETE FROM student_groups WHERE id=6')

    out = _sync(db)

    assert not Payroll.objects.filter(lesson__group=_group('СИ1002')).exists()
    assert 'ручная правка зарплаты не перенесена: урок не найден (СИ1002, 2026-07-21, урок 1)' in out


def test_purge_locks_lessons_then_plan_then_all_payroll_before_deleting(db, payroll_rates):
    """Порядок проекта «урок → план → деньги»: синк идёт при работающем сервере.
    Лок начислений — без условия на is_manual: иначе параллельный PATCH, делающий
    строку ручной, не попал бы под лок и потерялся."""
    with CaptureQueriesContext(connection) as q:
        purge_group_facts([_group().id])
    sqls = [x['sql'] for x in q.captured_queries]
    locks = [s for s in sqls if 'FOR UPDATE' in s]
    first_write = next(i for i, s in enumerate(sqls) if s.startswith(('DELETE', 'UPDATE')))
    assert [s.split(' FROM ', 1)[1].split()[0] for s in locks[:3]] == [
        '"lessons"', '"planned_lessons"', '"payroll"']
    assert sqls.index(locks[2]) < first_write
    lessons_lock, plan_lock, payroll_lock = locks[:3]
    assert 'ORDER BY "lessons"."id" ASC' in lessons_lock
    assert 'ORDER BY "planned_lessons"."seq" ASC NULLS LAST, "planned_lessons"."id" ASC' in plan_lock
    assert 'ORDER BY "payroll"."lesson_id" ASC' in payroll_lock
    assert 'is_manual' not in payroll_lock.split(' WHERE ', 1)[1]
