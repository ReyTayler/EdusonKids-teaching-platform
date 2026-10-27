"""Тексты ответов бота. Чистые функции, без сети."""
from __future__ import annotations

import datetime


def _day(iso: str) -> str:
    return datetime.date.fromisoformat(iso).strftime('%d.%m')


def start(full_name: str) -> str:
    return (
        f'Привет, {full_name}!\n\n'
        'Я присылаю напоминания о занятиях: утром — расписание на день, '
        'вечером — что осталось заполнить, и отдельно — когда назначают '
        'доп.урок, перенос или замену.\n\n'
        'Команда /my покажет ближайшие занятия.'
    )


def help_text() -> str:
    return (
        'Доступные команды:\n\n'
        '/my — мои ближайшие занятия\n'
        '/help — эта справка'
    )


def my_events(payload: dict) -> str:
    events = payload.get('events') or []
    if not events:
        return 'На ближайшую неделю занятий нет.'

    lines = []
    for event in events:
        mark = ' (замена)' if event.get('is_substitute') else ''
        number = f" — урок №{event['seq']}" if event.get('seq') is not None else ''
        lines.append(
            f"• {_day(event['date'])}, {event.get('time') or '—'} — "
            f"{event['group']} ({event['direction']}){number}{mark}"
        )
    return 'Ваши ближайшие занятия:\n\n' + '\n'.join(lines)


def not_linked() -> str:
    return (
        'Ваш аккаунт ещё не привязан к преподавателю.\n\n'
        'Обратитесь к администратору — он привяжет вас в журнале, '
        'после этого начнут приходить напоминания.'
    )


def journal_unavailable() -> str:
    return 'Журнал сейчас недоступен, попробуйте через пару минут.'
