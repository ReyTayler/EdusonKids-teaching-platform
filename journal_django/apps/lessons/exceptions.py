"""
Доменные исключения раздела lessons.

Не зависят от DRF/HTTP — бросаются в repository/services, маппятся в HTTP-ответ
во view (см. apps/groups/exceptions.py::ImmutableGroupFormat для того же паттерна).
"""
from __future__ import annotations


class UnpaidAttendanceBlocked(Exception):
    """
    Попытка отметить присутствие ученику, у которого не осталось оплаченных
    уроков (remaining <= 0, apps.finances.repository.balances_for_students).

    Действует одинаково для teacher SPA (submitLesson) и admin SPA (создание
    урока, переключение ячейки посещаемости) — единая проверка в
    apps.lessons.repository.assert_students_paid.
    """

    def __init__(self, blocked_names: list[str]) -> None:
        self.blocked_names = blocked_names
        names = ', '.join(blocked_names)
        super().__init__(
            f'У учеников без оплаченных уроков нельзя отметить посещение: {names}.'
        )


class SystemLessonProtected(Exception):
    """
    Попытка изменить/удалить системный урок (lesson_type='extra'/'burned') через
    общий CRUD /api/admin/lessons. Такие уроки — факты доп.урока/сгорания,
    ими владеет раздел «Доп.уроки» (apps.extra_lessons); менять/удалять их можно
    только откатом оттуда, не общим списком уроков.
    """

    def __init__(self, lesson_type: str) -> None:
        self.lesson_type = lesson_type
        label = 'сгоревший урок' if lesson_type == 'burned' else 'доп.урок'
        super().__init__(
            f'Это системный {label} — изменить или удалить его можно только '
            f'через раздел «Доп.уроки», не через общий список уроков.'
        )


class AttendanceCompensatedElsewhere(Exception):
    """
    Попытка вручную отметить ученика присутствовавшим на ИСХОДНОМ пропущенном
    уроке, чей пропуск уже компенсирован доп.уроком (makeup_done) или сожжён
    (burned). Потребление уже списано отдельным фактом (Lesson extra/burned
    present=true); флип исходной ячейки в present=true списал бы урок ВТОРОЙ раз.
    Править такой пропуск можно только откатом факта в разделе «Доп.уроки».
    """

    def __init__(self) -> None:
        super().__init__(
            'Пропуск этого ученика уже компенсирован доп.уроком или сожжён — '
            'отметить его присутствовавшим здесь нельзя (иначе урок спишется дважды). '
            'Откатите факт в разделе «Доп.уроки».'
        )


class LessonHasMakeupResolutions(Exception):
    """
    Попытка удалить ОБЫЧНЫЙ урок, по пропускам которого уже проведён доп.урок
    (apps.extra_lessons.AbsenceResolution.status='makeup_done'). FK
    missed_lesson = ON DELETE CASCADE снёс бы резолюцию вместе с уроком,
    осиротив факт доп.урока/сгорания (Lesson lesson_type='extra'/'burned') + его
    Payroll. Сначала нужно откатить доп.урок/сгорание из раздела «Доп.уроки»
    (delete_fact), затем удалять исходный урок.
    """

    def __init__(self) -> None:
        super().__init__(
            'По пропускам этого урока проведены доп.уроки — сначала откатите их '
            'в разделе «Доп.уроки», затем удаляйте урок.'
        )


class LessonAlreadyRecorded(Exception):
    """
    Попытка записать урок на позицию курса (planned_lessons), за которой уже
    закреплён факт. Так выглядит повторная отправка одного и того же занятия:
    учитель не увидел подтверждения (потерянный ответ, таймаут gunicorn) и нажал
    «Сохранить» ещё раз — сервер обязан отказать, а не создать второй урок.

    Раньше это не ловилось: lesson_number выводился из max(lessons_done)+step,
    то есть из состояния, которое сама запись инкрементирует, поэтому каждый
    повтор получал НОВЫЙ номер и проходил мимо lessons_natural_key. Теперь номер
    берётся из позиции плана, а позиция захватывается под select_for_update —
    второй захват невозможен (см. apps.lessons.services.record_lesson).

    Сообщение называет номер и дату уже записанного урока: без idempotency-ключа
    сервер не может отличить повтор от осмысленной второй попытки, поэтому говорит
    как есть, а не выдаёт чужой результат за успех.
    """

    def __init__(self, lesson_number, lesson_date) -> None:
        self.lesson_number = lesson_number
        self.lesson_date = lesson_date
        num = int(lesson_number) if lesson_number == int(lesson_number) else lesson_number
        super().__init__(
            f'Урок №{num} за {lesson_date} уже записан. Если нужно изменить '
            f'посещаемость — откройте урок в истории «Мои уроки».'
        )


class AttendanceLockedByTransfer(Exception):
    """
    Попытка отметить посещаемость (present ИЛИ absent) ученику, переведённому в
    эту группу, на уроке с lesson_number <= уже отработанного им в старой группе
    (apps.memberships.repository.locked_through). Такой урок ученик фактически
    уже прошёл в предыдущей группе — отмечать его здесь нельзя (двойной учёт
    посещаемости/баланса/зарплаты). Действует, пока группа не догонит его прогресс.
    """

    def __init__(self, unlocks_at) -> None:
        self.unlocks_at = unlocks_at
        super().__init__(
            f'Этот ученик переведён и уже отработал этот урок в другой группе — '
            f'отметить его здесь нельзя. Включится с урока №{unlocks_at}.'
        )


class CoursePositionVanished(Exception):
    """
    Позиция курса, названная клиентом, исчезла между предварительной проверкой
    и захватом под блокировкой: занятие отменили, план пересобрали или изменили
    его длину.

    Продолжать запись нельзя: без позиции ядро уходит на расчёт номера из
    прогресса учеников — путь, на котором повторная отправка создаёт дубль
    (инцидент ПГ215). Клиенту говорим обновить календарь.
    """

    def __init__(self) -> None:
        super().__init__(
            'Это занятие изменилось, пока вы заполняли форму — обновите '
            'страницу и отметьте его заново.'
        )


class AttendanceHasScheduledMakeup(Exception):
    """
    Попытка задним числом отметить ученика присутствовавшим на уроке, за пропуск
    которого уже НАЗНАЧЕН доп.урок (makeup_scheduled). Пропуска больше нет, значит
    назначение беспредметно, но за ним стоят преподаватель и время — снимать его
    молча нельзя (тот же принцип, что MembershipHasScheduledMakeups). Сначала
    отменить назначение в «Доп.уроках», затем исправлять отметку.
    """

    def __init__(self) -> None:
        super().__init__(
            'За этот пропуск уже назначен доп.урок. Сначала отмените его в разделе '
            '«Доп.уроки», затем отмечайте ученика присутствовавшим.'
        )
