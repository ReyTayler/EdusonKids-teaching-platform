"""Сверка импорта Eduson (спека §5.4 «Сверка (вместо 5.3)»): таблица «Eduson → KOTOKOD»,
«проведено» по каждой группе, проверки здоровья плана KOTOKOD."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.eduson_import.context import ImportContext
from apps.eduson_import.reconcile import reconcile
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import import_feedback, import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.steps.renewals import import_renewals
from apps.eduson_import.tests.factory import EdusonDB
from apps.groups.models import Group
from apps.lessons.models import Lesson
from apps.memberships.models import GroupMembership
from apps.scheduling.models import PlannedLesson

pytestmark = pytest.mark.django_db

AS_OF = dt.date(2026, 10, 18)


def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.teacher(2, 'Борис Тестов')
    db.teacher(13, 'Админ Без Ссылок', role='admin')
    db.user('anna@example.com', 'teacher', teacher_id=1)
    db.user('admin@example.com', 'admin', teacher_id=13)
    # ПИ1001: №1 — фиктивная held (дата-заглушка), дальше настоящие, отработка 45 мин.
    db.group(5, 'ПИ1001', 'python', 1, fmt='group', package=20, done=7)
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(5, 'Маша', 'Мама Маши', '79990000002')
    db.session(90, 5, '2028-06-07', time='', lesson_no=1)
    db.session(101, 5, '2026-07-06', lesson_no=2)
    db.session(102, 5, '2026-07-13', type='makeup', duration=45, lesson_no=3)
    db.session(103, 5, '2026-07-20', status='cancelled', lesson_no=4)
    db.session(104, 5, '2026-07-27', lesson_no=4)
    db.session(105, 5, '2026-10-05', status='scheduled', lesson_no=5)    # прошедшая незаполненная
    db.session(106, 5, '2030-01-07', status='scheduled', lesson_no=6)
    db.report(101, 5, 1, comment='ок')
    db.report(90, 5, 1)                                                  # на фиктивную → на проверку
    db.report(106, 5, 1, status='pending')
    # СИ1002: две проведённые в один день — duplicate_dates (предупреждение, не ошибка).
    db.group(6, 'СИ1002', 'scratch', 2)
    db.participant(6, 'Петя', 'Папа Пети', '79990000003')
    db.session(201, 6, '2026-08-03', time='10:00')
    db.session(202, 6, '2026-08-03', time='12:00')
    db.renewal(6, 'contacted', note='звонили')
    return db


def _run(db: EdusonDB):
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
        import_feedback(src, ctx)
        import_renewals(src, ctx)
        lines, errors, warnings = reconcile(src, ctx)
    return ctx, lines, errors, warnings


def _line(lines: list[str], label: str) -> list[str]:
    """Колонки «Eduson», «KOTOKOD» строки таблицы (пояснение после них отбрасывается)."""
    [line] = [x for x in lines if x.startswith(label)]
    return line[len(label):].split()[:2]


def test_clean_import_reconciles(tmp_path):
    ctx, lines, errors, warnings = _run(_db(tmp_path))
    assert errors == []
    table = '\n'.join(lines)
    # Колонки: Eduson, KOTOKOD.
    assert _line(lines, 'группы') == ['2', '2']
    assert _line(lines, 'учётки') == ['2', '2']
    assert _line(lines, 'преподаватели') == ['3', '2']      # админ без ссылок — только учётка
    assert _line(lines, 'ученики') == ['3', '3']
    assert _line(lines, 'проведённые уроки') == ['6', '5']  # фиктивная held уроком не стала
    assert _line(lines, 'позиции плана') == ['9', '8']
    assert _line(lines, 'отзывы (submitted)') == ['2', '1']
    assert _line(lines, 'продления (группы)') == ['1', '0']          # 2 урока из 4 — «Думает» нельзя
    assert 'фиктивн' in table and 'на проверку' in table
    # «проведено» Eduson (7 + 4 held, отработка целым) ≠ KOTOKOD (1 + 2.5) — справочно.
    joined = '\n'.join(warnings)
    assert 'ПИ1001' in joined and '3.5' in joined and '11' in joined
    assert 'duplicate_dates' in joined and 'СИ1002' in joined
    assert 'незаполненн' in joined and 'порядка заполнения' in joined
    # Без ПДн: ни имён учеников, ни почт, ни текста заметок.
    everything = table + joined + '\n'.join(errors)
    for secret in ('Миша', 'Маша', 'Петя', 'Мама', '7999', 'example.com', 'звонили'):
        assert secret not in everything


def test_membership_counter_mismatch_is_error(tmp_path):
    db = _db(tmp_path)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        GroupMembership.objects.filter(group_id=ctx.group_by_eduson[5]).update(lessons_done=Decimal('9'))
        _, errors, _ = reconcile(src, ctx)
    assert any('ПИ1001' in e and 'учени' in e for e in errors)


def test_offset_and_fact_count_mismatch_is_error(tmp_path):
    db = _db(tmp_path)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        Group.objects.filter(pk=ctx.group_by_eduson[6]).update(lesson_number_offset=Decimal('2'))
        Lesson.objects.filter(pk=ctx.lesson_by_session[104]).delete()
        _, errors, _ = reconcile(src, ctx)
    text = '\n'.join(errors)
    assert 'СИ1002' in text and 'ПИ1001' in text
    assert any(e.startswith('проведённые уроки') for e in errors)


def test_plan_health_error_check_fails_reconcile(tmp_path):
    db = _db(tmp_path)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        PlannedLesson.objects.filter(pk=ctx.planned_by_session[101]).update(lesson_number=Decimal('7'))
        _, errors, _ = reconcile(src, ctx)
    assert any('number_mismatch' in e and 'ПИ1001' in e for e in errors)


def test_plan_error_in_archived_group_fails_reconcile(tmp_path):
    db = _db(tmp_path)
    db.group(7, 'ПГ1003', 'python', 2, active=0)
    db.participant(7, 'Коля', 'Мама Коли', '79990000004')
    db.session(301, 7, '2026-06-01')
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        PlannedLesson.objects.filter(pk=ctx.planned_by_session[301]).update(scheduled_date=dt.date(2026, 6, 2))
        _, errors, _ = reconcile(src, ctx)
    assert any('date_mismatch' in e and 'ПГ1003' in e for e in errors)


def test_unknown_health_check_is_error(tmp_path):
    from unittest import mock

    fake = {'checked': 1, 'groups': [{'group_id': 1, 'name': 'ПИ1001', 'counts': {'new_check': 2, 'collision': 1}}]}
    db = _db(tmp_path)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        with mock.patch('apps.eduson_import.reconcile.health.check_all', return_value=fake):
            _, errors, warnings = reconcile(src, ctx)
    assert any('new_check' in e for e in errors)
    assert not any('collision' in e for e in errors) and any('collision' in w for w in warnings)


def test_year_fixed_held_reconcile_as_lessons(tmp_path):
    """Held из вкладки «Июнь 2027» стали уроками: «проведённые уроки» Eduson = KOTOKOD
    за вычетом только оставшихся фиктивных (у ПИ1001 — №1 на 2028 год), отдельная
    строка — сколько перенесено с исправленным годом; «проведено» группы с ними сходится."""
    db = _db(tmp_path)
    db.group(7, 'ВИ1001', 'vibe', 2, fmt='group', package=20)
    db.participant(7, 'Коля', 'Мама Коли', '79990000004')
    db.session(301, 7, '2027-06-02', time='', lesson_no=1)
    db.session(302, 7, '2027-06-09', time='', lesson_no=2)
    db.session(303, 7, '2026-09-01', lesson_no=3)
    ctx, lines, errors, warnings = _run(db)
    assert errors == []
    assert _line(lines, 'проведённые уроки') == ['9', '8']           # 1 фиктивный (2028) — в N
    assert _line(lines, 'из них год исправлен') == ['2', '2']
    assert _line(lines, 'позиции плана') == ['12', '11']
    table = '\n'.join(lines)
    assert 'Июнь 2027' in table
    assert Group.objects.get(pk=ctx.group_by_eduson[7]).lesson_number_offset == Decimal('0')
    assert GroupMembership.objects.get(group_id=ctx.group_by_eduson[7]).lessons_done == Decimal('3')


def test_year_fixed_lesson_missing_is_error(tmp_path):
    db = _db(tmp_path)
    db.group(7, 'ВИ1001', 'vibe', 2, fmt='group', package=20)
    db.participant(7, 'Коля', 'Мама Коли', '79990000004')
    db.session(301, 7, '2027-06-02', time='', lesson_no=1)
    db.session(303, 7, '2026-09-01', lesson_no=2)
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx); import_groups(src, ctx); import_lessons(src, ctx)
        Lesson.objects.filter(pk=ctx.lesson_by_session[301]).delete()
        _, errors, _ = reconcile(src, ctx)
    assert any(e.startswith('из них год исправлен') for e in errors)
    assert any('ВИ1001' in e for e in errors)
