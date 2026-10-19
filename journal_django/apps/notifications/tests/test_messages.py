"""Тесты форматирования текстов. Без БД: функции чистые."""
from __future__ import annotations

import datetime

from apps.notifications import messages


def test_morning_digest_marks_substitute_and_extra():
    text = messages.morning_digest(
        teacher_name='Анна Петрова',
        day=datetime.date(2026, 8, 3),
        items=[
            {'time': '12:00', 'group': 'СИ1027', 'direction': 'Scratch',
             'seq': 1, 'is_substitute': False, 'is_extra': False},
            {'time': '13:00', 'group': 'ПИ1062', 'direction': 'Python',
             'seq': 9, 'is_substitute': True, 'is_extra': False},
            {'time': '14:30', 'group': 'ПИ1062', 'direction': 'Python',
             'seq': None, 'is_substitute': False, 'is_extra': True},
        ],
    )
    assert 'Доброе утро, Анна Петрова!' in text
    assert 'Ваши уроки на сегодня (03.08):' in text
    assert '• 12:00 — СИ1027 (Scratch) — урок №1' in text
    assert '• 13:00 — ПИ1062 (Python) — урок №9 (замена)' in text
    assert '• 14:30 — ПИ1062 (Python) — доп.урок' in text
    assert 'Хорошего дня! 🚀' in text


def test_fill_digest_always_has_the_required_footer():
    text = messages.fill_digest(items=[
        {'date': datetime.date(2026, 8, 2), 'time': '16:00', 'group': 'ПИ1054',
         'direction': 'Python', 'seq': 11},
    ])
    assert '• 02.08, 16:00 — ПИ1054 (Python) — урок №11' in text
    assert 'Если уроков не было, сообщите менеджеру или администратору.' in text


def test_fill_digest_line_without_direction_does_not_break():
    """У доп.урока направления нет — fill_service отдаёт direction_name=None.

    Раньше строка падала на html.escape(None), и вместе с ней — вся вечерняя
    рассылка по школе: одна отработка без направления гасила дайджест всем.
    """
    text = messages.fill_digest(items=[
        {'date': datetime.date(2026, 8, 6), 'time': '14:00', 'group': 'БГ16',
         'direction': None, 'seq': None},
    ])
    assert '• 06.08, 14:00 — БГ16 — доп.урок' in text
    assert 'None' not in text


def test_morning_digest_line_without_direction_does_not_break():
    text = messages.morning_digest(
        teacher_name='Анна Петрова',
        day=datetime.date(2026, 8, 6),
        items=[
            {'time': '14:00', 'group': 'БГ16', 'direction': None,
             'seq': None, 'is_substitute': False, 'is_extra': True},
        ],
    )
    assert '• 14:00 — БГ16 — доп.урок' in text
    assert 'None' not in text


def test_makeup_assigned_states_who_what_when():
    text = messages.makeup_assigned(
        teacher_name='Анна Петрова',
        group='ПИ1062', direction='Python',
        day=datetime.date(2026, 8, 10), time='14:30',
        student_name='Пётр Иванов', is_beyond_course=False,
    )
    assert 'доп.урок' in text.lower()
    assert '10.08' in text
    assert '14:30' in text
    assert 'ПИ1062' in text
    assert 'Пётр Иванов' in text


def test_lesson_moved_shows_both_dates():
    text = messages.lesson_moved(
        group='СИ1027', direction='Scratch', seq=4,
        from_day=datetime.date(2026, 8, 5), to_day=datetime.date(2026, 8, 12),
        time='12:00',
    )
    assert '05.08' in text
    assert '12.08' in text
    assert 'СИ1027' in text


def test_lesson_cancelled_reports_course_extension():
    """Отмена не выбрасывает урок — он уезжает в конец курса.

    Без этой строки преподаватель видит только половину последствия: занятие
    отменили, а то, что у группы появилось новое занятие в конце, — нет.
    """
    text = messages.lesson_cancelled(
        group='СИ1027', direction='Scratch', seq=3,
        day=datetime.date(2026, 7, 21), time='12:00',
        course_end=datetime.date(2026, 8, 4),
    )
    assert 'Курс продлён до 04.08' in text


def test_lesson_cancelled_without_course_end_says_nothing_extra():
    text = messages.lesson_cancelled(
        group='СИ1027', direction='Scratch', seq=3,
        day=datetime.date(2026, 7, 21), time='12:00',
    )
    assert 'Курс продлён' not in text


def test_mention_prefers_username():
    assert messages.mention(name='Анна Петрова', username='anna', chat_id=555) == '@anna'


def test_mention_falls_back_to_inline_link_without_username():
    """Ник в Telegram необязателен, а пингануть человека всё равно нужно."""
    text = messages.mention(name='Анна Петрова', username=None, chat_id=555)
    assert text == '<a href="tg://user?id=555">Анна Петрова</a>'


def test_mention_without_binding_is_plain_name():
    text = messages.mention(name='Анна Петрова', username=None, chat_id=None)
    assert text == 'Анна Петрова'


def test_mention_escapes_name():
    """Имя с амперсандом иначе сломает разбор HTML и сообщение не доставится."""
    text = messages.mention(name='Иванов & Ко', username=None, chat_id=1)
    assert '&amp;' in text
