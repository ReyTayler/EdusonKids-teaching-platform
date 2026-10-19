"""Сверка импорта Eduson: «было в SQLite → стало в PostgreSQL» (спека §5.4, вместо §5.3).

reconcile(src, ctx) → (lines, errors, warnings):
  lines    — таблица для печати (сущность, Eduson, KOTOKOD, пояснение);
  errors   — расхождения без объяснимой причины: команда откатывает импорт;
  warnings — ожидаемые особенности данных, печатаются справочно.

ЧТО СЧИТАЕТСЯ ОШИБКОЙ. Ожидание каждой строки таблицы выведено из правил шагов
(held с ошибочным годом — уроками, фиктивные held не переносятся, администраторы без ссылок — только учётка,
склейка учеников, отзывы «на проверку»), поэтому любое другое расхождение —
дефект импорта. По каждой группе «проведено» = N + Σ вес проведённых held (настоящих и с
исправленным годом — всех, кроме фиктивных) + вес уроков из таблиц Eduson (--sheets-dir;
N тогда уже за вычетом их числа)
(apps.lessons.weights.lesson_weight) обязано совпасть и с
GroupMembership.lessons_done каждого ученика, и с Group.lesson_number_offset +
Σ вес Lesson группы — на этих двух числах KOTOKOD строит нумерацию следующих
уроков и прогресс курса. Расхождение с «проведено» самого Eduson (lessons_done +
held) — справочно: Eduson считал двойным счётом, фиктивные held и отработку целым
уроком (спека §5.4).

ЗДОРОВЬЕ ПЛАНА — штатная apps.scheduling.health.check_all (все семь проверок) по
всем группам, включая архивные (include_inactive=True; интерфейс смотрит только
живые). Сверх курса, две проведённые в один день и совпадение времени позиций —
свойства данных Eduson, предупреждения (WARNING_CHECKS). Всё остальное (факт без
позиции, расхождение номера/даты, «проведено» в будущем) импорт создать не может —
ошибки.

ПДн. Только коды групп, ключи проверок и числа: вывод уходит в консоль и в
переписку.

ПРОИЗВОДИТЕЛЬНОСТЬ. Всё, что нужно из PostgreSQL, читается агрегатами одним
запросом на таблицу (несколько тысяч строк), циклы по группам — в памяти.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from django.db.models import Count

from apps.accounts.models import Account
from apps.eduson_import.context import ImportContext
from apps.eduson_import.sqlite_source import EdusonSource
from apps.feedback.models import LessonFeedback
from apps.groups.models import Group
from apps.eduson_import.steps.lessons import SUBMITTED_BY_SHEETS, SUBMITTED_BY_YEAR_FIXED
from apps.lessons.models import Lesson, LessonAttendance
from apps.lessons.weights import lesson_weight
from apps.memberships.models import GroupMembership
from apps.renewals.models import RenewalDeal
from apps.scheduling import health
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import PENDING
from apps.students.models import Student
from apps.teachers.models import Teacher

# Свойства данных Eduson — предупреждения. Любая другая проверка (в т.ч. новая,
# которой здесь ещё нет) — ошибка: молча пропустить поломку плана хуже, чем откатить импорт.
WARNING_CHECKS = frozenset({'beyond_course', 'duplicate_dates', 'collision'})
_LABEL_WIDTH = 28


def _num(value: Decimal | int) -> str:
    """12.50 → '12.5', 10 → '10' (без экспоненты у normalize())."""
    return f'{Decimal(value).normalize():f}'


def reconcile(src: EdusonSource, ctx: ImportContext) -> tuple[list[str], list[str], list[str]]:
    lines: list[str] = [f'{"":<{_LABEL_WIDTH}}{"Eduson":>8}{"KOTOKOD":>9}']
    errors: list[str] = []
    warnings: list[str] = []

    def row(label: str, eduson: int, kotokod: int, expected: int | None = None, note: str = '') -> None:
        lines.append(f'{label:<{_LABEL_WIDTH}}{eduson:>8}{kotokod:>9}' + (f'   {note}' if note else ''))
        if expected is not None and kotokod != expected:
            errors.append(f'{label}: ожидалось {expected}, в KOTOKOD {kotokod}')

    sessions = src.rows('sessions')
    held_all = [s for s in sessions if s['status'] == 'held']
    real_held = [s for s in held_all if s['id'] not in ctx.fake_held]
    fake = len(held_all) - len(real_held)
    participants = src.count('participants')
    students = len(set(ctx.student_by_participant.values()))
    merged = len(ctx.student_by_participant) - students
    submitted = sum(1 for r in src.rows('lesson_reports') if r['status'] == 'submitted')
    renewals = src.count('renewals')

    row('группы', src.count('student_groups'), Group.objects.count(), src.count('student_groups'))
    row('учётки', src.count('users'), Account.objects.count(), src.count('users'))
    superadmins = Account.objects.filter(role=Account.Role.SUPERADMIN, is_active=True).count()
    row('суперадмины', len(ctx.superadmin_emails), superadmins, len(ctx.superadmin_emails),
        'в Eduson роли нет: колонка Eduson — сколько назначено --superadmin')
    if not superadmins:
        warnings.append('нет суперадмина — управлять учётками будет некому (учётки, 2FA, зарплата, '
                        'Синхро доступны только superadmin); укажите --superadmin EMAIL')
    row('преподаватели', src.count('teachers'), Teacher.objects.count(), len(set(ctx.teacher_by_eduson.values())),
        'администраторы без групп, уроков и отзывов — только учётка')
    row('ученики', participants, Student.objects.count(), students,
        f'склеено записей {merged}, пропущено пустых {len(ctx.skipped_participants)}, '
        f'на проверку {ctx.review_participants}')
    backfill = ctx.sheet_backfill or {}
    sheet_lessons = sum(len(b.lessons) for b in backfill.values())
    # Отметка в день незаполненной сессии проводит её — новой позиции не даёт.
    sheet_positions = sheet_lessons - sum(len(b.fills) for b in backfill.values())
    held_note = (f'held = уроки; фиктивных held (год не исправить) {fake} — в «пройдено до учёта»' if fake
                 else 'held = уроки')
    row('проведённые уроки', len(held_all), Lesson.objects.count(), len(real_held) + sheet_lessons,
        held_note + (f'; + {sheet_lessons} из таблиц' if sheet_lessons else ''))
    row('из них год исправлен', len(ctx.year_fixed),
        Lesson.objects.filter(submitted_by_token=SUBMITTED_BY_YEAR_FIXED).count(), len(ctx.year_fixed),
        'таблицы Eduson, вкладка «Июнь 2027» → уроки 2026 года')
    if ctx.sheet_backfill is not None:
        more = sum(1 for b in backfill.values() if b.candidates > b.n_before)
        less = sum(1 for b in backfill.values() if b.candidates < b.n_before)
        groups_with = sum(1 for b in backfill.values() if b.lessons)
        # Колонка Eduson — 0: в базе старой системы этих уроков нет.
        row('из таблиц (до запуска)', 0, Lesson.objects.filter(submitted_by_token=SUBMITTED_BY_SHEETS).count(),
            sheet_lessons, f'групп {groups_with} из {len(backfill)} с N > 0; отметок больше N — {more}, '
                           f'меньше N — {less}')
    row('позиции плана', len(sessions), PlannedLesson.objects.count(), len(sessions) - fake + sheet_positions,
        ('сессии без фиктивных held' if fake else 'все сессии')
        + (f' + {sheet_positions} из таблиц' if sheet_positions else ''))
    row('отзывы (submitted)', submitted, LessonFeedback.objects.count(), submitted - len(ctx.feedback_not_moved),
        f'на проверку {len(ctx.feedback_not_moved)}')
    row('продления (группы)', renewals, ctx.renewals_applied, None,
        f'статус Eduson встал всем ученикам группы; сделок после пересборки {RenewalDeal.objects.count()}')

    _check_groups(src, ctx, real_held, sessions, row, errors, warnings)
    _check_health(warnings, errors)
    _check_overdue(ctx, warnings)
    return lines, errors, warnings


def _check_groups(src, ctx, real_held, sessions, row, errors, warnings) -> None:
    """«Проведено», уроки, позиции и посещаемость по каждой группе."""
    held_weight: dict[int, Decimal] = defaultdict(Decimal)
    held_count: Counter[int] = Counter()
    held_eduson: Counter[int] = Counter()
    for s in real_held:
        held_weight[s['group_id']] += lesson_weight(s['duration_min'])
        held_count[s['group_id']] += 1
    for s in sessions:
        if s['status'] == 'held':
            held_eduson[s['group_id']] += 1
    positions_expected = Counter(s['group_id'] for s in sessions if s['id'] not in ctx.fake_held)

    lesson_weight_by: dict[int, Decimal] = defaultdict(Decimal)
    lesson_count: Counter[int] = Counter()
    for gid, minutes in Lesson.objects.values_list('group_id', 'lesson_duration_minutes'):
        lesson_weight_by[gid] += lesson_weight(minutes)
        lesson_count[gid] += 1
    positions = dict(PlannedLesson.objects.values('group_id').annotate(n=Count('id')).values_list('group_id', 'n'))
    offsets = dict(Group.objects.values_list('id', 'lesson_number_offset'))
    members: dict[int, list[Decimal]] = defaultdict(list)
    for gid, done in GroupMembership.objects.values_list('group_id', 'lessons_done'):
        members[gid].append(Decimal(done))

    attendance_expected = sum(lesson_count[gid] * len(members[gid]) for gid in lesson_count)
    row('посещения (был)', attendance_expected, LessonAttendance.objects.filter(present=True).count(),
        attendance_expected, 'в Eduson посещаемости нет: урок × ученики группы')

    differ_eduson = 0
    for g in src.rows('student_groups'):
        eid, code = g['id'], str(g['code'] or '').strip()
        gid = ctx.group_by_eduson[eid]
        n = ctx.pre_system.get(eid, Decimal(0))
        sheet_count = len(ctx.sheet_lessons(eid))
        sheet_fills = len(ctx.sheet_backfill[eid].fills) if ctx.sheet_backfill and eid in ctx.sheet_backfill else 0
        done = n + held_weight[eid] + ctx.sheet_weight(eid)
        offset = Decimal(offsets[gid] or 0)
        if offset != n:
            errors.append(f'группа {code}: пройдено до учёта {_num(offset)}, ожидалось {_num(n)}')
        if offset + lesson_weight_by[gid] != done:
            errors.append(f'группа {code}: проведено по урокам KOTOKOD {_num(offset + lesson_weight_by[gid])}, '
                          f'ожидалось {_num(done)}')
        wrong = [d for d in members[gid] if d != done]
        if wrong:
            errors.append(f'группа {code}: у {len(wrong)} учени(ков) пройдено {_num(wrong[0])}, '
                          f'ожидалось {_num(done)}')
        if lesson_count[gid] != held_count[eid] + sheet_count:
            errors.append(f'группа {code}: уроков {lesson_count[gid]}, проведённых held {held_count[eid]}'
                          + (f' + из таблиц {sheet_count}' if sheet_count else ''))
        if positions.get(gid, 0) != positions_expected[eid] + sheet_count - sheet_fills:
            errors.append(f'группа {code}: позиций плана {positions.get(gid, 0)}, '
                          f'ожидалось {positions_expected[eid] + sheet_count - sheet_fills}')
        eduson_done = Decimal(g['lessons_done'] or 0) + held_eduson[eid]
        if eduson_done != done:
            differ_eduson += 1
            warnings.append(f'группа {code}: проведено в KOTOKOD {_num(done)}, в Eduson {_num(eduson_done)} '
                            '(справочно)')
    if differ_eduson:
        warnings.append(f'«проведено» расходится с Eduson в {differ_eduson} группах — ожидаемо: Eduson считал '
                        'lessons_done двойным счётом и отработку 45 мин целым уроком (спека §5.4)')


def _check_health(warnings: list[str], errors: list[str]) -> None:
    # Все группы, не только активные: импорт пишет и архивные, а поломка плана в
    # архиве так же откатывает импорт.
    result = health.check_all(include_inactive=True)
    totals: dict[str, int] = Counter()
    names: dict[str, list[str]] = defaultdict(list)
    for g in result['groups']:
        for key, n in g['counts'].items():
            if key in WARNING_CHECKS:
                totals[key] += n
                names[key].append(g['name'])
            else:
                errors.append(f'план группы {g["name"]}: {key} — {n}')
    for key in sorted(totals):
        warnings.append(f'здоровье плана {key}: {totals[key]} в {len(names[key])} группах '
                        f'({", ".join(sorted(names[key]))}) — ожидаемая особенность данных Eduson')
    warnings.append(f'здоровье плана: проверено групп {result["checked"]} (включая архивные)')


def _check_overdue(ctx: ImportContext, warnings: list[str]) -> None:
    overdue = list(PlannedLesson.objects
                   .filter(status=PENDING, seq__isnull=False, scheduled_date__lt=ctx.as_of)
                   .values_list('group__name', flat=True))
    if overdue:
        groups = sorted(set(overdue))
        warnings.append(
            f'прошедших незаполненных занятий {len(overdue)} в {len(groups)} группах ({", ".join(groups)}) — '
            'блокер порядка заполнения KOTOKOD не даст отметить следующие уроки этих групп, '
            'пока прошедшие не закроют (проведено/отменено)')
