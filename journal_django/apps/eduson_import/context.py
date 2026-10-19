"""Общее состояние одного прогона импорта Eduson: соответствие id Eduson → id KOTOKOD,
выводы анализа данных и отчёт.

Выводы анализа (analysis.py) считаются ОДИН раз при создании контекста и дальше
берутся только отсюда: шаги групп, уроков и сверка обязаны видеть одно и то же N,
один и тот же набор held с исправленным годом и фиктивных held и одну историю преподавателей. Посчитай их
каждый шаг заново — при правке правила в одном месте шаги разошлись бы молча.

Отчёт и manual_review — без ПДн: только id записей Eduson, коды групп, названия
полей и числа. Имена, телефоны и адреса в них не пишутся — отчёт печатается в
консоль и может уйти в переписку.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING

from apps.lessons.weights import lesson_weight

if TYPE_CHECKING:  # analysis импортирует ImportDataError отсюда — без цикла на рантайме
    from apps.eduson_import.analysis import SheetBackfill, TeacherTimeline
    from apps.eduson_import.sheets import SheetMarks
    from apps.eduson_import.sqlite_source import EdusonSource


# Урок Eduson — 60 минут (legacy services/schedule.js:6 REGULAR_DURATION): длительность
# уроков из таблиц, у которых своей длительности нет. Шаг групп берёт её же для групп.
EDUSON_LESSON_MINUTES = 60


class ImportDataError(Exception):
    """Данные Eduson не ложатся на модель KOTOKOD — импорт останавливается целиком.

    Текст — для человека у консоли: что не так и с какой записью (id Eduson), без ПДн
    и без секретов (хэши паролей в сообщение не попадают)."""


@dataclass
class ImportContext:
    as_of: datetime.date                                   # день импорта: граница фиктивных held
    teacher_timeline: TeacherTimeline
    # sessions.id held с датой > as_of, год которой не исправить (analysis.corrected_year)
    fake_held: frozenset[int] = frozenset()
    # sessions.id → дата с исправленным годом: held с вкладки «Июнь 2027» → урок июня 2026
    year_fixed: dict[int, datetime.date] = field(default_factory=dict)
    # student_groups.id → N; при --sheets-dir — уже за вычетом уроков из таблиц (sheet_backfill)
    pre_system: dict[int, Decimal] = field(default_factory=dict)
    # student_groups.id → дозаполнение из таблиц Eduson (только группы с N > 0); None — без --sheets-dir
    sheet_backfill: dict[int, SheetBackfill] | None = None
    moved_from: dict[int, datetime.date] = field(default_factory=dict)  # sessions.id → дата «до»
    deleted_sessions: dict[int, tuple[int, datetime.date]] = field(default_factory=dict)
    # sessions.id → teacher_id Eduson, выставленный действием замены (не отметка генерации)
    substituted: dict[int, int] = field(default_factory=dict)
    held_at: dict[int, datetime.datetime] = field(default_factory=dict)  # sessions.id → момент «проведён»

    teacher_by_eduson: dict[int, int] = field(default_factory=dict)       # teachers.id → Teacher.id
    account_by_user: dict[int, int] = field(default_factory=dict)         # users.id → Account.id
    direction_by_course: dict[str, int] = field(default_factory=dict)     # 'python' → Direction.id
    # student_groups.id → курс-направление ('python'…). У курса 'groups' — по первой букве
    # кода (шаг people); группы берут направление только отсюда.
    course_by_group: dict[int, str] = field(default_factory=dict)
    group_by_eduson: dict[int, int] = field(default_factory=dict)         # student_groups.id → Group.id
    student_by_participant: dict[int, int] = field(default_factory=dict)  # participants.id → Student.id
    skipped_participants: list[int] = field(default_factory=list)         # пустые строки participants
    lesson_by_session: dict[int, int] = field(default_factory=dict)       # sessions.id → Lesson.id
    planned_by_session: dict[int, int] = field(default_factory=dict)      # sessions.id → PlannedLesson.id
    # Счётчики для сверки (reconcile): что шаги сознательно не перенесли.
    review_participants: int = 0                                          # участников «на ручную проверку» склейки
    feedback_not_moved: list[int] = field(default_factory=list)           # lesson_reports.id, ушедшие на проверку
    renewals_applied: int = 0                                             # статусов Eduson, ставших стадией сделки

    # Почты (norm_email) учёток-admin Eduson, которые становятся superadmin KOTOKOD:
    # в Eduson этой роли нет, а учётками/2FA/зарплатой в KOTOKOD управляет только она.
    # Задаёт команда (--superadmin), применяет шаг people. Сами почты в отчёт не идут.
    superadmin_emails: tuple[str, ...] = ()

    report: list[str] = field(default_factory=list)          # человекочитаемые заметки (без ПДн)
    manual_review: list[str] = field(default_factory=list)   # что человеку проверить руками

    def sheet_lessons(self, group_id: int) -> tuple:
        """(дата, время) уроков из таблиц для группы Eduson; без --sheets-dir — пусто."""
        backfill = (self.sheet_backfill or {}).get(group_id)
        return backfill.lessons if backfill else ()

    def sheet_weight(self, group_id: int) -> Decimal:
        """Вес уроков из таблиц группы: проведённая отметкой сессия — по своей длительности,
        отдельный урок — EDUSON_LESSON_MINUTES."""
        backfill = (self.sheet_backfill or {}).get(group_id)
        if not backfill:
            return Decimal(0)
        return sum((lesson_weight(backfill.fills[day][1] if day in backfill.fills else EDUSON_LESSON_MINUTES)
                    for day, _ in backfill.lessons), Decimal(0))

    def sheet_fills(self) -> dict[int, datetime.time | None]:
        """sessions.id незаполненной сессии, которую проводит отметка таблицы → время из ячейки."""
        out: dict[int, datetime.time | None] = {}
        for backfill in (self.sheet_backfill or {}).values():
            times = dict(backfill.lessons)
            for day, (sid, _) in backfill.fills.items():
                out[sid] = times[day]
        return out

    @classmethod
    def from_source(cls, src: EdusonSource, as_of: datetime.date,
                    sheet_marks: SheetMarks | None = None) -> ImportContext:
        """sheet_marks — отметки таблиц (sheets.load_sheets) или None (без --sheets-dir:
        поведение как до дозаполнения). N сразу уменьшается на число уроков из таблиц —
        шаги групп и уроков и сверка видят одно и то же итоговое N."""
        from apps.eduson_import import analysis

        pre_system = analysis.pre_system_lessons(src, as_of)
        backfill = None
        if sheet_marks is not None:
            backfill = analysis.sheet_backfill(src, sheet_marks, as_of, pre_system)
            for gid, item in backfill.items():
                pre_system[gid] = item.n_after
        return cls(
            as_of=as_of,
            teacher_timeline=analysis.group_teacher_timeline(src),
            fake_held=frozenset(analysis.fake_held_ids(src, as_of)),
            year_fixed=analysis.year_fixed_held(src, as_of),
            pre_system=pre_system,
            sheet_backfill=backfill,
            moved_from=analysis.moved_from_dates(src),
            deleted_sessions=analysis.deleted_session_dates(src),
            substituted=analysis.substituted_teacher_by_session(src),
            held_at=analysis.held_moments(src),
        )
