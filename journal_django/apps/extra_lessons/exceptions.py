"""Доменные исключения раздела extra_lessons (см. apps/lessons/exceptions.py для паттерна)."""
from __future__ import annotations


class MissedLessonNotFound(Exception):
    """missed_lesson_id не ссылается на существующий проведённый урок."""


class DuplicateAssignment(Exception):
    """У студента уже есть активное (не отменённое) назначение за этот же пропуск."""

    def __init__(self, student_names: list[str]) -> None:
        self.student_names = student_names
        names = ', '.join(student_names)
        super().__init__(
            f'Уже есть активный доп.урок за этот пропуск у: {names}.'
        )


class NotTeachersAssignment(Exception):
    """Преподаватель пытается провести/посмотреть чужое назначение."""


class AbsentStudentNotRecordable(Exception):
    """
    Попытка записать доп.урок с present=false («ученик не пришёл»). Запись
    доп.урока означает «занятие проведено, ученик присутствовал» — фиксировать
    неявку через запись нельзя (иначе резолюция закрылась бы как проведённая при
    отсутствии ученика). Неявку на назначенный доп.урок оформляют «Отменой»
    назначения (cancel → пропуск снова pending и требует нового решения).
    """

    def __init__(self) -> None:
        super().__init__(
            'Нельзя записать доп.урок без присутствия ученика. '
            'Если ученик не пришёл — отмените назначенный доп.урок.'
        )


class MembershipHasScheduledMakeups(Exception):
    """
    Попытка снять членство ученика в группе (удаление/перевод/деактивация/
    заморозка/уход), пока по его пропускам в ЭТОЙ группе есть НАЗНАЧЕННЫЕ, но не
    проведённые доп.уроки (status=makeup_scheduled). Их нельзя молча удалить —
    за назначением уже стоит преподаватель и дата; сначала отмените/проведите
    доп.урок в разделе «Доп.уроки», затем снимайте членство. pending-резолюции
    («Ждёт решения») при снятии членства удаляются автоматически и блока не дают.
    """

    def __init__(self, count: int = 0) -> None:
        self.count = count
        super().__init__(
            'Нельзя снять ученика из группы, пока есть назначенные доп.уроки '
            'по его пропускам в этой группе. Сначала отмените или проведите их '
            'в разделе «Доп.уроки».'
        )


class StudentNotInGroup(Exception):
    """Ручной доп.урок сверх курса (kind='extra') назначают ученику, который НЕ
    состоит активным участником указанной группы. Доп.урок двигает прогресс/баланс
    именно членства в группе — вне группы двигать нечего."""

    def __init__(self, student_names: list[str]) -> None:
        self.student_names = student_names
        names = ', '.join(student_names) or 'указанные ученики'
        super().__init__(f'Не состоят в выбранной группе: {names}.')


class GroupNotFound(Exception):
    """group_id ручного доп.урока не ссылается на существующую группу."""


class StudentWasPresent(Exception):
    """Ручной доп.урок назначают ЗА урок, на котором ученик реально ПРИСУТСТВОВАЛ
    (present=true). Доп.урок восполняет пропуск — ставить его на посещённый урок
    нельзя (иначе двойной учёт занятия/денег)."""

    def __init__(self, student_names: list[str]) -> None:
        self.student_names = student_names
        names = ', '.join(student_names) or 'указанные ученики'
        super().__init__(f'Были на этом уроке (доп.урок за него назначить нельзя): {names}.')


class StudentNotAbsent(Exception):
    """
    Ученику назначают доп.урок за пропуск, на котором он НЕ был отмечен
    отсутствующим (present=true или вообще не участник missed_lesson) — доп.урок
    компенсирует только реальное отсутствие, иначе преподавателю платится
    зарплата за компенсацию несуществующего пропуска.
    """

    def __init__(self, student_names: list[str]) -> None:
        self.student_names = student_names
        names = ', '.join(student_names)
        super().__init__(
            f'Эти ученики не были отмечены отсутствующими на пропущенном уроке: {names}.'
        )
