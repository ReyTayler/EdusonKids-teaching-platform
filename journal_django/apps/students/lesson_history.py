"""
Строки вкладки «Уроки» в карточке ученика (спека 2026-09-14).

Строка = запись посещаемости, где ученик реально был (present=true). Это ровно
четыре типа: обычный, бесплатный, доп.урок, сгоревший. Пропуски (present=false —
и ждущие решения, и неоплачиваемые) во вкладку не идут: их место в очереди
резолюций и во вкладке «Обучение».

Деньги строки — точный FIFO по уроку
(apps/finances/repository.py::student_lesson_recognition).
"""
from __future__ import annotations

from decimal import Decimal

from django.db.models import QuerySet

from apps.finances.repository import student_lesson_recognition
from apps.lessons.models import LessonAttendance

_ZERO = Decimal('0.00')

# Типы строки, выводимые из lesson_type + is_free. НЕ путать с сырым
# lessons.lesson_type: 'substitution'/'reschedule' — тоже обычные занятия, а
# «бесплатный» и вовсе не тип урока, а исход посещаемости.
# Литералы 'burned'/'extra' обязаны совпадать с SYSTEM_LESSON_TYPES из
# apps/lessons/models.py — там исходный набор системных типов урока.
# Префикс ROW_ намеренный: в apps/lessons/attendance_report.py уже есть
# KIND_BURNED из другой таксономии (категории ячеек Excel).
# Разрешённые ключи сортировки: ключ запроса → поле ORM. Белый список, а не
# сырой sort_by в order_by: иначе клиент подставит в сортировку любое поле
# (в т.ч. чужой таблицы) — то же правило, что у списка учеников.
ORDERING_FIELDS = {
    'submitted_at': 'lesson__submitted_at',
    'lesson_date': 'lesson__lesson_date',
}
DEFAULT_SORT_BY = 'submitted_at'
DEFAULT_SORT_DIR = 'desc'

ROW_KIND_BURNED = 'burned'
ROW_KIND_EXTRA = 'extra'
ROW_KIND_FREE = 'free'
ROW_KIND_REGULAR = 'regular'


def lesson_rows_queryset(
    student_id: int,
    sort_by: str = DEFAULT_SORT_BY,
    sort_dir: str = DEFAULT_SORT_DIR,
    date_from: str | None = None,
    date_to: str | None = None,
) -> QuerySet[LessonAttendance]:
    """
    Записи вкладки в заданном порядке. По умолчанию — убывание даты СОХРАНЕНИЯ
    урока (submitted_at): даты занятия и сохранения расходятся, урок могут
    заполнить спустя дни, и свежезаполненное должно быть сверху.

    Сортировать можно по обеим датам (ORDERING_FIELDS) в обе стороны. Значения
    вне белого списка сюда не доходят — их отбивает вьюха (400), а здесь стоит
    второй рубеж: неизвестное значение откатывается на умолчание, а не уезжает
    в order_by сырой строкой.

    date_from/date_to — необязательный период по дате ЗАНЯТИЯ, границы
    включительно. Нужен модалке дашборда «Посещения учеников»; вкладка карточки
    период не передаёт и показывает всю историю.

    К ключу сортировки ВСЕГДА добавляется lesson_id — иначе записи с одинаковой
    датой могут менять порядок между запросами, и страница 2 повторит строку со
    страницы 1. Направление тай-брейка совпадает с основным.

    select_related на группу, направление и преподавателя: строка показывает их
    все, без него был бы N+1 на каждую строку страницы.
    """
    field = ORDERING_FIELDS.get(sort_by, ORDERING_FIELDS[DEFAULT_SORT_BY])
    prefix = '' if sort_dir == 'asc' else '-'
    qs = LessonAttendance.objects.filter(student_id=student_id, present=True)
    if date_from:
        qs = qs.filter(lesson__lesson_date__gte=date_from)
    if date_to:
        qs = qs.filter(lesson__lesson_date__lte=date_to)
    return (
        qs
        .select_related('lesson', 'lesson__group', 'lesson__group__direction',
                        'lesson__teacher')
        .order_by(f'{prefix}{field}', f'{prefix}lesson_id')
    )


def row_kind(attendance: LessonAttendance) -> str:
    """
    Выводимый тип строки.

    lesson_type проверяется РАНЬШЕ is_free: сгорание и доп.урок бесплатными не
    бывают, и флаг is_free на них ничего не значит.
    """
    lesson_type = attendance.lesson.lesson_type
    if lesson_type == 'burned':
        return ROW_KIND_BURNED
    if lesson_type == 'extra':
        return ROW_KIND_EXTRA
    if attendance.is_free:
        return ROW_KIND_FREE
    return ROW_KIND_REGULAR


def serialize_rows(attendance_rows, student_id: int) -> list[dict]:
    """
    Строки вкладки для отданной страницы посещаемости.

    Карта денег строится по ВСЕЙ истории ученика (иначе неизвестно, какие
    абонементы к моменту урока погашены), а склеивается только со строками
    страницы. У ученика это десятки записей — расчёт дешёвый.

    Оба аргумента обязаны относиться к ОДНОМУ ученику: строки дают состав
    страницы, а student_id — чьи деньги к ним приклеить. Перепутать их на
    вызывающей стороне легко, а наружу тогда уйдут чужие суммы без единой
    ошибки — поэтому сверяем явно (student_id у записей уже выбран, лишних
    запросов нет).

    recognized_amount отдаётся строкой: деньги через float терять нельзя, а
    фронтовый fmtRub принимает и строку.
    """
    attendance_rows = list(attendance_rows)
    for att in attendance_rows:
        if att.student_id != student_id:
            raise ValueError(
                f'serialize_rows: запись посещаемости урока {att.lesson_id} '
                f'принадлежит ученику {att.student_id}, а деньги считаются '
                f'для ученика {student_id}')
    # Пустая страница: без строк склеивать нечего, а полный FIFO по истории
    # ученика — это 3 запроса и расчёт ради пустого списка.
    if not attendance_rows:
        return []

    recognition = student_lesson_recognition(student_id)
    rows: list[dict] = []
    for att in attendance_rows:
        lesson = att.lesson
        group = lesson.group
        direction = group.direction
        money = recognition.get(att.lesson_id, {'recognized': _ZERO, 'is_debt': False})
        rows.append({
            'lesson_id': att.lesson_id,
            # Номер урока в плане курса — numeric(5,1), отдаём строкой без
            # потерь масштаба («12.0»); человеческий вид делает фронт (fmtLessons).
            # У доп.урока и сгорания номер унаследован от пропущенного занятия.
            'lesson_number': str(lesson.lesson_number),
            'lesson_date': lesson.lesson_date.isoformat(),
            # Время сознательно в UTC, как и везде в API (DateTimeField в
            # apps/lessons/serializers.py). В МСК его переводит фронт —
            # fmtDateTime с timeZone: 'Europe/Moscow'. Чинить тут нечего.
            'submitted_at': lesson.submitted_at.isoformat(),
            'kind': row_kind(att),
            'duration_minutes': lesson.lesson_duration_minutes,
            'group_id': group.id,
            'group_name': group.name,
            'teacher_id': lesson.teacher_id,
            'teacher_name': lesson.teacher.name,
            'direction_id': direction.id,
            'direction_name': direction.name,
            'recognized_amount': str(money['recognized']),
            'is_debt': money['is_debt'],
        })
    return rows
