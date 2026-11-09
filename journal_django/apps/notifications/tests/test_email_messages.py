"""Письма семьям: текст и оформленная версия собираются из одних данных."""
from __future__ import annotations

import datetime

from apps.notifications import email_messages

DAY = datetime.date(2026, 11, 12)
TIME = datetime.time(18, 0)


def test_day_before_html_carries_the_same_facts_as_text():
    subject, text, html = email_messages.parent_day_before(
        day=DAY, time=TIME, direction='Scratch', children=['Маша'])

    assert subject == 'Напоминание: занятие завтра в 18:00'
    assert 'Ученик: Маша.' in text and '<' not in text
    for fact in ('Занятие завтра в 18:00', '12.11', 'Scratch', 'Маша', 'Eduson Kids'):
        assert fact in html


def test_hour_before_html():
    subject, _text, html = email_messages.parent_hour_before(
        time=TIME, direction='Scratch', children=['Маша', 'Миша'])

    assert subject == 'Занятие через час — в 18:00'
    assert 'Ученики' in html and 'Маша, Миша' in html


def test_html_escapes_data_from_the_database():
    """Имя и направление приходят из базы и не должны становиться разметкой письма."""
    _s, text, html = email_messages.parent_day_before(
        day=DAY, time=TIME, direction='<i>Python</i>', children=['<b>Маша</b>'])

    assert '<b>Маша</b>' not in html and '<i>Python</i>' not in html
    assert '&lt;b&gt;Маша&lt;/b&gt;' in html
    assert '<b>Маша</b>' in text


def test_html_without_direction_has_no_empty_row():
    _s, _t, html = email_messages.parent_day_before(
        day=DAY, time=TIME, direction=None, children=['Маша'])

    assert '>Занятие<' not in html
