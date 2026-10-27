"""Тексты ответов бота."""
from bot.utils import formatters


def test_my_events_formats_list():
    text = formatters.my_events({
        'teacher_name': 'Анна Петрова',
        'events': [
            {'date': '2026-08-05', 'time': '12:00', 'group': 'СИ1027',
             'direction': 'Scratch', 'seq': 1, 'is_substitute': False},
            {'date': '2026-08-06', 'time': '13:00', 'group': 'ПИ1062',
             'direction': 'Python', 'seq': 9, 'is_substitute': True},
        ],
    })
    assert '05.08' in text
    assert 'СИ1027' in text
    assert 'замена' in text


def test_my_events_handles_empty_list():
    text = formatters.my_events({'teacher_name': 'Анна', 'events': []})
    assert 'нет' in text.lower()


def test_not_linked_message_explains_what_to_do():
    text = formatters.not_linked()
    assert 'администратор' in text.lower()
