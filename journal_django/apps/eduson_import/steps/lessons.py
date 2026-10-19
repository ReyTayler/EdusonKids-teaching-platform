"""Шаг «уроки»: sessions → Lesson + LessonAttendance (проведённые) + PlannedLesson
(все, кроме фиктивных); lesson_reports → LessonFeedback (import_feedback).

ЧТОБЫ ИМПОРТ БЫЛ НЕОТЛИЧИМ ОТ KOTOKOD. План и факты пишутся в той форме, в какой
их оставляет сам KOTOKOD, иначе первая же операция планировщика или проверка
здоровья плана (apps.scheduling.health) примет импорт за поломку:
  * нумерация — как generate_for_group для группы-продолжения: seq — 1-based
    позиция в ЭТОМ плане, lesson_number = Group.lesson_number_offset (N) +
    нарастающий вес (apps.lessons.weights.lesson_weight, 45 мин = 0.5). Номер
    Eduson не берётся (в Eduson номера в группе повторяются, спека §5.1);
  * проведённая позиция стоит на дате факта, её lesson_number = номеру факта,
    moved_from_date = NULL — так позицию оставляют relink_fact / link_facts /
    sync_position_date при записи урока (метка переноса описывает плановое
    движение и гасится фактом). Перенос проведённого урока виден по
    lesson_type='reschedule';
  * отмена — маркер status=cancelled с seq/lesson_number NULL (как cancel_lesson
    KOTOKOD и CHECK planned_lessons_seq_number_together_check); номера курса она
    не занимает;
  * замена — Lesson.teacher = кто провёл, original_teacher = преподаватель группы,
    lesson_type='substitution'; в плане teacher = преподаватель группы,
    substitute_teacher = кто провёл (замена — свойство даты, не контента). Замена
    важнее переноса — как в submit_lesson KOTOKOD, где тип выводится сервером с тем
    же приоритетом;
  * submission_key = NULL — исторический факт (ключ есть только у отправок из
    интерфейса), submitted_by_token = SUBMITTED_BY.

КТО ПРОВЁЛ (спека §5.4). owner — преподаватель группы на дату урока по истории
журнала Eduson (ImportContext.teacher_timeline), а не текущий: у 50 групп он менялся.
sessions.teacher_id — отметка генерации расписания, замену она не доказывает;
доказывает только действие замены над уроком (ImportContext.substituted). Поэтому:
  held:   выставлен заменой → он; иначе owner; иначе отметка урока; иначе текущий
          преподаватель группы + ручная проверка. Отметка ≠ owner без замены →
          owner + ручная проверка (не молча: вдруг это всё же была замена);
  план:   teacher = owner (нет — текущий), substitute_teacher = выставленный
          заменой, если он ≠ owner.
substitution/original_teacher — только если провёл не owner.

ЗАРПЛАТА за импортированные уроки не начисляется (решение 2026-10-18, спека §5.4):
ставки и правила оплаты KOTOKOD — не Eduson; правила Eduson — этап 3.

HELD С ОШИБОЧНЫМ ГОДОМ (ImportContext.year_fixed; решение владельца 2026-10-19).
Held «2027-06-DD» — настоящие уроки июня 2026, отмеченные в таблицах Eduson на
вкладке «Июнь 2027». Они идут ровно как настоящие held, только с датой годом
раньше: время — своё у сессии, иначе слот группы (как у всех уроков без времени);
посещаемость — «был» всем ученикам группы; кто провёл — то же правило §5.4 (owner на
исправленную дату по истории; дата раньше первой записи истории даёт первого
известного преподавателя группы — TeacherTimeline начинается «с начала времён»);
зарплата не начисляется. Номер — по дате, вперемешку с настоящими. Источник виден
по submitted_by_token = SUBMITTED_BY_YEAR_FIXED (поля комментария у Lesson нет).
Два проведённых урока группы в один день модель допускает (уникальность урока —
дата + номер + токен, у плана — seq), поэтому совпавшая дата не сдвигается, а только
отмечается в отчёте; health покажет её как duplicate_dates (предупреждение).

ФИКТИВНЫЕ held (ImportContext.fake_held: дата позже дня импорта, год не исправить) —
ни уроком, ни позицией: они уже учтены в N группы. Отзывов на них по данным нет;
если встретится — на ручную проверку.

ПРОИЗВОДИТЕЛЬНОСТЬ. ~5 000 сессий: всё собирается в памяти и пишется тремя
bulk_create (уроки — с возвратом id, PostgreSQL его поддерживает), запросов в
цикле по сессиям нет. pghistory-триггеры срабатывают и на bulk_create — журнал
изменений увидит импорт.
"""
from __future__ import annotations

import datetime
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
from zoneinfo import ZoneInfo

from apps.core.utils.dates import msk_now
from apps.eduson_import.analysis import parse_moment
from apps.eduson_import.context import ImportContext, ImportDataError
from apps.eduson_import.sqlite_source import EdusonSource
from apps.eduson_import.steps import require_atomic
from apps.feedback.models import LessonFeedback
from apps.groups.models import GroupScheduleSlot
from apps.lessons.models import Lesson, LessonAttendance
from apps.lessons.weights import lesson_weight
from apps.scheduling.models import PlannedLesson
from apps.scheduling.occurrences import CANCELLED, DONE, PENDING

# Метка источника в lessons.submitted_by_token — по образцу служебных меток KOTOKOD
# ('admin-imported', 'manual-override'): по ней импортированные факты отличимы от
# записанных людьми, и natural key урока не пересекается с их отправками.
SUBMITTED_BY = 'eduson-import'
# Урок из held с ошибочным годом: перенесён из таблицы Eduson (вкладка «Июнь 2027»,
# год исправлен). Отдельная метка — чтобы такие уроки можно было найти и пересмотреть.
SUBMITTED_BY_YEAR_FIXED = 'eduson-import:year-fixed'

MSK = ZoneInfo('Europe/Moscow')
HELD, SCHEDULED, CANCELLED_EDUSON = 'held', 'scheduled', 'cancelled'
_STATUSES = frozenset({HELD, SCHEDULED, CANCELLED_EDUSON})
# Типы legacy (services/schedule.js): moved — перенесённый, makeup — отработка 45 мин.
_TYPES = frozenset({'regular', 'moved', 'makeup'})
_TIME_RE = re.compile(r'^(\d{1,2}):(\d{2})(?::\d{2})?$')
_MIDNIGHT = datetime.time(0, 0)
_SCORE_RANGE = range(1, 11)


def _text(value) -> str:
    return str(value or '').strip()


def _time(value, session_id: int) -> datetime.time | None:
    """Время урока; пустое — None (у held первичной загрузки времени нет)."""
    raw = _text(value)
    if not raw:
        return None
    m = _TIME_RE.fullmatch(raw)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ImportDataError(f'урок #{session_id}: время {raw!r} не в формате ЧЧ:ММ')
    return datetime.time(int(m.group(1)), int(m.group(2)))


def _js_dow(day: datetime.date) -> int:
    """День недели как в слотах KOTOKOD и Eduson: 0=Вс … 6=Сб."""
    return (day.weekday() + 1) % 7


@dataclass
class _Session:
    row: dict
    date: datetime.date
    time: datetime.time | None


@dataclass
class _GroupInfo:
    """Что шагу нужно знать о группе: код (для отчёта), id в KOTOKOD, текущий
    преподаватель, ученики, слоты."""
    code: str
    group_id: int
    current_teacher: int
    students: list[int]
    slot_times: dict[int, datetime.time]      # день недели → первое время слота
    first_slot_time: datetime.time | None


# ── уроки и план ────────────────────────────────────────────────────────────

def import_lessons(src: EdusonSource, ctx: ImportContext) -> None:
    require_atomic('уроки')
    groups = _group_info(src, ctx)
    by_group: dict[int, list[_Session]] = defaultdict(list)
    for s in src.rows('sessions'):
        if s['group_id'] not in groups:
            raise ImportDataError(f'урок #{s["id"]}: группа #{s["group_id"]} не найдена')
        if s['status'] not in _STATUSES:
            raise ImportDataError(f'урок #{s["id"]}: неизвестный статус {s["status"]!r}')
        if s['type'] not in _TYPES:
            raise ImportDataError(f'урок #{s["id"]}: неизвестный тип {s["type"]!r}')
        if not isinstance(s['duration_min'], int) or s['duration_min'] <= 0:
            raise ImportDataError(f'урок #{s["id"]}: длительность {s["duration_min"]!r} не число минут')
        try:
            day = datetime.date.fromisoformat(_text(s['date']))
        except ValueError:
            raise ImportDataError(f'урок #{s["id"]}: дата не в формате ГГГГ-ММ-ДД') from None
        day = ctx.year_fixed.get(s['id'], day)
        by_group[s['group_id']].append(_Session(s, day, _time(s['time'], s['id'])))

    now = msk_now()
    lessons: list[tuple[int, int, Lesson]] = []                  # (sessions.id, group_id Eduson, факт)
    positions: list[tuple[int, PlannedLesson, int | None]] = []  # (sessions.id, позиция, sessions.id факта)
    # Порядок групп и сессий детерминирован — id в KOTOKOD от запуска к запуску одни и те же.
    for gid in sorted(by_group):
        _plan_group(gid, by_group[gid], groups[gid], ctx, now, lessons, positions)
    ctx.report.append('зарплата за импортированные уроки не начисляется (спека §5.4)')
    if ctx.year_fixed:
        ctx.report.append(f'проведённых с датой следующего года (таблицы Eduson, вкладка «Июнь 2027») — '
                          f'{len(ctx.year_fixed)}: перенесены уроками с годом −1')
    if ctx.fake_held:
        ctx.report.append(f'фиктивных проведённых, чей год не исправить (дата −1 год всё ещё позже дня '
                          f'импорта) — {len(ctx.fake_held)}: учтены в пройденном до учёта')

    created = Lesson.objects.bulk_create([lesson for _, _, lesson in lessons], batch_size=1000)
    for (sid, _, _), lesson in zip(lessons, created):
        ctx.lesson_by_session[sid] = lesson.id

    LessonAttendance.objects.bulk_create([
        LessonAttendance(lesson_id=lesson.id, student_id=student_id, present=True)
        for (_, gid, _), lesson in zip(lessons, created)
        for student_id in groups[gid].students
    ], batch_size=1000)

    for _, position, fact_sid in positions:
        if fact_sid is not None:
            position.fact_lesson_id = ctx.lesson_by_session[fact_sid]
    created_positions = PlannedLesson.objects.bulk_create([p for _, p, _ in positions], batch_size=1000)
    for (sid, _, _), position in zip(positions, created_positions):
        ctx.planned_by_session[sid] = position.id


def _group_info(src: EdusonSource, ctx: ImportContext) -> dict[int, _GroupInfo]:
    """Группы шага одним проходом: ученики из participants (без пустых строк и без
    повторов склеенного ученика), слоты — одним запросом к уже созданным слотам."""
    students: dict[int, dict[int, None]] = defaultdict(dict)
    skipped = set(ctx.skipped_participants)
    for p in src.rows('participants'):
        if p['id'] not in skipped:
            students[p['group_id']][ctx.student_by_participant[p['id']]] = None

    slots: dict[int, list[tuple[int, datetime.time]]] = defaultdict(list)
    for group_id, day, start in (GroupScheduleSlot.objects
                                 .filter(group_id__in=ctx.group_by_eduson.values())
                                 .order_by('group_id', 'start_time', 'day_of_week')
                                 .values_list('group_id', 'day_of_week', 'start_time')):
        slots[group_id].append((day, start))

    out: dict[int, _GroupInfo] = {}
    for g in src.rows('student_groups'):
        group_id = ctx.group_by_eduson[g['id']]
        by_day: dict[int, datetime.time] = {}
        for day, start in slots[group_id]:
            by_day.setdefault(day, start)                  # самое раннее время дня
        ordered = sorted(slots[group_id], key=lambda s: (s[0], s[1]))
        out[g['id']] = _GroupInfo(
            code=_text(g['code']), group_id=group_id,
            current_teacher=ctx.teacher_by_eduson[g['teacher_id']],
            students=list(students.get(g['id'], {})),
            slot_times=by_day, first_slot_time=ordered[0][1] if ordered else None,
        )
    return out


def _plan_group(gid: int, sessions: list[_Session], info: _GroupInfo, ctx: ImportContext, now,
                lessons: list, positions: list) -> None:
    """Сессии одной группы → факты и позиции в хронологии (дата, время, id)."""
    sessions.sort(key=lambda s: (s.date, s.time or _MIDNIGHT, s.row['id']))
    number = ctx.pre_system.get(gid, Decimal(0))
    seq = 0
    counts: Counter[str] = Counter()
    held_days: Counter[datetime.date] = Counter()     # дата → проведённых в этот день
    fixed_days: list[datetime.date] = []

    for s in sessions:
        row, sid = s.row, s.row['id']
        if sid in ctx.fake_held:
            counts['fake'] += 1
            continue
        when = s.time
        if when is None:
            counts['no_time'] += 1
            when = info.slot_times.get(_js_dow(s.date)) or info.first_slot_time or _MIDNIGHT

        owner = _owner_on(gid, s, info, ctx)                 # преподаватель группы на дату
        substitute = _teacher(ctx.substituted.get(sid), sid, ctx)   # выставлен действием замены
        plan_teacher = owner if owner is not None else info.current_teacher
        plan_substitute = substitute if substitute is not None and substitute != plan_teacher else None

        if row['status'] == CANCELLED_EDUSON:
            positions.append((sid, PlannedLesson(
                group_id=info.group_id, seq=None, lesson_number=None,
                scheduled_date=s.date, scheduled_time=when,
                teacher_id=plan_teacher, substitute_teacher_id=plan_substitute,
                status=CANCELLED, created_at=now, updated_at=now,
            ), None))
            continue

        seq += 1
        number += lesson_weight(row['duration_min'])

        if row['status'] == SCHEDULED:
            if s.date < ctx.as_of:
                counts['past_pending'] += 1
            positions.append((sid, PlannedLesson(
                group_id=info.group_id, seq=seq, lesson_number=number,
                scheduled_date=s.date, scheduled_time=when,
                teacher_id=plan_teacher, substitute_teacher_id=plan_substitute,
                status=PENDING, moved_from_date=ctx.moved_from.get(sid),
                created_at=now, updated_at=now,
            ), None))
            continue

        # held — настоящий проведённый урок.
        teacher = _held_teacher(s, info, owner, substitute, ctx)
        substitution = owner is not None and teacher != owner
        if substitution:
            lesson_type = 'substitution'
        elif row['type'] == 'moved':
            lesson_type = 'reschedule'
        else:
            lesson_type = 'regular'
        lessons.append((sid, gid, Lesson(
            group_id=info.group_id, teacher_id=teacher,
            original_teacher_id=owner if substitution else None,
            lesson_date=s.date, lesson_number=number,
            lesson_duration_minutes=row['duration_min'], lesson_type=lesson_type,
            record_url=_text(row['recording_url']) or None,
            # Момент перехода в held; updated_at не годится — legacy двигает его и
            # ссылкой на запись урока, и любой правкой. Без него — не раньше самого
            # урока: сессии генерируются заранее, и голый created_at давал бы
            # «урок записан» до того, как он прошёл.
            submitted_at=ctx.held_at.get(sid) or max(
                parse_moment(row['created_at']) or datetime.datetime.min.replace(tzinfo=MSK),
                datetime.datetime.combine(s.date, when, tzinfo=MSK)),
            submitted_by_token=SUBMITTED_BY_YEAR_FIXED if sid in ctx.year_fixed else SUBMITTED_BY,
        )))
        positions.append((sid, PlannedLesson(
            group_id=info.group_id, seq=seq, lesson_number=number,
            scheduled_date=s.date, scheduled_time=when,
            teacher_id=owner if owner is not None else teacher,
            substitute_teacher_id=teacher if substitution else None,
            status=DONE, created_at=now, updated_at=now,
        ), sid))
        counts['held'] += 1
        held_days[s.date] += 1
        if sid in ctx.year_fixed:
            fixed_days.append(s.date)

    counts['year_fixed'] = len(fixed_days)
    counts['year_fixed_same_day'] = sum(1 for d in fixed_days if held_days[d] > 1)
    _report_group(info, counts, ctx)


def _held_teacher(s: _Session, info: _GroupInfo, owner: int | None, substitute: int | None,
                  ctx: ImportContext) -> int:
    """Кто провёл проведённый урок — правило §5.4 (см. шапку модуля)."""
    sid = s.row['id']
    if substitute is not None:
        return substitute
    mark = _teacher(s.row['teacher_id'], sid, ctx)            # отметка генерации расписания
    if owner is not None:
        if mark is not None and mark != owner:
            ctx.manual_review.append(
                f'группа {info.code}, урок #{sid}: у урока отмечен другой преподаватель, но замены '
                'в журнале Eduson нет — урок записан на преподавателя группы, проверьте')
        return owner
    if mark is not None:
        return mark
    ctx.manual_review.append(
        f'группа {info.code}, урок #{sid}: преподаватель группы на дату урока не известен '
        'и у урока его нет — поставлен текущий преподаватель группы, проверьте')
    return info.current_teacher


def _owner_on(gid: int, s: _Session, info: _GroupInfo, ctx: ImportContext) -> int | None:
    """Teacher.id преподавателя группы на момент урока (None — в тот период у группы
    его не было). Для будущих уроков история даёт текущего."""
    eduson_id = ctx.teacher_timeline.group_teacher_on(gid, s.date, s.time)
    return None if eduson_id is None else _teacher(eduson_id, s.row['id'], ctx)


def _teacher(eduson_id, session_id: int, ctx: ImportContext) -> int | None:
    if eduson_id is None:
        return None
    try:
        return ctx.teacher_by_eduson[eduson_id]
    except KeyError:
        # Шаг people заводит Teacher всем, на кого ссылаются уроки и история групп.
        raise ImportDataError(f'урок #{session_id}: преподаватель #{eduson_id} не перенесён') from None


def _report_group(info: _GroupInfo, counts: Counter, ctx: ImportContext) -> None:
    code = info.code
    if counts['fake']:
        ctx.report.append(f'группа {code}: фиктивных проведённых (дата позже дня импорта) — '
                          f'{counts["fake"]}, учтены в пройденном до учёта, уроками не созданы')
    if counts['year_fixed']:
        ctx.report.append(f'группа {code}: перенесено из таблицы Eduson (вкладка «Июнь 2027», год исправлен) '
                          f'— {counts["year_fixed"]} проведённых')
    if counts['year_fixed_same_day']:
        ctx.report.append(f'группа {code}: из них {counts["year_fixed_same_day"]} пришлись на дату, где есть '
                          'другой проведённый урок — оставлены два урока в один день')
    if counts['past_pending']:
        ctx.report.append(f'группа {code}: прошлых незаполненных занятий — {counts["past_pending"]} '
                          '(в KOTOKOD — просроченные, блокируют отметку следующих)')
    if counts['no_time']:
        ctx.report.append(f'группа {code}: уроков без времени — {counts["no_time"]}, '
                          'время взято из расписания группы (нет слота — 00:00)')
    if counts['held'] and not info.students:
        ctx.report.append(f'группа {code}: без учеников — {counts["held"]} проведённых уроков '
                          'перенесены без посещаемости')


# ── отзывы ──────────────────────────────────────────────────────────────────

def import_feedback(src: EdusonSource, ctx: ImportContext) -> None:
    """lesson_reports (submitted) → LessonFeedback. Вызывать после import_lessons.

    Привязка (спека §5.4, модель — apps.feedback): group и lesson_date — с урока
    отчёта, lesson — факт, если урок проведён; иначе NULL. Урок удалён — дата и
    группа из снимка в журнале Eduson (ImportContext.deleted_sessions). Нет и там,
    или отзыв на фиктивную held (её дата — заглушка) — на ручную проверку без
    переноса: придумать дату значило бы положить отзыв не на тот урок.
    """
    require_atomic('отзывы')
    sessions = {s['id']: s for s in src.rows('sessions')}
    pending = 0
    items: list[LessonFeedback] = []
    for r in src.rows('lesson_reports'):
        if r['status'] != 'submitted':
            pending += 1            # заготовки напоминаний: преподаватель ничего не отправлял
            continue
        rid, sid = r['id'], r['session_id']
        place = _feedback_place(r, sessions, ctx)
        if place is None:
            ctx.feedback_not_moved.append(rid)       # сверка: submitted = перенесено + на проверку
            continue
        group_eduson, lesson_date = place
        if r['teacher_id'] not in ctx.teacher_by_eduson:
            raise ImportDataError(f'отзыв #{rid}: преподаватель отзыва не перенесён (в KOTOKOD он обязателен)')
        took_place = _took_place(r, sid, ctx)
        items.append(LessonFeedback(
            group_id=ctx.group_by_eduson[group_eduson], lesson_date=lesson_date,
            lesson_id=ctx.lesson_by_session.get(sid),
            teacher_id=ctx.teacher_by_eduson[r['teacher_id']], took_place=took_place,
            understanding=_score(r, 'understanding', ctx), interest=_score(r, 'interest', ctx),
            tech_problems=r['tech_problems'] == 'yes', comment=_text(r['comment']),
            submitted_at=(parse_moment(r['submitted_at']) or parse_moment(r['created_at'])
                          or datetime.datetime.combine(lesson_date, _MIDNIGHT, tzinfo=MSK)),
        ))
    LessonFeedback.objects.bulk_create(items, batch_size=1000)
    if pending:
        ctx.report.append(f'отзывы: не отправленных преподавателем заготовок — {pending}, не перенесены')


def _feedback_place(r: dict, sessions: dict[int, dict], ctx: ImportContext) -> tuple[int, datetime.date] | None:
    """(group_id Eduson, дата урока) отзыва или None — отзыв ушёл на ручную проверку."""
    rid, sid = r['id'], r['session_id']
    if sid in sessions:
        if sid in ctx.fake_held:
            ctx.manual_review.append(f'отзыв #{rid} на урок #{sid}: урок с датой-заглушкой первичной загрузки '
                                     '(фиктивный проведённый) — дата урока неизвестна, отзыв не перенесён')
            return None
        s = sessions[sid]
        return s['group_id'], ctx.year_fixed.get(sid) or datetime.date.fromisoformat(_text(s['date']))
    if sid in ctx.deleted_sessions:
        # Дата — из снимка удалённого урока; группа — из отчёта (спека §5.4), а если
        # её среди перенесённых нет — из того же снимка.
        snapshot_group, day = ctx.deleted_sessions[sid]
        for group_eduson in (r['group_id'], snapshot_group):
            if group_eduson in ctx.group_by_eduson:
                return group_eduson, day
        ctx.manual_review.append(f'отзыв #{rid} на удалённый урок #{sid}: группа удалена — '
                                 'отзыв не перенесён')
        return None
    ctx.manual_review.append(f'отзыв #{rid} на урок #{sid}: урока нет ни в базе, ни в журнале Eduson — '
                             'отзыв не перенесён')
    return None


def _took_place(r: dict, sid, ctx: ImportContext) -> bool:
    if r['took_place'] in ('yes', 'no'):
        return r['took_place'] == 'yes'
    # Поле обязательное; без ответа — выводим из урока и просим проверить.
    guess = sid in ctx.lesson_by_session
    ctx.manual_review.append(f'отзыв #{r["id"]}: не указано, состоялся ли урок — '
                             f'записано «{"да" if guess else "нет"}» по статусу урока')
    return guess


def _score(r: dict, field: str, ctx: ImportContext) -> int | None:
    value = r[field]
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool) and value in _SCORE_RANGE:
        return value
    ctx.manual_review.append(f'отзыв #{r["id"]}: оценка «{field}» = {value!r} вне шкалы 1–10 — не перенесена')
    return None
