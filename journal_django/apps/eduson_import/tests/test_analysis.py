"""Анализ данных Eduson (analysis.py): на этих выводах стоят нумерация уроков,
пройденное до учёта, замены и переносы. Базы — выдуманные, через фабрику, которая
повторяет действия legacy (и их след в audit_log)."""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from apps.eduson_import.analysis import (
    deleted_session_dates,
    fake_held_ids,
    group_teacher_timeline,
    held_moments,
    moved_from_dates,
    pre_system_lessons,
    substituted_teacher_by_session,
)
from apps.eduson_import.context import ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.tests.factory import EdusonDB

AS_OF = dt.date(2026, 10, 18)
D = dt.date.fromisoformat


def _src(db: EdusonDB) -> EdusonSource:
    return EdusonSource(db.close())


# ── фиктивные held ──────────────────────────────────────────────────────────

def test_fake_held_are_only_held_after_as_of(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(10, 1, '2026-10-18')                          # день импорта — настоящий
    db.session(11, 1, '2026-10-19')                          # завтра — фиктивный
    db.session(12, 1, '2027-06-01', time='')                 # артефакт первичной загрузки
    db.session(13, 1, '2027-06-08', status='scheduled')      # будущее по плану — не held
    db.session(14, 1, '2027-06-15', status='cancelled')
    with _src(db) as src:
        assert fake_held_ids(src, AS_OF) == {11, 12}


def test_fake_held_bad_date_is_import_error(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(10, 1, '18.10.2026')
    with _src(db) as src, pytest.raises(ImportDataError, match='#10'):
        fake_held_ids(src, AS_OF)


# ── пройдено до учёта N ─────────────────────────────────────────────────────

def _n(db: EdusonDB) -> dict[int, Decimal]:
    with _src(db) as src:
        n = pre_system_lessons(src, AS_OF)
    assert all(isinstance(v, Decimal) for v in n.values())
    return n


def test_pre_system_fake_numbered_first_real_continue(tmp_path):
    """(а) Фиктивные held заняли №1..3, настоящие продолжают с №4 — фиктивные уже
    внутри 1..L−1, второй раз не считаются: N = 3, а не 3 + 3."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, done=7)   # lessons_done Eduson — двойной счёт, не берём
    for no, day in ((1, '2027-06-01'), (2, '2027-06-08'), (3, '2027-06-15')):
        db.session(None, 1, day, lesson_no=no)
    db.session(None, 1, '2026-09-01', lesson_no=4)
    db.session(None, 1, '2026-09-08', lesson_no=5)
    assert _n(db) == {1: Decimal(3)}


@pytest.mark.parametrize('fake_numbers', [[2, 3, 4], [None, None, None]])
def test_pre_system_real_first_lesson_fake_after(tmp_path, fake_numbers):
    """(б) Настоящий №1, фиктивные с номерами 2..k или без номеров: N = F."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(None, 1, '2026-09-01', lesson_no=1)
    for i, no in enumerate(fake_numbers):
        db.session(None, 1, f'2027-06-0{i + 1}', lesson_no=no)
    assert _n(db) == {1: Decimal(3)}


def test_pre_system_only_lesson_no_gap(tmp_path):
    """(в) Только задел: первый учтённый урок №13, фиктивных нет — N = 12.
    Не-held и held без номера в L не участвуют."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(None, 1, '2026-09-02', lesson_no=13)
    db.session(None, 1, '2026-09-09', lesson_no=14)
    db.session(None, 1, '2026-09-16', lesson_no=None)
    db.session(None, 1, '2026-09-23', status='scheduled', lesson_no=2)
    db.session(None, 1, '2026-09-30', status='cancelled', lesson_no=1)
    assert _n(db) == {1: Decimal(12)}


def test_pre_system_unnumbered_fake_plus_gap(tmp_path):
    """(г) Фиктивные без номеров + задел: N = (L − 1) + F_без_номера; фиктивный с
    номером < L — урок из 1..L−1, отдельно не считается."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(None, 1, '2026-09-03', lesson_no=5)
    db.session(None, 1, '2027-06-01', lesson_no=None)
    db.session(None, 1, '2027-06-08', lesson_no=None)
    db.session(None, 1, '2027-06-15', lesson_no=2)        # внутри 1..4
    db.session(None, 1, '2027-06-22', lesson_no=9)        # после первого учтённого
    assert _n(db) == {1: Decimal(4 + 2 + 1)}


def test_pre_system_edge_groups(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)           # без проведённых
    db.session(None, 1, '2026-11-01', status='scheduled', lesson_no=1)
    db.group(2, 'ТС1002', teacher_id=5)           # только фиктивные: L = 1, все считаются
    db.session(None, 2, '2027-06-01', lesson_no=1)
    db.session(None, 2, '2027-06-08', lesson_no=None)
    db.group(3, 'ТС1003', teacher_id=5)           # номер 0 — задела нет, не минус
    db.session(None, 3, '2026-09-04', lesson_no=0)
    assert _n(db) == {1: Decimal(0), 2: Decimal(2), 3: Decimal(0)}


# ── преподаватель группы на дату ────────────────────────────────────────────

def _ts(day: str, hhmm: str = '09:00') -> str:
    """Метка audit_log в UTC (как toISOString) для момента МСК day hhmm."""
    msk = dt.datetime.fromisoformat(f'{day}T{hhmm}')
    return (msk - dt.timedelta(hours=3)).strftime('%Y-%m-%dT%H:%M:%S.000Z')


def test_teacher_without_history_is_current(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2020-01-01')) == 5
    assert tl.group_teacher_on(1, D('2030-01-01')) == 5
    assert tl.changed_groups() == set()


def test_teacher_changed_twice(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-08-10')})
    db.reschedule_group(1, from_date='2026-09-01', dows=[3], time='16:00', teacher_id=7,
                        ts=_ts('2026-09-01', '12:00'))
    db.freeze_group(1, freeze_from='2026-09-20', resume_date='2026-10-01', ts=_ts('2026-09-15'))  # не меняет
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-08-09')) == 5
    assert tl.group_teacher_on(1, D('2026-08-10'), dt.time(8, 0)) == 5    # до правки в 9:00 МСК
    assert tl.group_teacher_on(1, D('2026-08-10'), dt.time(15, 0)) == 6
    # reschedule со сменой преподавателя действует с fromDate 00:00, а не с момента записи
    assert tl.group_teacher_on(1, D('2026-08-31'), dt.time(23, 0)) == 6
    assert tl.group_teacher_on(1, D('2026-09-01'), dt.time(10, 0)) == 7
    assert tl.group_teacher_on(1, D('2026-09-01'), dt.time(15, 0)) == 7
    assert tl.group_teacher_on(1, D('2026-12-01')) == 7
    assert tl.changed_groups() == {1}
    assert tl.teacher_ids() == {5, 6, 7}


def test_teacher_restored_by_revert(tmp_path):
    """Откат пишет запись без before/after: значение после отката берётся из
    снимка откатываемой записи."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    upd = db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-08-10')})
    db.update_group(1, notes='без смены преподавателя', audit_kw={'ts': _ts('2026-08-20')})
    db.revert(upd, ts=_ts('2026-09-01'))
    db.update_group(1, teacher_id=8, audit_kw={'ts': _ts('2026-10-01')})
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-08-15')) == 6
    assert tl.group_teacher_on(1, D('2026-08-25')) == 6
    assert tl.group_teacher_on(1, D('2026-09-15')) == 5
    assert tl.group_teacher_on(1, D('2026-10-02')) == 8


def test_teacher_change_reverted_right_after(tmp_path):
    """Откат ИМЕННО этого действия сразу после него: окно между правкой и откатом
    отдаётся прежнему преподавателю (новое значение журнал не хранит, а откат
    признал прежнего правильным); после отката — снова прежний."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    upd = db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-08-10', '09:00')})
    db.revert(upd, ts=_ts('2026-08-10', '09:05'))
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-08-10'), dt.time(9, 2)) == 5
    assert tl.group_teacher_on(1, D('2026-08-11')) == 5
    assert tl.changed_groups() == set() and tl.teacher_ids() == {5}


def test_teacher_restored_by_reschedule_revert(tmp_path):
    """Откат reschedule идёт путём UPDATE … teacher_id=before.group.teacher_id."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(None, 1, '2026-09-07', status='scheduled', lesson_no=1)
    res = db.reschedule_group(1, from_date='2026-09-01', dows=[3], time='16:00', teacher_id=7,
                              ts=_ts('2026-08-20'))
    db.update_group(1, notes='после смены', audit_kw={'ts': _ts('2026-08-25')})
    db.revert(res, ts=_ts('2026-09-01'))
    with _src(db) as src:
        assert src.rows('student_groups')[0]['teacher_id'] == 5      # фабрика повторила UPDATE legacy
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-08-19')) == 5
    assert tl.group_teacher_on(1, D('2026-08-22')) == 7
    assert tl.group_teacher_on(1, D('2026-08-28')) == 7
    assert tl.group_teacher_on(1, D('2026-09-02')) == 5


def test_teacher_can_be_none(tmp_path):
    """legacy PATCH принимает teacherId: null — период без преподавателя бывает."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=None)
    db.update_group(1, teacher_id=None, notes='x', audit_kw={'ts': _ts('2026-08-10')})
    db.update_group(1, teacher_id=5, audit_kw={'ts': _ts('2026-09-10')})
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-08-01')) is None
    assert tl.group_teacher_on(1, D('2026-10-01')) == 5


def test_teacher_history_ignores_other_groups_and_session_actions(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.group(2, 'ТС1002', teacher_id=9)
    db.session(100, 1, '2026-08-01', status='scheduled')
    db.move_session(100, date='2026-08-02', teacher_id=6, ts=_ts('2026-07-20'))  # разовая замена — не группа
    db.update_group(2, teacher_id=10, audit_kw={'ts': _ts('2026-08-10')})
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-07-01')) == 5
    assert tl.group_teacher_on(2, D('2026-07-01')) == 9
    assert tl.group_teacher_on(2, D('2026-09-01')) == 10
    with pytest.raises(KeyError):
        tl.group_teacher_on(3, D('2026-07-01'))


# ── перенос: дата «до» ─────────────────────────────────────────────────────

def test_moved_from_last_date_move(tmp_path):
    """before.date ПОСЛЕДНЕГО переноса, сменившего дату (спека §5.4)."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(100, 1, '2026-07-10', status='scheduled')
    db.move_session(100, date='2026-07-12')
    db.move_session(100, date='2026-07-14')                 # последний перенос по дате: 12 → 14
    db.session(101, 1, '2026-07-17', status='scheduled')
    db.move_session(101, date='2026-07-18')
    db.move_session(101, time='18:00')                     # следом только время — дата «до» та же
    db.session(102, 1, '2026-07-24', status='scheduled')
    db.move_session(102, time='19:00')                     # только время — переноса по дате нет
    db.session(103, 1, '2026-07-31', status='scheduled')
    db.revert(db.move_session(103, date='2026-08-02'))     # откаченный перенос не в счёт
    db.session(104, 1, '2026-08-07', status='scheduled')   # не переносили
    with _src(db) as src:
        assert moved_from_dates(src) == {100: D('2026-07-12'), 101: D('2026-07-17')}


def test_moved_from_uses_date_at_record_time_not_current(tmp_path):
    """Дата «после» переноса — на момент записи (её фиксирует before следующей правки
    этого урока), а не текущая: иначе перенос только времени, после которого дату
    поправили карточкой урока, выглядел бы переносом по дате."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(100, 1, '2026-07-10', status='scheduled')
    db.move_session(100, time='18:00')                      # только время
    db.patch_session(100, date='2026-07-15')                # дату правили не переносом
    db.session(101, 1, '2026-07-17', status='scheduled')
    db.move_session(101, date='2026-07-12')
    db.move_session(101, date='2026-07-17', time='17:00')   # вернули на исходную дату
    with _src(db) as src:
        assert moved_from_dates(src) == {101: D('2026-07-12')}


# ── отчёты на удалённые сессии ──────────────────────────────────────────────

def test_deleted_session_dates(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.group(2, 'ТС1002', teacher_id=5)
    db.session(100, 1, '2026-07-10', status='scheduled')
    db.session(101, 2, '2026-07-11', status='scheduled')
    db.session(102, 1, '2026-07-12', status='scheduled')
    db.session(103, 1, '2026-07-13', status='scheduled')
    db.report(100, 1, 5, took_place='no', understanding=None, interest=None)
    db.cancel_keep_total(100)
    db.delete_session(101)
    db.revert(db.delete_session(102))      # восстановлена — уже не удалённая
    with _src(db) as src:
        assert deleted_session_dates(src) == {100: (1, D('2026-07-10')), 101: (2, D('2026-07-11'))}


def test_deleted_session_dates_from_replaced_schedule(tmp_path):
    """reschedule/freeze снимают будущие scheduled и пишут их в before.oldSessions —
    отзыв на такой урок висит, дата берётся из снимка."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, slots=[{'dow': 1, 'time': '15:00'}])
    db.session(200, 1, '2026-09-07', status='scheduled', lesson_no=3)
    db.session(201, 1, '2026-09-14', status='scheduled', lesson_no=4)
    db.report(200, 1, 5, took_place='no', understanding=None, interest=None)
    db.reschedule_group(1, from_date='2026-09-01', dows=[3], time='16:00')
    db.session(300, 1, '2026-10-05', status='scheduled', lesson_no=9)
    db.freeze_group(1, freeze_from='2026-10-01', resume_date='2026-11-01')
    with _src(db) as src:
        got = deleted_session_dates(src)
        alive = {s['id'] for s in src.rows('sessions')}
    assert got[200] == (1, D('2026-09-07')) and got[201] == (1, D('2026-09-14'))
    assert got[300] == (1, D('2026-10-05'))
    assert not alive & got.keys()          # новые строки reschedule/freeze — не удалённые


# ── reschedule со сменой преподавателя: граница с fromDate ──────────────────

def test_reschedule_teacher_change_starts_from_from_date(tmp_path):
    """legacy rescheduleFuture переводит группу с fromDate, а запись в журнале
    появляется позже — урок в день fromDate ДО момента записи уже у нового."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, slots=[{'dow': 1, 'time': '15:00'}])
    db.reschedule_group(1, from_date='2026-09-07', dows=[1], time='15:00', teacher_id=6,
                        ts=_ts('2026-09-09', '12:00'))
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-09-06'), dt.time(15, 0)) == 5
    assert tl.group_teacher_on(1, D('2026-09-07'), dt.time(15, 0)) == 6     # до записи, но с fromDate
    assert tl.group_teacher_on(1, D('2026-09-08')) == 6


def test_reschedule_without_teacher_change_keeps_boundary_at_ts(tmp_path):
    """reschedule без смены преподавателя не двигает историю, а смена карточкой после
    него — по моменту своей записи."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, slots=[{'dow': 1, 'time': '15:00'}])
    db.reschedule_group(1, from_date='2026-09-01', dows=[3], time='16:00', ts=_ts('2026-09-09'))
    db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-09-20')})
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-09-02')) == 5
    assert tl.group_teacher_on(1, D('2026-09-19')) == 5
    assert tl.group_teacher_on(1, D('2026-09-21')) == 6


def test_reschedule_from_date_never_crosses_previous_point(tmp_path):
    """fromDate раньше предыдущей смены — граница не уходит за неё (история не путается)."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, slots=[{'dow': 1, 'time': '15:00'}])
    db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-09-10')})
    db.reschedule_group(1, from_date='2026-09-01', dows=[1], time='15:00', teacher_id=7,
                        ts=_ts('2026-09-15'))
    with _src(db) as src:
        tl = group_teacher_timeline(src)
    assert tl.group_teacher_on(1, D('2026-09-05')) == 5
    assert tl.group_teacher_on(1, D('2026-09-10'), dt.time(10, 0)) == 7    # с момента прошлой правки
    assert tl.group_teacher_on(1, D('2026-09-20')) == 7


# ── замена на уроке: кто выставил sessions.teacher_id ───────────────────────

def test_substituted_only_by_substitution_actions(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5, slots=[{'dow': 1, 'time': '15:00'}], package=6)
    db.update_progress(1, lessons_done=0, from_date='2026-09-07')   # отметка генерации: teacher_id=5
    ids = [s['id'] for s in db._rows('SELECT id FROM sessions WHERE group_id=1 ORDER BY date')]
    a, b, c, d, e, f = ids
    db.move_session(a, teacher_id=7)                        # замена переносом
    db.move_session(b, date='2026-09-15')                   # перенос без смены преподавателя
    db.revert(db.move_session(c, teacher_id=7))             # откаченная замена
    db.patch_session(d, teacher_id=8)                       # замена правкой карточки урока
    db.patch_session(e, teacher_id=7)
    db.patch_session(e, teacher_id=5)                       # вернули прежнего — тоже действие над уроком
    db.update_group(1, teacher_id=6, audit_kw={'ts': _ts('2026-09-01')})   # f: старая отметка 5 ≠ группе
    with _src(db) as src:
        got = substituted_teacher_by_session(src)
        current = {s['id']: s['teacher_id'] for s in src.rows('sessions')}
    # e: значение выставила правка урока; замена ли это — решает шаг уроков сравнением
    # с преподавателем группы на дату.
    assert got == {a: 7, d: 8, e: 5}
    assert current[f] == 5 and f not in got                 # отметка генерации — не замена


def test_substituted_later_move_keeps_substitute(tmp_path):
    """Замену потом перенесли по дате, не трогая преподавателя — замена остаётся."""
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    db.session(100, 1, '2026-09-07', status='scheduled', teacher_id=5)
    db.move_session(100, teacher_id=7)
    db.move_session(100, date='2026-09-08')
    with _src(db) as src:
        assert substituted_teacher_by_session(src) == {100: 7}


# ── момент перехода в held ──────────────────────────────────────────────────

def test_held_moments(tmp_path):
    db = EdusonDB(tmp_path / 'e.db')
    db.group(1, 'ТС1001', teacher_id=5)
    for sid in (100, 101, 102, 103, 104):
        db.session(sid, 1, '2026-09-07', status='scheduled')
    db.set_status(100, 'held', ts=_ts('2026-09-07', '16:00'))
    db.submit_report(101, submitted_at='2026-09-07T14:30:00.000Z')          # отчёт «состоялся» → held
    db.set_status(102, 'held', ts=_ts('2026-09-07', '16:00'))
    db.set_status(102, 'scheduled', ts=_ts('2026-09-07', '17:00'))
    db.set_status(102, 'held', ts=_ts('2026-09-08', '10:00'))               # последний переход
    db.revert(db.set_status(103, 'held', ts=_ts('2026-09-07', '16:00')))     # откачен
    db.submit_report(104, took_place='no', submitted_at='2026-09-07T14:30:00.000Z')  # не состоялся
    with _src(db) as src:
        got = held_moments(src)
    utc = dt.timezone.utc
    assert got == {100: dt.datetime(2026, 9, 7, 13, 0, tzinfo=utc),
                   101: dt.datetime(2026, 9, 7, 14, 30, tzinfo=utc),
                   102: dt.datetime(2026, 9, 8, 7, 0, tzinfo=utc)}
