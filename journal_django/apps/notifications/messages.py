"""
Тексты сообщений. Чистые функции: данные на входе, готовая строка на выходе.

Формат — HTML (parse_mode=HTML в Telegram API), поэтому пользовательские данные
экранируются: имя группы или ученика с символом «&» иначе сломает разбор
сообщения на стороне Telegram и оно не доставится.

Номер урока — ПОРЯДКОВЫЙ (PlannedLesson.seq). На 45-минутных курсах в БД лежит
lesson_number = seq × 0.5 — это внутренний вес для расчёта денег и прогресса,
преподавателю он не показывается.
"""
from __future__ import annotations

import datetime
from html import escape


def _d(day: datetime.date) -> str:
    """Дата в человеческом виде: 03.08."""
    return day.strftime('%d.%m')


def _place(item: dict) -> str:
    """
    «Группа (направление)» — с направлением, только если оно известно.

    У доп.урока направления нет: fill_service отдаёт direction_name=None (так же
    рисует прочерк и вкладка «Заполнить»). Подставлять None в escape() нельзя —
    падало всё формирование дайджеста, а значит рассылка по ВСЕЙ школе
    (прод, 06.08.2026: одна незаполненная отработка — и вечернего письма не
    получил никто).
    """
    group = escape(item['group'] or '—')
    direction = item.get('direction')
    return f'{group} ({escape(direction)})' if direction else group


def _lesson_line(item: dict) -> str:
    """Одна строка утреннего списка занятий."""
    head = f"• {item['time']} — {_place(item)}"
    if item.get('is_extra'):
        return f'{head} — доп.урок'
    line = f"{head} — урок №{item['seq']}"
    if item.get('is_substitute'):
        line += ' (замена)'
    return line


def _fill_line(item: dict) -> str:
    """Одна строка вечернего списка незаполненных отчётов."""
    head = f"• {_d(item['date'])}, {item['time']} — {_place(item)}"
    if item.get('seq') is None:
        return f'{head} — доп.урок'
    return f"{head} — урок №{item['seq']}"


def mention(*, name: str, username: str | None, chat_id: int | None) -> str:
    """
    Упоминание преподавателя для общего чата — чтобы человек получил уведомление,
    а не пролистал сообщение про себя в общем потоке.

    По @нику — если он есть. Ник в Telegram необязателен и может меняться,
    поэтому запасной вариант — инлайн-упоминание по числовому id: оно тоже
    пингует и не зависит от ника. Если привязки нет вовсе, остаётся простое имя
    без пинга: сообщение всё равно должно быть понятным.
    """
    if username:
        return f'@{escape(username)}'
    if chat_id:
        return f'<a href="tg://user?id={chat_id}">{escape(name)}</a>'
    return escape(name)


def morning_digest(*, teacher_name: str, day: datetime.date, items: list[dict]) -> str:
    """Утренний список занятий. Вызывается только когда items непуст."""
    lines = '\n'.join(_lesson_line(i) for i in items)
    return (
        f'Доброе утро, {escape(teacher_name)}!\n\n'
        f'Ваши уроки на сегодня ({_d(day)}):\n\n'
        f'{lines}\n\n'
        'Хорошего дня! 🚀'
    )


def fill_digest(*, items: list[dict]) -> str:
    """Вечерний список незаполненных отчётов. Последняя строка обязательна."""
    lines = '\n'.join(_fill_line(i) for i in items)
    return (
        'Не заполнены отчёты:\n\n'
        f'{lines}\n\n'
        'Если уроков не было, сообщите менеджеру или администратору.'
    )


def makeup_assigned(*, teacher_name: str, group: str, direction: str,
                     day: datetime.date, time: str, student_name: str,
                     is_beyond_course: bool) -> str:
    what = 'Доп.урок сверх курса' if is_beyond_course else 'Доп.урок (отработка)'
    return (
        f'{what} назначен.\n\n'
        f'{escape(group)} ({escape(direction)})\n'
        f'{_d(day)} в {time}\n'
        f'Ученик: {escape(student_name)}\n'
        f'Преподаватель: {escape(teacher_name)}'
    )


def makeup_changed(*, teacher_name: str, group: str, direction: str,
                    day: datetime.date, time: str, student_name: str) -> str:
    return (
        'Доп.урок изменён.\n\n'
        f'{escape(group)} ({escape(direction)})\n'
        f'Новое время: {_d(day)} в {time}\n'
        f'Ученик: {escape(student_name)}\n'
        f'Преподаватель: {escape(teacher_name)}'
    )


def makeup_cancelled(*, teacher_name: str, group: str, direction: str,
                      day: datetime.date, time: str, student_name: str) -> str:
    return (
        'Доп.урок отменён.\n\n'
        f'{escape(group)} ({escape(direction)})\n'
        f'Было: {_d(day)} в {time}\n'
        f'Ученик: {escape(student_name)}\n'
        f'Преподаватель: {escape(teacher_name)}'
    )


def lesson_moved(*, group: str, direction: str, seq: int | None,
                  from_day: datetime.date, to_day: datetime.date, time: str) -> str:
    number = f' — урок №{seq}' if seq is not None else ''
    return (
        'Занятие перенесено.\n\n'
        f'{escape(group)} ({escape(direction)}){number}\n'
        f'Было: {_d(from_day)}\n'
        f'Стало: {_d(to_day)} в {time}'
    )


def lesson_cancelled(*, group: str, direction: str, seq: int | None,
                      day: datetime.date, time: str,
                      course_end: datetime.date | None = None) -> str:
    """
    Отмена занятия.

    course_end — новая дата последнего занятия курса. Отмена не выбрасывает урок,
    а переносит его в конец: у группы появляется новое занятие, и без этой строки
    преподаватель видит только половину последствия.
    """
    number = f' — урок №{seq}' if seq is not None else ''
    tail = f'\nКурс продлён до {_d(course_end)}' if course_end else ''
    return (
        'Занятие отменено.\n\n'
        f'{escape(group)} ({escape(direction)}){number}\n'
        f'Было: {_d(day)} в {time}'
        f'{tail}'
    )


def substitute_assigned(*, group: str, direction: str, day: datetime.date,
                         time: str, substitute_name: str, for_substitute: bool) -> str:
    if for_substitute:
        head = 'Вас назначили заменой.'
    else:
        head = f'На ваше занятие назначена замена: {escape(substitute_name)}.'
    return (
        f'{head}\n\n'
        f'{escape(group)} ({escape(direction)})\n'
        f'{_d(day)} в {time}'
    )


def substitute_removed(*, group: str, direction: str, day: datetime.date,
                        time: str, for_substitute: bool) -> str:
    head = ('Замена снята — занятие ведёт основной преподаватель.' if for_substitute
            else 'Замена снята — занятие снова за вами.')
    return (
        f'{head}\n\n'
        f'{escape(group)} ({escape(direction)})\n'
        f'{_d(day)} в {time}'
    )
