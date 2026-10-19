"""
Тесты «Отчёта по переходимости» (apps.reports.builders.retention).

Как и остальные тесты reports, идут по свежей мигрированной test_eduson_test
(django_db_setup НЕ переопределён — см. conftest.py пакета).
"""
from __future__ import annotations

import datetime

import pytest
from django.db import connection

from apps.reports.builders import retention

MONTH = '2026-06'


@pytest.fixture
def world(renewals_fixture):
    """Направления, преподаватели, группы, членства и занятия месяца."""
    created = {'attendance': [], 'lesson': [], 'membership': [],
               'group': [], 'teacher': [], 'direction': []}

    class W:
        def __init__(self, base):
            self.base = base
            self.pipeline = base.pipeline()
            self.won = base.stage(self.pipeline, 'renewed', 'Продлён', 'won', sort_order=9)
            self.lost = base.stage(self.pipeline, 'churned', 'Ушёл', 'lost', sort_order=10)
            self.open = base.stage(self.pipeline, 'awaiting_renewal', 'Ждём продление',
                                   'decision', sort_order=5)

        def teacher(self, name):
            with connection.cursor() as cur:
                cur.execute('INSERT INTO teachers (name, active, created_at) '
                            'VALUES (%s, true, now()) RETURNING id', [name])
                tid = cur.fetchone()[0]
            created['teacher'].append(tid)
            return tid

        def direction(self, name):
            with connection.cursor() as cur:
                cur.execute('INSERT INTO directions (name, total_lessons, active) '
                            'VALUES (%s, 36, true) RETURNING id', [name])
                did = cur.fetchone()[0]
            created['direction'].append(did)
            return did

        def group(self, name, teacher_id, direction_id):
            with connection.cursor() as cur:
                cur.execute(
                    'INSERT INTO groups (name, direction_id, teacher_id, is_individual, '
                    'lesson_duration_minutes, active, lesson_number_offset) '
                    'VALUES (%s, %s, %s, false, 90, true, 0) RETURNING id',
                    [name, direction_id, teacher_id])
                gid = cur.fetchone()[0]
            created['group'].append(gid)
            return gid

        def enrol(self, group_id, student_id):
            with connection.cursor() as cur:
                cur.execute(
                    'INSERT INTO group_memberships (group_id, student_id, lessons_done, active) '
                    'VALUES (%s, %s, 0, true) RETURNING id', [group_id, student_id])
                created['membership'].append(cur.fetchone()[0])

        def lesson(self, group_id, teacher_id, student_id, date=f'{MONTH}-10'):
            """Занятие с отметкой — так ребёнок попадает в «детей за месяц»."""
            with connection.cursor() as cur:
                cur.execute(
                    'INSERT INTO lessons (group_id, teacher_id, lesson_date, lesson_number, '
                    'lesson_duration_minutes, lesson_type, submitted_at, submitted_by_token) '
                    "VALUES (%s, %s, %s, 1, 90, 'regular', now(), %s) RETURNING id",
                    [group_id, teacher_id, date, f'__ret_tok_{len(created["lesson"])}__'])
                lid = cur.fetchone()[0]
                created['lesson'].append(lid)
                cur.execute('INSERT INTO lesson_attendance (lesson_id, student_id, present) '
                            'VALUES (%s, %s, true)', [lid, student_id])
                created['attendance'].append((lid, student_id))
            return lid

        @staticmethod
        def stuck_due():
            """Опорная дата внутри месяца И заведомо просроченная."""
            return f'{MONTH}-01'

    yield W(renewals_fixture)

    with connection.cursor() as cur:
        for lid, sid in created['attendance']:
            cur.execute('DELETE FROM lesson_attendance WHERE lesson_id = %s AND student_id = %s',
                        [lid, sid])
        for lid in created['lesson']:
            cur.execute('DELETE FROM lessons WHERE id = %s', [lid])
        for mid in created['membership']:
            cur.execute('DELETE FROM group_memberships WHERE id = %s', [mid])
        for gid in created['group']:
            cur.execute('DELETE FROM groups WHERE id = %s', [gid])
        for tid in created['teacher']:
            cur.execute('DELETE FROM teachers WHERE id = %s', [tid])
        for did in created['direction']:
            cur.execute('DELETE FROM directions WHERE id = %s', [did])


@pytest.fixture
def scene(world, renewals_fixture):
    """Один преподаватель, одно направление, фабрика «ученик занимается в июне»."""
    teacher = world.teacher('__ret_teacher__')
    direction = world.direction('__ret_dir__')
    group = world.group('__ret_group__', teacher, direction)

    def _student(name, with_lesson=True):
        sid = renewals_fixture.student(name)
        world.enrol(group, sid)
        if with_lesson:
            world.lesson(group, teacher, sid)
        return sid

    _student.teacher = '__ret_teacher__'
    _student.direction = '__ret_dir__'
    return _student


def _block(rows: list[dict], name: str) -> dict:
    return next(r for r in rows if r['name'] == name)


# ---------------------------------------------------------------------------
# Сетка циклов
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_all_cycles_present_including_empty(world, renewals_fixture, scene):
    """
    Развёртка по ВСЕМ циклам: сетка идёт от 1 до максимального цикла в базе,
    и циклы без единого продления присутствуют с нулём. Иначе ширина таблицы
    прыгала бы от месяца к месяцу и «Ц12» в июне означала бы не то же, что
    «Ц12» в июле — месяцы стало бы нельзя класть рядом.
    """
    student = scene('__ret_gap__')
    renewals_fixture.deal(student, world.pipeline, world.won, 7,
                          closed_at=f'{MONTH}-15')

    data = retention.collect(MONTH)

    assert data['cycles'][0] == 1
    assert data['cycles'] == list(range(1, data['cycles'][-1] + 1))
    assert data['cycles'][-1] >= 7
    counts = _block(data['directions'], '__ret_dir__')['counts']['won']
    assert counts[7] == 1
    # Пустые циклы присутствуют явными нулями, а не отсутствуют.
    assert counts[1] == 0
    assert set(counts) == set(data['cycles'])


@pytest.mark.django_db
def test_cycle_grid_does_not_shrink_for_quiet_month(world, renewals_fixture, scene):
    """Сетка не зависит от выбранного месяца: в пустом месяце те же колонки."""
    student = scene('__ret_wide__')
    renewals_fixture.deal(student, world.pipeline, world.won, 12, closed_at=f'{MONTH}-15')

    busy = retention.collect(MONTH)
    quiet = retention.collect('2026-01')

    assert quiet['cycles'] == busy['cycles']


# ---------------------------------------------------------------------------
# Три показателя
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_three_measures_split_by_cycle(world, renewals_fixture, scene):
    """Продлились / ушли / зависли — каждый в свой цикл."""
    a, b, c = (scene(f'__ret_m_{x}__') for x in 'abc')
    renewals_fixture.deal(a, world.pipeline, world.won, 3, closed_at=f'{MONTH}-15')
    renewals_fixture.deal(b, world.pipeline, world.lost, 5, closed_at=f'{MONTH}-20')
    renewals_fixture.deal(c, world.pipeline, world.open, 8, due_at=world.stuck_due())

    counts = _block(retention.collect(MONTH)['directions'], '__ret_dir__')['counts']

    assert counts['won'][3] == 1
    assert counts['lost'][5] == 1
    assert counts['stuck'][8] == 1


@pytest.mark.django_db
def test_stuck_belongs_to_month_when_decision_was_due(world, renewals_fixture, scene):
    """
    Зависание — не событие с датой, а несостоявшееся решение, поэтому относится
    к месяцу ОПОРНОЙ даты (когда цикл должен был решиться), а не к сегодня.
    Сделка, просроченная с мая, в июньский отчёт не попадает.
    """
    student = scene('__ret_may__')
    renewals_fixture.deal(student, world.pipeline, world.open, 4, due_at='2026-05-10')

    june = _block(retention.collect(MONTH)['directions'], '__ret_dir__')['counts']['stuck']
    may = _block(retention.collect('2026-05')['directions'], '__ret_dir__')['counts']['stuck']

    assert june[4] == 0
    assert may[4] == 1


@pytest.mark.django_db
def test_fresh_open_deal_is_not_stuck(world, renewals_fixture, scene):
    """Свежая открытая сделка — «в работе», в потери не идёт."""
    student = scene('__ret_fresh__')
    today = datetime.date.today()
    renewals_fixture.deal(student, world.pipeline, world.open, 2, due_at=today)

    data = retention.collect(f'{today:%Y-%m}')
    block = next((r for r in data['directions'] if r['name'] == '__ret_dir__'), None)

    assert block is None or block['totals']['stuck'] == 0


# ---------------------------------------------------------------------------
# Детей за месяц
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_students_counted_by_actual_lessons(world, renewals_fixture, scene):
    """
    «Детей за месяц» — по фактическим занятиям, а не по членствам: членство
    остаётся у замороженного, и такой ребёнок раздувал бы базу.
    """
    scene('__ret_active__')
    scene('__ret_frozen__', with_lesson=False)

    block = _block(retention.collect(MONTH)['directions'], '__ret_dir__')

    assert block['students'] == 1


@pytest.mark.django_db
def test_renewal_falls_back_to_membership_without_lessons(world, renewals_fixture, scene):
    """
    Ребёнок без занятий в месяце (заморозка, оплата вперёд) всё равно попадает
    в строку продлений — через членство. Без отката его продление потерялось бы
    вовсе и итог по направлениям разошёлся бы со школьным.
    """
    student = scene('__ret_prepaid__', with_lesson=False)
    renewals_fixture.deal(student, world.pipeline, world.won, 6, closed_at=f'{MONTH}-15')

    data = retention.collect(MONTH)
    block = _block(data['directions'], '__ret_dir__')

    assert block['counts']['won'][6] == 1
    assert block['students'] == 0  # занятий не было — в число детей не входит
    assert data['total']['totals']['won'] >= 1


# ---------------------------------------------------------------------------
# Разрезы и итог
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_same_deal_counted_for_both_teachers(world, renewals_fixture):
    """
    Ребёнок у двух преподавателей засчитывается обоим: сделка привязана к
    ученику, разделить её данные не позволяют. Отсюда и предупреждение в шапке
    листа, что колонку нельзя складывать.
    """
    t1, t2 = world.teacher('__ret_t1__'), world.teacher('__ret_t2__')
    direction = world.direction('__ret_dir_two__')
    g1 = world.group('__ret_g1__', t1, direction)
    g2 = world.group('__ret_g2__', t2, direction)
    student = renewals_fixture.student('__ret_two__')
    world.enrol(g1, student)
    world.enrol(g2, student)
    world.lesson(g1, t1, student)
    world.lesson(g2, t2, student)
    renewals_fixture.deal(student, world.pipeline, world.won, 2, closed_at=f'{MONTH}-15')

    teachers = retention.collect(MONTH)['teachers']

    assert _block(teachers, '__ret_t1__')['counts']['won'][2] == 1
    assert _block(teachers, '__ret_t2__')['counts']['won'][2] == 1


@pytest.mark.django_db
def test_school_total_counts_each_deal_once(world, renewals_fixture):
    """Итог школы не удваивает сделку ребёнка, занимающегося у двоих."""
    t1, t2 = world.teacher('__ret_tt1__'), world.teacher('__ret_tt2__')
    direction = world.direction('__ret_dir_tot__')
    g1 = world.group('__ret_gg1__', t1, direction)
    g2 = world.group('__ret_gg2__', t2, direction)
    student = renewals_fixture.student('__ret_tot__')
    world.enrol(g1, student)
    world.enrol(g2, student)
    world.lesson(g1, t1, student)
    world.lesson(g2, t2, student)
    renewals_fixture.deal(student, world.pipeline, world.won, 2, closed_at=f'{MONTH}-15')

    data = retention.collect(MONTH)
    by_teacher = sum(b['counts']['won'][2] for b in data['teachers']
                     if b['name'].startswith('__ret_tt'))

    assert by_teacher == 2          # в разрезе — обоим
    assert data['total']['counts']['won'][2] == 1   # в итоге — один раз


@pytest.mark.django_db
def test_other_month_events_excluded(world, renewals_fixture, scene):
    student = scene('__ret_other_month__')
    renewals_fixture.deal(student, world.pipeline, world.won, 3, closed_at='2026-07-15')

    counts = _block(retention.collect(MONTH)['directions'], '__ret_dir__')['counts']

    assert counts['won'][3] == 0


# ---------------------------------------------------------------------------
# Книга
# ---------------------------------------------------------------------------

@pytest.mark.django_db
def test_workbook_shape(world, renewals_fixture, scene):
    import io

    from openpyxl import load_workbook

    student = scene('__ret_wb__')
    renewals_fixture.deal(student, world.pipeline, world.won, 2, closed_at=f'{MONTH}-15')

    content, events, filename = retention.build(MONTH)

    assert filename == f'retention_{MONTH}.xlsx'
    assert events >= 1
    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames == ['Направления', 'Преподаватели']

    ws = wb['Направления']
    cycles = retention.collect(MONTH)['cycles']
    # 4 фиксированные колонки + все циклы.
    assert ws.max_column == 4 + len(cycles)
    # Каждая сущность занимает ровно три строки — по числу показателей.
    labels = [ws.cell(row=r, column=2).value for r in range(7, 10)]
    assert labels == ['Продлились', 'Ушли', 'Зависли']


@pytest.mark.django_db
def test_total_column_is_a_formula(world, renewals_fixture, scene):
    """Итог строки — формула по её циклам: лист обязан сойтись сам с собой,
    если кто-то поправит ячейку под собой."""
    import io

    from openpyxl import load_workbook

    student = scene('__ret_formula__')
    renewals_fixture.deal(student, world.pipeline, world.won, 2, closed_at=f'{MONTH}-15')

    content, _events, _name = retention.build(MONTH)
    ws = load_workbook(io.BytesIO(content))['Направления']

    assert str(ws.cell(row=7, column=4).value).startswith('=SUM(')
