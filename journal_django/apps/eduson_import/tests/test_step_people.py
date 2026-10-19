"""Шаг «люди»: направления, преподаватели, учётки (спека §5.1 с поправками §5.4)."""
from __future__ import annotations

import datetime as dt

import pytest

from apps.accounts.models import Account
from apps.directions.models import Direction
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.tests.factory import EdusonDB
from apps.teachers.models import Teacher

pytestmark = pytest.mark.django_db

AS_OF = dt.date(2026, 10, 18)


def _run(db: EdusonDB) -> ImportContext:
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
    return ctx


def _teacher(ctx: ImportContext, eduson_id: int) -> Teacher:
    return Teacher.objects.get(pk=ctx.teacher_by_eduson[eduson_id])


# ── направления ─────────────────────────────────────────────────────────────

def test_directions_with_eduson_colors(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    ctx = _run(db)
    got = {d.name: (d.color, d.active, d.total_lessons) for d in Direction.objects.all()}
    assert got == {'Scratch': ('#c2410c', True, None), 'Python': ('#1a56db', True, None),
                   'Вайб-кодинг': ('#067a57', True, None), 'Группа': ('#a21570', True, None)}
    assert {c: Direction.objects.get(pk=i).name for c, i in ctx.direction_by_course.items()} == {
        'scratch': 'Scratch', 'python': 'Python', 'vibe': 'Вайб-кодинг', 'groups': 'Группа'}


def test_unknown_course_stops_import(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.group(5, 'ХХ1001', 'robotics', 1)
    with pytest.raises(ImportDataError, match='ХХ1001'):
        _run(db)


# ── преподаватели ───────────────────────────────────────────────────────────

def test_teachers_who_is_created(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Учитель Без Групп', email='t1@example.com', phone='70000000011', active=0)
    db.teacher(2, 'Админ Ведёт Группу', role='admin')
    db.teacher(3, 'Админ Заменял Урок', role='admin')
    db.teacher(4, 'Админ Писал Отчёт', role='admin')
    db.teacher(6, 'Админ Вёл Раньше', role='admin')
    db.teacher(13, 'Админ Без Ссылок', role='admin')
    db.group(5, 'ПИ1001', 'python', 2)
    db.group(7, 'ПИ1002', 'python', 6)
    db.update_group(7, teacher_id=2)                       # 6 вёл группу до смены
    db.session(100, 5, '2026-09-01', teacher_id=3)
    db.report(100, 5, 4)
    ctx = _run(db)

    assert set(Teacher.objects.values_list('name', flat=True)) == {
        'Учитель Без Групп', 'Админ Ведёт Группу', 'Админ Заменял Урок', 'Админ Писал Отчёт',
        'Админ Вёл Раньше'}
    assert 13 not in ctx.teacher_by_eduson
    t1 = _teacher(ctx, 1)
    assert (t1.email, t1.phone, t1.active, t1.is_service) == ('t1@example.com', '70000000011', False, False)
    assert _teacher(ctx, 2).email is None and _teacher(ctx, 2).phone is None   # '' → NULL


def test_duplicate_teacher_names_stop_import(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна  Тестова')
    db.teacher(2, 'анна тестова ')
    with pytest.raises(ImportDataError, match=r'#1.*#2'):
        _run(db)
    assert not Teacher.objects.exists()


def test_reference_to_missing_teacher_stops_import(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.group(5, 'ПИ1001', 'python', 99)
    with pytest.raises(ImportDataError, match='#99'):
        _run(db)


def test_teacher_contacts_reported_without_values(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова', tg='@anna_secret', telemost='https://telemost.example/x', subjects='Python')
    db.teacher(2, 'Борис Тестов')
    ctx = _run(db)
    text = '\n'.join(ctx.report)
    assert '#1' in text and 'tg' in text and 'telemost' in text and 'subjects' in text
    assert '#2' not in text
    for secret in ('anna_secret', 'telemost.example', 'Анна'):
        assert secret not in text


# ── учётки ──────────────────────────────────────────────────────────────────

def test_accounts_roles_links_and_passwords(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.teacher(6, 'Директор Ведёт', role='admin')
    db.teacher(13, 'Директор Не Ведёт', role='admin')
    db.group(5, 'ПИ1001', 'python', 6)
    u_anna = db.user(' Anna@Example.com ', 'teacher', teacher_id=1, password='pw-anna', name='Анна')
    u_lead = db.user('lead@example.com', 'admin', teacher_id=6, password='pw-lead', name='Директор')
    u_boss = db.user('boss@example.com', 'admin', teacher_id=13, password='pw-boss', name='Босс')
    u_off = db.user('off@example.com', 'admin', password='pw-off', name='', active=0)
    ctx = _run(db)

    anna = Account.objects.get(email='anna@example.com')
    assert (anna.role, anna.teacher_id, anna.full_name, anna.is_active) == ('teacher', ctx.teacher_by_eduson[1],
                                                                            None, True)
    assert anna.password.startswith('pbkdf2_wrapped_bcrypt$')
    assert anna.check_password('pw-anna') and not anna.check_password('pw-lead')

    lead = Account.objects.get(email='lead@example.com')  # админ, который ведёт группу, — преподаватель
    assert (lead.role, lead.teacher_id, lead.full_name) == ('teacher', ctx.teacher_by_eduson[6], None)
    assert lead.check_password('pw-lead')

    boss = Account.objects.get(email='boss@example.com')  # админ без ссылок — admin, связь отброшена
    assert (boss.role, boss.teacher_id, boss.full_name) == ('admin', None, 'Босс')
    off = Account.objects.get(email='off@example.com')
    assert (off.role, off.full_name, off.is_active) == ('admin', None, False)
    assert ctx.account_by_user == {u_anna: anna.id, u_lead: lead.id, u_boss: boss.id, u_off: off.id}


@pytest.mark.parametrize('setup, match', [
    (lambda db: db.user('a@example.com', 'teacher', teacher_id=1, password_hash='plain-text'),
     r'учётка #1: пароль'),
    (lambda db: db.user('a@example.com', 'teacher'), r'учётка #1.*без преподавателя'),
    (lambda db: db.user('a@example.com', 'manager', teacher_id=1), r'учётка #1.*роль'),
    (lambda db: (db.user('a@example.com', 'teacher', teacher_id=1),
                 db.user('A@Example.com', 'admin')), r'#1.*#2.*почт'),
    (lambda db: (db.user('a@example.com', 'teacher', teacher_id=1),
                 db.user('b@example.com', 'teacher', teacher_id=1)), r'#1.*#2.*преподавател'),
    (lambda db: db.user('  ', 'admin'), r'учётка #1.*почт'),
])
def test_account_data_errors(tmp_path, setup, match):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    setup(db)
    with pytest.raises(ImportDataError, match=match) as err:
        _run(db)
    assert '$2a$' not in str(err.value)


def test_bad_hash_not_leaked_in_error(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    broken = '$2a$31$' + 'x' * 53                     # cost вне допустимого — хэш отвергнут
    db.user('a@example.com', 'teacher', teacher_id=1, password_hash=broken)
    with pytest.raises(ImportDataError) as err:
        _run(db)
    assert broken not in str(err.value) and 'x' * 10 not in str(err.value)
