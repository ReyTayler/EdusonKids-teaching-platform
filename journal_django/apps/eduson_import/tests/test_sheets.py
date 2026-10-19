"""Уроки «до запуска старой системы» из таблиц Eduson (--sheets-dir): разбор CSV
вкладок, правило дозаполнения N, шаг уроков, сверка и команда.

CSV — выдуманные, по формату gviz-выгрузки вкладки (коды групп и имена тестовые)."""
from __future__ import annotations

import csv
import datetime as dt
import io
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from apps.eduson_import.analysis import pre_system_lessons, sheet_backfill
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.reconcile import reconcile
from apps.eduson_import.sheets import COURSES, TABS, file_name, load_sheets, norm_code, parse_tab
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps.groups import import_groups
from apps.eduson_import.steps.lessons import SUBMITTED_BY, SUBMITTED_BY_SHEETS, import_lessons
from apps.eduson_import.steps.people import import_people
from apps.eduson_import.tests.factory import EdusonDB
from apps.groups.models import Group
from apps.lessons.models import Lesson, LessonAttendance
from apps.memberships.models import GroupMembership
from apps.payroll.models import Payroll
from apps.scheduling import health
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import CANCELLED, DONE
from apps.teachers.models import Teacher

AS_OF = dt.date(2026, 10, 18)
D = dt.date.fromisoformat


# ── CSV вкладки ─────────────────────────────────────────────────────────────

def tab_csv(groups: dict[str, dict[int, str]], *, header: str = 'Название группы',
            collapsed_header: bool = False) -> str:
    """CSV вкладки месяца как отдаёт gviz: шапка, затем строка на группу — код,
    преподаватель, пакет, осталось, пройдено и 31 пара (флажок, время).
    groups: код → {день: время ('' — без времени)} отмеченных TRUE дней."""
    out = io.StringIO()
    w = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator='\n')
    w.writerow([header, 'Препод'] + [''] * 65)
    if not collapsed_header:      # gviz иногда склеивает вторую строку шапки с первой
        w.writerow([''] * 5 + ['', 'Время'] * 31)
    w.writerow(['Итого', '', '', '', ''] + ['FALSE', ''] * 31)     # не код группы — пропуск
    for code, days in groups.items():
        cells = []
        for day in range(1, 32):
            cells += (['TRUE', days[day]] if day in days else ['FALSE', ''])
        w.writerow([code, 'Препод Тестов', '8', '4', '4'] + cells)
    return out.getvalue()


def write_sheets(directory, tabs: dict[tuple[str, int], str] | None = None):
    """Все 8 файлов каталога --sheets-dir; tabs: (курс, год вкладки) → CSV, остальные — пустые вкладки."""
    directory.mkdir(parents=True, exist_ok=True)
    for course in COURSES:
        for year, month in TABS:
            text = (tabs or {}).get((course, year)) or tab_csv({})
            (directory / file_name(course, year, month)).write_text(text, encoding='utf-8')
    return directory


def test_parse_tab_marks_codes_and_times():
    text = tab_csv({'тс 1001': {1: '', 3: '11:30', 31: ''}, 'ТС1002': {2: 'потом'}})
    marks = parse_tab(text, 2026, 6, 'scratch-2026-06.csv')
    # Код — как legacy (регистр, пробелы); 31 июня не бывает — флажок не даёт даты; «потом» — не время.
    assert marks == {'ТС1001': {D('2026-06-01'): None, D('2026-06-03'): dt.time(11, 30)},
                     'ТС1002': {D('2026-06-02'): None}}


def test_parse_tab_collapsed_header_keeps_first_group():
    marks = parse_tab(tab_csv({'ТС1001': {5: ''}}, collapsed_header=True), 2027, 6, 'x.csv')
    assert marks == {'ТС1001': {D('2027-06-05'): None}}


def test_parse_tab_rejects_foreign_header():
    with pytest.raises(ImportDataError, match='шапки'):
        parse_tab(tab_csv({}, header='Что-то другое'), 2026, 6, 'x.csv')
    with pytest.raises(ImportDataError):
        parse_tab('<html><body>вход</body></html>', 2026, 6, 'x.csv')


def test_norm_code_like_legacy():
    assert norm_code(' тс 01037 ') == 'ТС1037'
    assert norm_code('ТС1037') == 'ТС1037'


def test_load_sheets_requires_all_files(tmp_path):
    write_sheets(tmp_path / 's')
    (tmp_path / 's' / 'vibe-2027-06.csv').unlink()
    with pytest.raises(ImportDataError, match='vibe-2027-06.csv'):
        load_sheets(tmp_path / 's')
    with pytest.raises(ImportDataError, match='каталог'):
        load_sheets(tmp_path / 'нет')


def test_load_sheets_merges_tabs_per_course(tmp_path):
    root = write_sheets(tmp_path / 's', {
        ('scratch', 2026): tab_csv({'ТС1001': {25: '15:00'}}),
        ('scratch', 2027): tab_csv({'ТС1001': {1: ''}}),
        ('python', 2027): tab_csv({'ТС1001': {9: ''}}),       # тот же код в чужом курсе — отдельно
    })
    marks = load_sheets(root)
    assert marks[('scratch', 'ТС1001')] == {D('2026-06-25'): dt.time(15, 0), D('2027-06-01'): None}
    assert marks[('python', 'ТС1001')] == {D('2027-06-09'): None}


# ── правило дозаполнения ────────────────────────────────────────────────────

def _db(tmp_path) -> EdusonDB:
    db = EdusonDB(tmp_path / 'e.db')
    db.teacher(1, 'Анна Тестова')
    db.teacher(2, 'Борис Тестов')
    return db


def _marks(**by_code) -> dict:
    """ТС1001={'2026-06-25': '', '2027-06-01': '11:00'} → отметки курса scratch."""
    return {('scratch', code): {D(day): (dt.time.fromisoformat(t) if t else None) for day, t in days.items()}
            for code, days in by_code.items()}


def _backfill(db: EdusonDB, marks, as_of=AS_OF):
    with EdusonSource(db.close()) as src:
        return sheet_backfill(src, marks, as_of, pre_system_lessons(src, as_of))


def test_backfill_all_candidates_when_not_more_than_n(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1)
    db.session(101, 5, '2026-07-06', lesson_no=5)             # N = 4
    res = _backfill(db, _marks(ТС1001={'2026-06-25': '', '2027-06-01': '11:00', '2027-06-03': ''}))
    b = res[5]
    # «Июнь 2027» → тот же ошибочный год, 2026-06-DD; порядок — по дате.
    assert b.lessons == ((D('2026-06-01'), dt.time(11, 0)), (D('2026-06-03'), None), (D('2026-06-25'), None))
    assert (b.n_before, b.candidates, b.n_after) == (Decimal(4), 3, Decimal(1))


def test_backfill_takes_latest_when_more_than_n(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1)
    db.session(101, 5, '2026-07-06', lesson_no=3)             # N = 2
    res = _backfill(db, _marks(ТС1001={'2027-06-01': '', '2027-06-02': '', '2027-06-03': '', '2027-06-04': ''}))
    assert [d for d, _ in res[5].lessons] == [D('2026-06-03'), D('2026-06-04')]
    assert (res[5].candidates, res[5].n_after) == (4, Decimal(0))


def test_backfill_cutoff_duplicates_and_session_days(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1)
    db.session(100, 5, '2026-06-05', status='cancelled')       # отменённая сессия базы в этот день
    db.session(99, 5, '2027-06-12', time='', lesson_no=10)     # held с исправленным годом → первый урок 12.06
    db.session(101, 5, '2026-07-06', lesson_no=11)             # N = 9
    res = _backfill(db, _marks(ТС1001={
        '2026-06-02': '', '2027-06-02': '10:00',     # одна дата с двух вкладок — один урок, время где есть
        '2027-06-05': '',                            # отменённая сессия урок не отменяет — считается
        '2026-06-12': '', '2027-06-20': '',          # не раньше первого проведённого — база уже знает
        '2026-06-11': '',
    }))
    assert res[5].lessons == ((D('2026-06-02'), dt.time(10, 0)), (D('2026-06-05'), None),
                              (D('2026-06-11'), None))
    assert (res[5].candidates, res[5].n_after, res[5].on_cancelled_days) == (3, Decimal(6), 1)
    assert res[5].fills == {}


def test_backfill_not_after_as_of_and_only_groups_with_n(tmp_path):
    """День импорта 15.06: у ТС1001 проведённых нет (две held-заглушки на 2028 — N = 2),
    отсечка «раньше первого урока» не работает — работает «не позже дня импорта»."""
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1)
    db.session(101, 5, '2028-01-10', time='')
    db.session(102, 5, '2028-01-17', time='')
    db.group(6, 'ТС1002', 'scratch', 1)
    db.session(201, 6, '2026-06-01', lesson_no=1)             # N = 0 — таблица не трогает
    marks = _marks(ТС1001={'2026-06-10': '', '2026-06-20': '', '2027-06-12': '', '2027-06-16': ''},
                   ТС1002={'2026-05-10': ''})
    marks[('python', 'ТС1001')] = {D('2026-06-01'): None}     # чужой курс — не эта группа
    res = _backfill(db, marks, as_of=D('2026-06-15'))
    assert set(res) == {5}
    # 20.06 и 16.06 (из «Июнь 2027») позже дня импорта.
    assert res[5].lessons == ((D('2026-06-10'), None), (D('2026-06-12'), None))
    assert res[5].n_before == Decimal(2)


def test_backfill_group_with_n_but_no_marks_reported(tmp_path):
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1)
    db.session(101, 5, '2026-07-06', lesson_no=2)
    res = _backfill(db, {})
    assert (res[5].lessons, res[5].candidates, res[5].n_after) == ((), 0, Decimal(1))


# ── шаг уроков и группы ────────────────────────────────────────────────────

def _school(tmp_path) -> EdusonDB:
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1, fmt='group', package=20, dow=3, time='16:00')   # слот — среда
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.participant(5, 'Маша', 'Мама Маши', '79990000002')
    db.session(101, 5, '2026-07-01', lesson_no=4)             # N = 3
    db.session(102, 5, '2026-07-08', type='makeup', duration=45, lesson_no=5)
    db.session(103, 5, '2030-01-07', status='scheduled', lesson_no=6)
    # Группу передали Борису только в августе: июнь — у Анны.
    db.update_group(5, teacher_id=2, audit_kw={'ts': '2026-08-05T06:00:00.000Z'})
    db.group(6, 'ТС1002', 'scratch', 1, fmt='group', package=20)
    db.participant(6, 'Петя', 'Папа Пети', '79990000003')
    db.session(201, 6, '2026-07-02', lesson_no=2)             # N = 1, отметок больше
    return db


SCHOOL_MARKS = {
    ('scratch', 'ТС1001'): {D('2027-06-03'): None, D('2027-06-10'): dt.time(11, 0)},
    ('scratch', 'ТС1002'): {D('2026-06-04'): None, D('2026-06-11'): None, D('2026-06-18'): dt.time(9, 0)},
}


def _run(db: EdusonDB, marks=SCHOOL_MARKS) -> ImportContext:
    with EdusonSource(db.close()) as src:
        ctx = ImportContext.from_source(src, AS_OF, marks)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
    return ctx


@pytest.mark.django_db
def test_sheet_lessons_held_before_real_with_numbering_and_offset(tmp_path):
    ctx = _run(_school(tmp_path))
    group = Group.objects.get(pk=ctx.group_by_eduson[5])
    assert group.lesson_number_offset == Decimal('1')           # N 3 − 2 из таблиц
    lessons = list(Lesson.objects.filter(group=group).order_by('lesson_date'))
    assert [(x.lesson_date, x.lesson_number, x.submitted_by_token) for x in lessons] == [
        (D('2026-06-03'), Decimal('2'), SUBMITTED_BY_SHEETS),
        (D('2026-06-10'), Decimal('3'), SUBMITTED_BY_SHEETS),
        (D('2026-07-01'), Decimal('4'), SUBMITTED_BY),
        (D('2026-07-08'), Decimal('4.5'), SUBMITTED_BY),
    ]
    assert {x.lesson_duration_minutes for x in lessons[:2]} == {60}
    assert {x.lesson_type for x in lessons[:2]} == {'regular'}
    # lessons_done = оставшийся N (1) + из таблиц (2 × 1) + проведённые (1 + 0.5 отработка).
    assert set(GroupMembership.objects.filter(group=group).values_list('lessons_done', flat=True)) == {
        Decimal('4.5')}
    plan = list(PlannedLesson.objects.filter(group=group).order_by('seq'))
    assert [(p.seq, p.lesson_number, p.status) for p in plan][:2] == [(1, Decimal('2'), DONE), (2, Decimal('3'), DONE)]
    assert plan[-1].lesson_number == Decimal('5.5')            # следующий плановый продолжает нумерацию
    assert [p.fact_lesson_id for p in plan[:2]] == [lessons[0].id, lessons[1].id]
    # Время: из ячейки; нет — слот группы (среда 16:00).
    assert [p.scheduled_time for p in plan[:2]] == [dt.time(16, 0), dt.time(11, 0)]
    findings = health.check_group(group.id)['findings']
    assert not set(findings) & {'fact_without_position', 'number_mismatch', 'date_mismatch', 'done_in_future'}


@pytest.mark.django_db
def test_sheet_lessons_attendance_teacher_no_payroll(tmp_path):
    ctx = _run(_school(tmp_path))
    anna = Teacher.objects.get(name='Анна Тестова').id
    sheet = Lesson.objects.filter(submitted_by_token=SUBMITTED_BY_SHEETS, group_id=ctx.group_by_eduson[5])
    assert sheet.count() == 2
    students = set(GroupMembership.objects.filter(group_id=ctx.group_by_eduson[5])
                   .values_list('student_id', flat=True))
    assert len(students) == 2
    for lesson in sheet:
        assert set(LessonAttendance.objects.filter(lesson=lesson).values_list('student_id', 'present')) == {
            (s, True) for s in students}
        # Преподаватель группы на дату по истории (июнь — Анна, хотя сейчас группа у Бориса).
        assert (lesson.teacher_id, lesson.original_teacher_id) == (anna, None)
    # Момент записи — дата и время урока по Москве (16:00 слота / 11:00 из ячейки).
    assert sorted(x.submitted_at for x in sheet) == [
        dt.datetime(2026, 6, 3, 13, 0, tzinfo=dt.timezone.utc), dt.datetime(2026, 6, 10, 8, 0, tzinfo=dt.timezone.utc)]
    assert Payroll.objects.count() == 0
    # Сессии Eduson у урока из таблицы нет — в карте сессий его нет.
    assert set(ctx.lesson_by_session) == {101, 102, 201}


@pytest.mark.django_db
def test_more_marks_than_n_latest_and_reported(tmp_path):
    ctx = _run(_school(tmp_path))
    group = Group.objects.get(pk=ctx.group_by_eduson[6])
    assert group.lesson_number_offset == Decimal('0')
    sheet = Lesson.objects.get(group=group, submitted_by_token=SUBMITTED_BY_SHEETS)
    assert (sheet.lesson_date, sheet.lesson_number) == (D('2026-06-18'), Decimal('1'))
    assert any('ТС1002' in line and 'больше' in line for line in ctx.report)
    assert any('ТС1002' in line and '3' in line and 'самых поздних' in line for line in ctx.manual_review)
    assert any(line.startswith('из таблиц') and 'заведено 3 уроков в 2 группах' in line for line in ctx.report)


@pytest.mark.django_db
def test_sheet_mark_fills_unfilled_session_of_that_day(tmp_path):
    """В день отметки у группы незаполненное занятие (scheduled) — это тот же урок:
    отдельной позиции нет, сессия проведена этой отметкой (факт с меткой таблиц на её
    позиции, время из ячейки), строк «закройте вручную» и collision нет. Отсечка и
    нумерация не меняются: scheduled не входит в «первый проведённый»."""
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1, fmt='group')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.session(100, 5, '2026-06-28', time='12:00', status='scheduled', lesson_no=9)
    db.session(101, 5, '2026-07-05', lesson_no=3)             # N = 2
    ctx = _run(db, {('scratch', 'ТС1001'): {D('2026-06-21'): None, D('2026-06-28'): dt.time(10, 30)}})
    group = Group.objects.get(pk=ctx.group_by_eduson[5])
    assert group.lesson_number_offset == Decimal('0')
    lessons = list(Lesson.objects.filter(group=group).order_by('lesson_date'))
    assert [(x.lesson_date, x.lesson_number, x.submitted_by_token) for x in lessons] == [
        (D('2026-06-21'), Decimal('1'), SUBMITTED_BY_SHEETS),
        (D('2026-06-28'), Decimal('2'), SUBMITTED_BY_SHEETS),
        (D('2026-07-05'), Decimal('3'), SUBMITTED_BY)]
    # Позиция сессии #100 — та же, теперь done и с фактом; второй позиции на 28.06 нет.
    pos = PlannedLesson.objects.get(pk=ctx.planned_by_session[100])
    assert (pos.status, pos.seq, pos.lesson_number, pos.fact_lesson_id) == (
        DONE, 2, Decimal('2'), lessons[1].id)
    assert pos.scheduled_time == dt.time(10, 30)                # время из ячейки таблицы
    assert ctx.lesson_by_session[100] == lessons[1].id
    assert PlannedLesson.objects.filter(group=group).count() == 3
    assert set(LessonAttendance.objects.filter(lesson=lessons[1]).values_list('present', flat=True)) == {True}
    assert GroupMembership.objects.get(group=group).lessons_done == Decimal('3')
    assert any('ТС1001' in line and 'проведено этой отметкой' in line for line in ctx.report)
    assert not any('вручную' in line for line in ctx.report)
    assert 'collision' not in health.check_group(group.id)['findings']
    with EdusonSource(db.path) as src:
        _, errors, _ = reconcile(src, ctx)
    assert errors == []


@pytest.mark.django_db
def test_sheet_mark_does_not_fill_scheduled_makeup(tmp_path):
    """Запланированная отработка (makeup, 45 мин, один ученик) в день отметки — не урок
    группы: отметка её не проводит, урок из таблицы заводится отдельно (60 мин)."""
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1, fmt='group')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.session(100, 5, '2026-06-28', time='12:00', status='scheduled', type='makeup', duration=45)
    db.session(101, 5, '2026-07-05', lesson_no=2)             # N = 1
    ctx = _run(db, {('scratch', 'ТС1001'): {D('2026-06-28'): None}})
    group = Group.objects.get(pk=ctx.group_by_eduson[5])
    assert group.lesson_number_offset == Decimal('0')
    assert 100 not in ctx.lesson_by_session                    # отработку отметка не провела
    sheet = Lesson.objects.get(group=group, submitted_by_token=SUBMITTED_BY_SHEETS)
    assert (sheet.lesson_date, sheet.lesson_number) == (D('2026-06-28'), Decimal('1'))
    assert GroupMembership.objects.get(group=group).lessons_done == Decimal('2')
    with EdusonSource(db.path) as src:
        _, errors, _ = reconcile(src, ctx)
    assert errors == []


@pytest.mark.django_db
def test_sheet_mark_on_cancelled_session_day_keeps_both(tmp_path):
    """Отменённое занятие в день отметки: урок из таблицы заводится отдельно, маркер
    отмены остаётся, в отчёте — строка об этом."""
    db = _db(tmp_path)
    db.group(5, 'ТС1001', 'scratch', 1, fmt='group')
    db.participant(5, 'Миша', 'Мама Миши', '79990000001')
    db.session(100, 5, '2026-06-28', status='cancelled', lesson_no=9)
    db.session(101, 5, '2026-07-05', lesson_no=2)             # N = 1
    ctx = _run(db, {('scratch', 'ТС1001'): {D('2026-06-28'): None}})
    group = Group.objects.get(pk=ctx.group_by_eduson[5])
    sheet = Lesson.objects.get(group=group, submitted_by_token=SUBMITTED_BY_SHEETS)
    assert (sheet.lesson_date, sheet.lesson_number) == (D('2026-06-28'), Decimal('1'))
    day = PlannedLesson.objects.filter(group=group, scheduled_date=D('2026-06-28'))
    assert sorted(p.status for p in day) == sorted([DONE, CANCELLED])
    assert PlannedLesson.objects.get(pk=ctx.planned_by_session[100]).fact_lesson_id is None
    assert any('ТС1001' in line and 'отменённого' in line for line in ctx.report)
    with EdusonSource(db.path) as src:
        _, errors, _ = reconcile(src, ctx)
    assert errors == []


@pytest.mark.django_db
def test_without_sheets_behaviour_unchanged(tmp_path):
    with EdusonSource(_school(tmp_path).close()) as src:
        ctx = ImportContext.from_source(src, AS_OF)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
        _, errors, _ = reconcile(src, ctx)
    assert errors == []
    assert ctx.sheet_backfill is None
    assert not Lesson.objects.filter(submitted_by_token=SUBMITTED_BY_SHEETS).exists()
    assert Group.objects.get(pk=ctx.group_by_eduson[5]).lesson_number_offset == Decimal('3')
    assert not any('из таблиц' in line for line in ctx.report)


# ── сверка ──────────────────────────────────────────────────────────────────

def _line(lines: list[str], label: str) -> list[str]:
    """Колонки «Eduson», «KOTOKOD» строки таблицы сверки."""
    [line] = [x for x in lines if x.startswith(label)]
    return line[len(label):].split()[:2]


@pytest.mark.django_db
def test_reconcile_counts_sheet_lessons(tmp_path):
    with EdusonSource(_school(tmp_path).close()) as src:
        ctx = ImportContext.from_source(src, AS_OF, SCHOOL_MARKS)
        import_people(src, ctx)
        import_groups(src, ctx)
        import_lessons(src, ctx)
        lines, errors, _ = reconcile(src, ctx)
        assert errors == []
        assert _line(lines, 'из таблиц (до запуска)') == ['0', '3']
        assert _line(lines, 'проведённые уроки') == ['3', '6']
        assert any('отметок больше N — 1' in line for line in lines)

        Lesson.objects.filter(group_id=ctx.group_by_eduson[5], submitted_by_token=SUBMITTED_BY_SHEETS,
                              lesson_date=D('2026-06-03')).delete()
        _, errors, _ = reconcile(src, ctx)
    assert any(e.startswith('из таблиц (до запуска)') for e in errors)
    assert any('ТС1001' in e for e in errors)


# ── команда ─────────────────────────────────────────────────────────────────

@pytest.mark.django_db
def test_command_with_sheets_dir(tmp_path):
    db = _school(tmp_path)
    db.user('anna@example.com', 'teacher', teacher_id=1)
    root = write_sheets(tmp_path / 'sheets', {
        ('scratch', 2027): tab_csv({'ТС1001': {3: '', 10: '11:00'}}),
        ('scratch', 2026): tab_csv({'ТС1002': {4: '', 11: '', 18: '09:00'}}),
    })
    out = io.StringIO()
    call_command('import_eduson', str(db.close()), '--as-of', AS_OF.isoformat(), '--sheets-dir', str(root),
                 stdout=out)
    text = out.getvalue()
    assert 'Импорт Eduson завершён' in text
    assert 'из таблиц (до запуска старой системы): заведено 3 уроков в 2 группах' in text
    assert Lesson.objects.filter(submitted_by_token=SUBMITTED_BY_SHEETS).count() == 3
    for secret in ('Миша', 'Маша', 'Петя', 'Препод Тестов', '7999'):
        assert secret not in text


@pytest.mark.django_db
def test_command_sheets_dir_broken_stops_before_import(tmp_path):
    db = _school(tmp_path)
    root = write_sheets(tmp_path / 'sheets')
    (root / 'groups-2026-06.csv').write_text('<html>вход</html>', encoding='utf-8')
    with pytest.raises(CommandError, match='Таблицы Eduson'):
        call_command('import_eduson', str(db.close()), '--as-of', AS_OF.isoformat(), '--sheets-dir', str(root),
                     stdout=io.StringIO())
    assert not Group.objects.exists()
