"""Фабрика выдуманной базы пишет то же, что пишет legacy Eduson: на её формате будут
проверяться все шаги импорта, поэтому сам формат и побочные эффекты действий
(хвост, перенумерация, пересоздание участников) проверяются отдельно."""
from __future__ import annotations

import json

import pytest

from apps.accounts.hashers import wrap_eduson_bcrypt
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.tests.factory import EdusonDB, bcrypt_hash, generate_session_descriptors

# 2026-07-06 — понедельник (dow=1 в нотации JS: 0=Вс)
MON = '2026-07-06'


def _read(path, table, **kw):
    with EdusonSource(path) as src:
        return src.rows(table, **kw)


def _sessions(path):
    return [(s['date'], s['time'], s['status'], s['type'], s['lesson_no'])
            for s in _read(path, 'sessions', order_by='date')]


def test_bcrypt_hash_is_bcryptjs_format_and_wrappable():
    h = bcrypt_hash('выдуманный-пароль')
    assert h.startswith('$2a$04$') and len(h) == 60
    assert wrap_eduson_bcrypt(h).startswith('pbkdf2_wrapped_bcrypt$')


def test_group_slots_stored_as_json(tmp_path):
    slots = [{'dow': 1, 'time': '15:00'}, {'dow': 4, 'time': '16:30'}]
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', slots=slots, notes='заметка')
        db.group(2, 'ТС1002')
    g1, g2 = _read(tmp_path / 'e.db', 'student_groups')
    assert json.loads(g1['schedule_slots']) == slots and g1['notes'] == 'заметка'
    assert g2['schedule_slots'] is None and g2['lessons_left'] == 12


def test_empty_participant_like_legacy_create_group(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', amo_url='https://crm.example/leads/1')
        db.empty_participant(1, amo_url='https://crm.example/leads/1')
    (row,) = _read(tmp_path / 'e.db', 'participants')
    assert row['age'] is None and row['amo_url'] == 'https://crm.example/leads/1'
    assert all(row[f] == '' for f in ('child_name', 'child_tg', 'child_phone', 'child_email',
                                      'parent_name', 'parent_phone', 'parent_tg', 'parent_email'))


def test_audit_ts_strictly_increasing(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        for _ in range(3):
            db.audit('x')
    ts = [a['ts'] for a in _read(tmp_path / 'e.db', 'audit_log')]
    assert ts == sorted(ts) and len(set(ts)) == 3 and ts[0].endswith('.000Z')


def test_generate_descriptors_by_slots():
    out = generate_session_descriptors(MON, [{'dow': 4, 'time': '16:00'}, {'dow': 1, 'time': '15:00'}], 3, 5)
    assert out == [{'date': '2026-07-06', 'time': '15:00', 'lesson_no': 5},
                   {'date': '2026-07-09', 'time': '16:00', 'lesson_no': 6},
                   {'date': '2026-07-13', 'time': '15:00', 'lesson_no': 7}]


def test_move_session_like_legacy(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', teacher_id=5)
        db.session(100, 1, '2026-07-10', time='15:00', status='scheduled', lesson_no=4)
        db.move_session(100, date='2026-07-12', time='17:00', teacher_id=7)
        db.session(101, 1, '2026-07-17', status='scheduled', type='makeup', duration=45)
        db.move_session(101, time='18:00')
    s100, s101 = _read(tmp_path / 'e.db', 'sessions')
    a1, a2 = _read(tmp_path / 'e.db', 'audit_log')
    assert (s100['date'], s100['time'], s100['teacher_id'], s100['type']) == ('2026-07-12', '17:00', 7, 'moved')
    assert s101['type'] == 'makeup'  # отработка остаётся отработкой
    assert (a1['action'], a1['entity_type'], a1['entity_id'], a1['code']) == ('move_session', 'session', 100,
                                                                              'ТС1001')
    assert a1['summary'] == 'Перенос ТС1001 №4: 2026-07-10 15:00 → 2026-07-12 17:00'
    assert json.loads(a1['before_json']) == {'date': '2026-07-10', 'time': '15:00', 'teacher_id': None,
                                             'type': 'regular'}
    assert json.loads(a2['before_json'])['date'] == '2026-07-17'  # перенос только времени


def test_makeup_like_legacy(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', teacher_id=5)
        sid = db.makeup(1, '2026-07-08', '12:00', lesson_no=3)
    (s,) = _read(tmp_path / 'e.db', 'sessions')
    (a,) = _read(tmp_path / 'e.db', 'audit_log')
    assert (s['id'], s['type'], s['status'], s['duration_min'], s['lesson_no'], s['teacher_id']) == \
        (sid, 'makeup', 'scheduled', 45, 3, 5)
    assert a['action'] == 'makeup' and a['entity_id'] == sid and json.loads(a['after_json']) == {'id': sid}
    assert a['before_json'] is None


def _group_with_plan(db, *, held=1, scheduled=3, lessons_done=0):
    # понедельники с 2026-07-06: held, затем scheduled; номера по порядку
    db.group(1, 'ТС1001', teacher_id=5, dow=1, time='15:00', slots=[{'dow': 1, 'time': '15:00'}],
             done=lessons_done)
    dates = ['2026-07-06', '2026-07-13', '2026-07-20', '2026-07-27', '2026-08-03', '2026-08-10']
    ids = []
    for i in range(held + scheduled):
        ids.append(db.session(None, 1, dates[i], status='held' if i < held else 'scheduled',
                              lesson_no=lessons_done + i + 1))
    return ids


def test_cancel_keep_total_appends_tail_and_renumbers(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        ids = _group_with_plan(db, held=1, scheduled=3, lessons_done=2)
        db.report(ids[1], 1, 5, comment='урок удалят, отзыв останется')
        db.cancel_keep_total(ids[1])
    assert _sessions(tmp_path / 'e.db') == [
        ('2026-07-06', '15:00', 'held', 'regular', 3),
        ('2026-07-20', '15:00', 'scheduled', 'regular', 4),
        ('2026-07-27', '15:00', 'scheduled', 'regular', 5),
        ('2026-08-03', '15:00', 'scheduled', 'regular', 6),   # хвост: следующий слот после последнего
    ]
    tail = _read(tmp_path / 'e.db', 'sessions')[-1]
    assert tail['teacher_id'] == 5 and tail['duration_min'] == 60
    (report,) = _read(tmp_path / 'e.db', 'lesson_reports')
    assert report['session_id'] == ids[1]  # висячая ссылка, как в боевой базе
    (entry,) = _read(tmp_path / 'e.db', 'audit_log')
    before = json.loads(entry['before_json'])
    assert entry['action'] == 'cancel_keep_total' and entry['entity_id'] == ids[1]
    assert before['id'] == ids[1] and before['date'] == '2026-07-13' and before['lesson_no'] == 4
    assert entry['summary'] == ('Урок ТС1001 №4 (2026-07-13 15:00) не состоится, перенесён в конец → '
                                '2026-08-03 15:00 (тотал сохранён)')


def test_delete_session_no_tail(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        ids = _group_with_plan(db, held=1, scheduled=2)
        db.delete_session(ids[1])
    assert [s[4] for s in _sessions(tmp_path / 'e.db')] == [1, 3]  # без хвоста и без перенумерации
    (entry,) = _read(tmp_path / 'e.db', 'audit_log')
    assert entry['action'] == 'delete_session' and json.loads(entry['before_json'])['id'] == ids[1]
    assert entry['summary'] == 'Удалён урок ТС1001 №2 (2026-07-13 15:00)'


def test_extra_lesson_trims_tail_and_renumbers(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        _group_with_plan(db, held=1, scheduled=3)
        new_id = db.extra_lesson(1, '2026-07-08')
    assert _sessions(tmp_path / 'e.db') == [
        ('2026-07-06', '15:00', 'held', 'regular', 1),
        ('2026-07-08', '15:00', 'scheduled', 'regular', 2),
        ('2026-07-13', '15:00', 'scheduled', 'regular', 3),
        ('2026-07-20', '15:00', 'scheduled', 'regular', 4),
    ]
    (entry,) = _read(tmp_path / 'e.db', 'audit_log')
    assert entry['action'] == 'extra_lesson' and entry['entity_type'] == 'group' and entry['entity_id'] == 1
    assert entry['summary'] == 'Доп. занятие ТС1001 на 2026-07-08 15:00 (тотал сохранён: снят хвост 2026-07-27 15:00)'
    assert entry['before_json'] is None and entry['after_json'] is None
    assert new_id in {s['id'] for s in _read(tmp_path / 'e.db', 'sessions')}


def test_update_group_teacher_change_trace(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', teacher_id=5)
        db.update_group(1, teacher_id=6)
        assert db.update_group(1) is None  # пустая правка — без записи, как в legacy
    (group,) = _read(tmp_path / 'e.db', 'student_groups')
    (upd,) = _read(tmp_path / 'e.db', 'audit_log')
    assert group['teacher_id'] == 6
    assert upd['summary'] == 'Изменена карточка ТС1001'
    before = json.loads(upd['before_json'])
    assert before['group']['teacher_id'] == 5 and 'participants' not in before


def test_update_group_replaces_participants_with_new_ids(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001')
        old = db.participant(1, 'Миша', 'Анна Тестова', '79990000000', 10)
        db.update_group(1, participants=[{'child_name': 'Миша', 'parent_name': 'Анна Тестова',
                                          'parent_phone': '79990000000', 'age': 11}])
    (row,) = _read(tmp_path / 'e.db', 'participants')
    assert row['id'] != old and row['age'] == 11
    before = json.loads(_read(tmp_path / 'e.db', 'audit_log')[0]['before_json'])
    assert [p['id'] for p in before['participants']] == [old]


@pytest.mark.parametrize('kwargs, after', [
    ({'slots': [{'dow': 3, 'time': '12:00'}]}, {'slots': [{'dow': 3, 'time': '12:00'}]}),
    ({'dows': [3], 'time': '12:00'}, {'dows': [3], 'time': '12:00'}),
])
def test_reschedule_regenerates_future_like_legacy(tmp_path, kwargs, after):
    with EdusonDB(tmp_path / 'e.db') as db:
        _group_with_plan(db, held=1, scheduled=2)
        db.reschedule_group(1, from_date='2026-07-10', teacher_id=7, **kwargs)
    assert _sessions(tmp_path / 'e.db') == [
        ('2026-07-06', '15:00', 'held', 'regular', 1),
        ('2026-07-15', '12:00', 'scheduled', 'regular', 2),   # среды с from_date
        ('2026-07-22', '12:00', 'scheduled', 'regular', 3),
    ]
    (group,) = _read(tmp_path / 'e.db', 'student_groups')
    assert (group['teacher_id'], group['default_dow'], group['default_time']) == (7, 3, '12:00')
    (entry,) = _read(tmp_path / 'e.db', 'audit_log')
    before = json.loads(entry['before_json'])
    assert entry['action'] == 'reschedule' and before['group']['teacher_id'] == 5
    assert before['fromDate'] == '2026-07-10' and len(before['oldSessions']) == 2
    assert json.loads(entry['after_json']) == after
    assert entry['summary'] == 'Изменено расписание ТС1001 (заменено 2 уроков)'


def test_freeze_shifts_future_like_legacy(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        _group_with_plan(db, held=1, scheduled=2)
        db.freeze_group(1, freeze_from='2026-07-10', resume_date='2026-08-01')
    assert _sessions(tmp_path / 'e.db') == [
        ('2026-07-06', '15:00', 'held', 'regular', 1),
        ('2026-08-03', '15:00', 'scheduled', 'regular', 2),
        ('2026-08-10', '15:00', 'scheduled', 'regular', 3),
    ]
    assert {s['teacher_id'] for s in _read(tmp_path / 'e.db', 'sessions') if s['status'] == 'scheduled'} == {5}
    (entry,) = _read(tmp_path / 'e.db', 'audit_log')
    assert entry['action'] == 'freeze'
    assert json.loads(entry['after_json']) == {'freezeFrom': '2026-07-10', 'resumeDate': '2026-08-01'}
    assert entry['summary'] == 'Заморозка ТС1001: 2 уроков с 2026-07-10 → возобновление с 2026-08-01'


def test_revert_update_group_like_legacy(tmp_path):
    """Откат восстанавливает снимок before, помечает исходную запись и пишет 'revert'
    без before/after: новое значение откат в журнал не кладёт."""
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', teacher_id=5)
        upd = db.update_group(1, teacher_id=6)
        db.revert(upd)
    (group,) = _read(tmp_path / 'e.db', 'student_groups')
    first, rev = _read(tmp_path / 'e.db', 'audit_log')
    assert group['teacher_id'] == 5
    assert first['reverted'] == 1
    assert (rev['action'], rev['revert_of'], rev['entity_id']) == ('revert', upd, 1)
    assert rev['before_json'] is None and rev['after_json'] is None
    assert rev['summary'] == 'Откат: Изменена карточка ТС1001'


def test_revert_move_and_delete_session_like_legacy(tmp_path):
    with EdusonDB(tmp_path / 'e.db') as db:
        db.group(1, 'ТС1001', teacher_id=5)
        db.session(100, 1, '2026-07-10', status='scheduled', lesson_no=4)
        db.session(101, 1, '2026-07-17', status='scheduled', lesson_no=5)
        db.revert(db.move_session(100, date='2026-07-12'))
        db.revert(db.delete_session(101))
    s100, s101 = _read(tmp_path / 'e.db', 'sessions')
    assert (s100['date'], s100['type']) == ('2026-07-10', 'regular')
    assert (s101['id'], s101['date'], s101['lesson_no']) == (101, '2026-07-17', 5)  # тот же id
